"""
Main orchestrator for the job scraping pipeline.
Ties together: Apify -> Gemini -> SQLite -> Dashboard
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, or_

from .apify_scraper import ApifyScraper
from .gemini_scorer import GeminiScorer
from .models import init_db, Job, SearchQuery, Company
from .resume_loader import load_resume_bundle

load_dotenv()


# Default search queries (Step 11 - Tier 1 high-intent)
DEFAULT_SEARCHES = [
    {
        "query_name": "APM Remote",
        "keywords": "Associate Product Manager",
        "location": "Remote",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
    {
        "query_name": "PM India",
        "keywords": "Product Manager",
        "location": "India",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
    {
        "query_name": "AI PM India",
        "keywords": "AI Product Manager",
        "location": "India",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
    {
        "query_name": "Product Analyst Bangalore",
        "keywords": "Product Analyst",
        "location": "Bangalore",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
    {
        "query_name": "Technical PM Remote",
        "keywords": "Technical Product Manager",
        "location": "Remote",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
]


class JobSearchOrchestrator:
    """
    Main orchestrator for the job search pipeline.

    Workflow:
    1. Load resume sources
    2. Run Apify searches
    3. Deduplicate jobs
    4. Score with Gemini
    5. Save to database
    6. Generate reports
    """

    def __init__(
        self,
        resume_paths: list[str] | None = None,
        database_url: str = "sqlite:///job_tracker.db",
        gemini_model: str = "gemini-3-flash-preview",
    ):
        self.resume_bundle = load_resume_bundle(resume_paths)
        self.resume_text = self.resume_bundle["resume_text"]
        self.resume_sources = self.resume_bundle["source_paths"]
        self.database_url = database_url
        self.gemini_model = gemini_model

        # Initialize components
        self.apify = ApifyScraper()
        self.scorer = GeminiScorer(model=gemini_model)

        # Initialize database
        self.db = init_db(database_url)

    def _deduplicate_key(self, job: dict) -> str:
        """Create a deduplication key for a job."""
        title = str(job.get("title", "")).lower().strip()
        company = str(job.get("company", "")).lower().strip()
        location = str(job.get("location", "")).lower().strip()
        return f"{title}|{company}|{location}"

    def _normalize_job(self, raw_job: dict, source: str) -> dict:
        """Normalize job data from various Apify actors."""
        # Handle different field names from different actors
        return {
            "title": raw_job.get("title") or raw_job.get("job_title") or "Unknown",
            "company": raw_job.get("company")
            or raw_job.get("company_name")
            or raw_job.get("employer")
            or "Unknown",
            "location": raw_job.get("location")
            or raw_job.get("job_location")
            or raw_job.get("search_location")
            or "",
            "remote": bool(raw_job.get("is_remote"))
            or "remote" in str(raw_job.get("location", "")).lower(),
            "posted_date": raw_job.get("posted_date")
            or raw_job.get("date_posted")
            or raw_job.get("posted_at")
            or "Unknown",
            "salary": raw_job.get("salary") or raw_job.get("compensation") or "",
            "url": raw_job.get("url")
            or raw_job.get("job_url")
            or raw_job.get("official_url")
            or raw_job.get("platform_url")
            or "",
            "jd_raw": raw_job.get("description")
            or raw_job.get("job_description")
            or raw_job.get("jd")
            or raw_job.get("summary")
            or "",
            "source": source,
        }

    def _normalize_url(self, url: str) -> str:
        """Normalize URLs for cross-run deduplication."""
        return str(url or "").strip().rstrip("/")

    def _find_existing_job(self, job_data: dict) -> Job | None:
        """Find an existing tracked job using URL first, then title/company/location."""
        normalized_url = self._normalize_url(job_data.get("url", ""))
        if normalized_url:
            existing = (
                self.db.query(Job)
                .filter(Job.url == normalized_url)
                .order_by(Job.id.asc())
                .first()
            )
            if existing:
                return existing

        dedupe_key = self._deduplicate_key(job_data)
        existing = (
            self.db.query(Job)
            .filter(Job.dedupe_key == dedupe_key)
            .order_by(Job.id.asc())
            .first()
        )
        if existing:
            return existing

        return (
            self.db.query(Job)
            .filter(
                func.lower(Job.title) == str(job_data.get("title", "")).lower().strip(),
                func.lower(Job.company) == str(job_data.get("company", "")).lower().strip(),
                func.lower(Job.location) == str(job_data.get("location", "")).lower().strip(),
            )
            .order_by(Job.id.asc())
            .first()
        )

    def _refresh_existing_job(
        self,
        existing: Job,
        job_data: dict,
        search_query: str,
    ) -> None:
        """Update a previously tracked job with the latest scrape metadata."""
        now = datetime.utcnow()
        existing.last_seen_at = now
        existing.seen_count = (existing.seen_count or 0) + 1
        existing.last_search_query = search_query
        existing.dedupe_key = self._deduplicate_key(job_data)
        existing.source = job_data.get("source", existing.source)
        existing.posted_date = job_data.get("posted_date", existing.posted_date)
        existing.salary = job_data.get("salary", existing.salary)
        existing.remote = job_data.get("remote", existing.remote)

        normalized_url = self._normalize_url(job_data.get("url", ""))
        if normalized_url:
            existing.url = normalized_url

        if job_data.get("jd_raw"):
            existing.jd_raw = job_data["jd_raw"]

    def _build_feedback_context(self, limit: int = 12) -> str:
        """Build a short memory block from past human feedback."""
        reviewed_jobs = (
            self.db.query(Job)
            .filter(
                or_(
                    Job.human_feedback.isnot(None),
                    Job.human_verdict.isnot(None),
                    Job.human_score.isnot(None),
                )
            )
            .order_by(Job.feedback_updated_at.desc(), Job.updated_at.desc())
            .limit(limit)
            .all()
        )

        if not reviewed_jobs:
            return ""

        lines = []
        for job in reviewed_jobs:
            parts = [f"{job.title} @ {job.company}"]
            if job.fit_score is not None:
                parts.append(f"AI score {job.fit_score}")
            if job.human_score is not None:
                parts.append(f"human score {job.human_score}")
            if job.human_verdict:
                parts.append(f"verdict {job.human_verdict}")
            summary = " | ".join(parts)
            feedback = (job.human_feedback or job.notes or "").strip()
            if feedback:
                summary += f" | feedback: {feedback}"
            lines.append(f"- {summary}")

        return "\n".join(lines)

    def prepare_jobs_for_scoring(self, jobs: list[dict]) -> dict:
        """Split scraped jobs into new jobs to score vs. previously tracked jobs."""
        now = datetime.utcnow()
        jobs_to_score = []
        existing_count = 0
        already_scored_count = 0

        for job_data in jobs:
            job_copy = dict(job_data)
            job_copy["url"] = self._normalize_url(job_copy.get("url", ""))
            job_copy["dedupe_key"] = self._deduplicate_key(job_copy)

            existing = self._find_existing_job(job_copy)
            if existing:
                existing_count += 1
                self._refresh_existing_job(
                    existing,
                    job_copy,
                    search_query=job_copy.get("source", ""),
                )
                if existing.fit_score is None:
                    job_copy["_existing_job_id"] = existing.id
                    jobs_to_score.append(job_copy)
                else:
                    already_scored_count += 1
                continue

            job_copy["first_seen_at"] = now
            job_copy["last_seen_at"] = now
            job_copy["seen_count"] = 1
            jobs_to_score.append(job_copy)

        self.db.commit()

        return {
            "jobs_to_score": jobs_to_score,
            "existing_jobs": existing_count,
            "already_scored_jobs": already_scored_count,
            "new_jobs": len([job for job in jobs_to_score if "_existing_job_id" not in job]),
        }

    async def run_searches(
        self, searches: list[dict] | None = None, resume_text: str | None = None
    ) -> dict:
        """
        Run all configured searches and return results.

        Args:
            searches: List of search configurations (uses DEFAULT_SEARCHES if None)
            resume_text: Optional resume text for AI Job Finder

        Returns:
            Dictionary with search results and stats
        """
        searches = searches or DEFAULT_SEARCHES
        resume_text = resume_text or self.resume_text
        all_jobs = []
        search_stats = []

        for search_config in searches:
            try:
                jobs = await self.apify.search_jobs(
                    query_name=search_config["query_name"],
                    keywords=search_config["keywords"],
                    location=search_config.get("location", ""),
                    actor=search_config.get("actor", "all_jobs"),
                    date_posted=search_config.get("date_posted", "7 days"),
                    limit=search_config.get("limit", 100),
                    cv_text=resume_text,
                )

                # Normalize and deduplicate
                seen_keys = set()
                unique_jobs = []

                for job in jobs:
                    normalized = self._normalize_job(
                        job, search_config["query_name"]
                    )
                    key = self._deduplicate_key(normalized)

                    if key not in seen_keys:
                        seen_keys.add(key)
                        unique_jobs.append(normalized)

                all_jobs.extend(unique_jobs)

                search_stats.append(
                    {
                        "query_name": search_config["query_name"],
                        "jobs_found": len(unique_jobs),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )

                # Save search query to DB
                sq = SearchQuery(
                    query_name=search_config["query_name"],
                    keywords=search_config["keywords"],
                    location=search_config.get("location", ""),
                    last_run=datetime.utcnow(),
                    jobs_found=len(unique_jobs),
                )
                self.db.add(sq)
                self.db.commit()

            except Exception as e:
                print(f"[ERROR] Error in search {search_config['query_name']}: {e}")
                search_stats.append(
                    {
                        "query_name": search_config["query_name"],
                        "jobs_found": 0,
                        "error": str(e),
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                )

        print(f"\n[INFO] Total unique jobs found: {len(all_jobs)}")

        return {
            "jobs": all_jobs,
            "search_stats": search_stats,
            "total_jobs": len(all_jobs),
        }

    async def score_jobs(self, jobs: list[dict], batch_size: int = 10) -> list[dict]:
        """
        Score all jobs using Gemini API.

        Args:
            jobs: List of normalized job dicts
            batch_size: Number of jobs to process in parallel

        Returns:
            List of jobs with scoring results attached
        """
        print(f"\n[INFO] Scoring {len(jobs)} jobs with Gemini...")

        scored_jobs = []

        # Process in batches to avoid rate limits
        for i in range(0, len(jobs), batch_size):
            batch = jobs[i : i + batch_size]
            print(f"   Processing batch {i // batch_size + 1}...")

            scores = await self.scorer.score_batch(self.resume_text, batch)

            for job, score in zip(batch, scores):
                job["score"] = score
                scored_jobs.append(job)

            # Small delay between batches
            if i + batch_size < len(jobs):
                await asyncio.sleep(1)

        # Sort by fit score descending
        scored_jobs.sort(key=lambda x: x.get("score", {}).get("fit_score", 0), reverse=True)

        print(f"   [OK] Scoring complete")
        print(f"   Top score: {scored_jobs[0].get('score', {}).get('fit_score', 0) if scored_jobs else 0}")
        print(f"   Average score: {sum(j.get('score', {}).get('fit_score', 0) for j in scored_jobs) / len(scored_jobs):.1f}" if scored_jobs else "   No jobs scored")

        return scored_jobs

    def save_to_db(self, scored_jobs: list[dict]) -> int:
        """
        Save scored jobs to database.

        Returns:
            Number of jobs saved
        """
        saved = 0

        for job_data in scored_jobs:
            score = job_data.get("score", {})

            job = Job(
                title=job_data.get("title", ""),
                company=job_data.get("company", ""),
                location=job_data.get("location", ""),
                remote=job_data.get("remote", False),
                posted_date=job_data.get("posted_date", ""),
                salary=job_data.get("salary", ""),
                url=job_data.get("url", ""),
                jd_raw=job_data.get("jd_raw", ""),
                source=job_data.get("source", ""),
                fit_score=score.get("fit_score", 0),
                interview_chance=score.get("interview_chance", "Medium"),
                apply_priority=score.get("apply_priority", "P3"),
                why_match=score.get("why_match", ""),
                biggest_gap=score.get("biggest_gap", ""),
                resume_tweaks=json.dumps(score.get("resume_tweaks", [])),
                why_company_angle=score.get("why_company_angle", ""),
                should_apply=score.get("should_apply", True),
                status="new",
            )

            self.db.add(job)
            saved += 1

        self.db.commit()
        print(f"\n[OK] Saved {saved} jobs to database")
        return saved

    def generate_report(self, scored_jobs: list[dict], output_path: str = "ranked_jobs.md") -> str:
        """
        Generate a markdown report of ranked jobs.

        Returns:
            Path to generated report
        """
        if not scored_jobs:
            return ""

        # Get top jobs by priority
        p1_jobs = [j for j in scored_jobs if j.get("score", {}).get("apply_priority") == "P1"]
        p2_jobs = [j for j in scored_jobs if j.get("score", {}).get("apply_priority") == "P2"]
        stretch_jobs = [j for j in scored_jobs if 55 <= j.get("score", {}).get("fit_score", 0) < 70]
        skip_jobs = [j for j in scored_jobs if j.get("score", {}).get("fit_score", 0) < 55]

        report = f"""# Job Search Report

Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}

## Summary

- **Total Jobs Analyzed:** {len(scored_jobs)}
- **P1 (Apply Immediately):** {len(p1_jobs)}
- **P2 (Strong Apply):** {len(p2_jobs)}
- **Stretch:** {len(stretch_jobs)}
- **Skip:** {len(skip_jobs)}

---

## Top Priority Jobs (P1)

"""

        for i, job in enumerate(p1_jobs[:15], 1):
            score = job.get("score", {})
            report += f"""### {i}. {job.get('title')} @ {job.get('company')}

| Metric | Value |
|--------|-------|
| Fit Score | {score.get('fit_score', 0)}/100 |
| Interview Chance | {score.get('interview_chance', 'N/A')} |
| Location | {job.get('location', 'N/A')} |

**Why it matches:** {score.get('why_match', 'N/A')}

**Biggest gap:** {score.get('biggest_gap', 'N/A')}

**Resume tweaks:**
{chr(10).join('- ' + t for t in score.get('resume_tweaks', []))}

**Why this company:** {score.get('why_company_angle', 'N/A')}

[Apply Here]({job.get('url', '#')})

---

"""

        report += f"""## P2 Jobs (Strong Apply)

{chr(10).join(f"- {j.get('title')} @ {j.get('company')} (Score: {j.get('score', {}).get('fit_score', 0)})" for j in p2_jobs[:10])}

## Score Distribution

| Score Range | Count | Action |
|-------------|-------|--------|
| 85-100 | {len([j for j in scored_jobs if j.get('score', {}).get('fit_score', 0) >= 85])} | Apply immediately |
| 70-84 | {len([j for j in scored_jobs if 70 <= j.get('score', {}).get('fit_score', 0) < 85])} | Strong apply |
| 55-69 | {len([j for j in scored_jobs if 55 <= j.get('score', {}).get('fit_score', 0) < 70])} | Stretch if upside |
| <55 | {len([j for j in scored_jobs if j.get('score', {}).get('fit_score', 0) < 55])} | Skip |

---

*Report generated by Job Search Orchestrator*
"""

        output_file = Path(output_path)
        output_file.write_text(report)
        print(f"[OK] Report saved to: {output_path}")

        return output_path

    async def run_full_pipeline(
        self,
        searches: list[dict] | None = None,
        generate_report: bool = True,
        report_path: str = "ranked_jobs.md",
    ) -> dict:
        """
        Run the complete pipeline: search -> score -> save -> report.

        Returns:
            Pipeline results dictionary
        """
        print("[START] Job Search Pipeline\n")
        print("=" * 50)

        # Step 1: Search
        print("\n[STEP 1] Searching for jobs via Apify")
        print("=" * 50)
        search_results = await self.run_searches(searches, resume_text=self.resume_text)

        if not search_results["jobs"]:
            print("[ERROR] No jobs found. Check Apify API key and search parameters.")
            return {"success": False, "error": "No jobs found"}

        # Step 2: Score
        print("\n[STEP 2] Scoring jobs with Gemini")
        print("=" * 50)
        scored_jobs = await self.score_jobs(search_results["jobs"])

        # Step 3: Save to DB
        print("\n[STEP 3] Saving to database")
        print("=" * 50)
        self.save_to_db(scored_jobs)

        # Step 4: Generate report
        if generate_report:
            print("\n[STEP 4] Generating report")
            print("=" * 50)
            self.generate_report(scored_jobs, report_path)

        print("\n" + "=" * 50)
        print("[OK] Pipeline complete!")
        print("=" * 50)

        return {
            "success": True,
            "total_jobs": search_results["total_jobs"],
            "scored_jobs": len(scored_jobs),
            "top_score": scored_jobs[0].get("score", {}).get("fit_score", 0) if scored_jobs else 0,
            "p1_jobs": len([j for j in scored_jobs if j.get("score", {}).get("apply_priority") == "P1"]),
            "report_path": report_path if generate_report else None,
        }


async def main():
    """Entry point for running the pipeline."""
    orchestrator = JobSearchOrchestrator()

    result = await orchestrator.run_full_pipeline()

    if result["success"]:
        print(f"\n🎯 Found {result['p1_jobs']} P1 jobs to apply to!")
        print(f"📄 Check {result.get('report_path', 'ranked_jobs.md')} for details")


if __name__ == "__main__":
    asyncio.run(main())
