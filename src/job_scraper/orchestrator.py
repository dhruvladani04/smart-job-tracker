"""
Main orchestrator for the job scraping pipeline.
Ties together: Apify -> Gemini -> SQLite -> Dashboard
"""
import asyncio
import html
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
        "query_name": "Technical PM Remote",
        "keywords": "Technical Product Manager",
        "location": "Remote",
        "actor": "all_jobs",
        "date_posted": "7 days",
        "limit": 50,
    },
]

MAPPING_REGISTRY = {
    "generic": {
        "title": ["title", "job_title"],
        "company": ["company", "company_name", "employer"],
        "location": ["location", "job_location", "search_location"],
        "remote": ["is_remote"],
        "posted_date": ["posted_date", "date_posted", "posted_at"],
        "salary": ["salary", "compensation"],
        "url": ["url", "job_url", "official_url", "platform_url"],
        "jd_raw": ["description", "job_description", "jd", "summary"],
    },
    # Specific actor mappings can be added here (e.g., "linkedin_jobs": {...})
}


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
        """Normalize job data from various Apify actors using a mapping registry."""
        # Get mapping for the specific source or fall back to generic
        mapper = MAPPING_REGISTRY.get(source, MAPPING_REGISTRY["generic"])
        normalized = {}

        for field, keys in mapper.items():
            # Find the first key that exists and has a truthy value
            val = next((raw_job.get(k) for k in keys if raw_job.get(k)), None)

            # Default "Unknown" for title/company, empty string otherwise
            if val is None:
                normalized[field] = "Unknown" if field in ("title", "company") else ""
            else:
                normalized[field] = val

        # Special handling for 'remote' boolean logic
        # Check if explicitly marked remote OR if 'remote' is in the location string
        remote_val = normalized.get("remote")
        is_remote = bool(remote_val) or "remote" in str(raw_job.get("location", "")).lower()
        normalized["remote"] = is_remote

        normalized["source"] = source
        return normalized

    def _normalize_url(self, url: str) -> str:
        """Normalize URLs for cross-run deduplication."""
        return str(url or "").strip().rstrip("/")

    def _job_identity_key(self, job: Job) -> str:
        """Build a stable identity key for already-saved jobs."""
        normalized_url = self._normalize_url(job.url or "")
        if normalized_url:
            return f"url::{normalized_url}"
        if job.dedupe_key:
            return f"dedupe::{job.dedupe_key}"
        return "triple::{title}|{company}|{location}".format(
            title=str(job.title or "").lower().strip(),
            company=str(job.company or "").lower().strip(),
            location=str(job.location or "").lower().strip(),
        )

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

    def merge_duplicate_jobs(self) -> int:
        """Merge duplicate jobs already stored from older runs."""
        jobs = self.db.query(Job).order_by(Job.id.asc()).all()
        grouped: dict[str, list[Job]] = {}
        for job in jobs:
            grouped.setdefault(self._job_identity_key(job), []).append(job)

        status_rank = {
            "offer": 7,
            "interview": 6,
            "applied": 5,
            "reviewed": 4,
            "new": 3,
            "skipped": 2,
            "rejected": 1,
            "archived": 0,
        }

        merged = 0
        for duplicates in grouped.values():
            if len(duplicates) < 2:
                continue

            primary = duplicates[0]
            extras = duplicates[1:]

            for extra in extras:
                primary.first_seen_at = min(
                    [dt for dt in [primary.first_seen_at, extra.first_seen_at] if dt],
                    default=primary.first_seen_at or extra.first_seen_at,
                )
                primary.last_seen_at = max(
                    [dt for dt in [primary.last_seen_at, extra.last_seen_at] if dt],
                    default=primary.last_seen_at or extra.last_seen_at,
                )
                primary.seen_count = (primary.seen_count or 1) + (extra.seen_count or 1)

                if (extra.fit_score or -1) > (primary.fit_score or -1):
                    primary.fit_score = extra.fit_score
                    primary.interview_chance = extra.interview_chance
                    primary.apply_priority = extra.apply_priority
                    primary.why_match = extra.why_match or primary.why_match
                    primary.biggest_gap = extra.biggest_gap or primary.biggest_gap
                    primary.resume_tweaks = extra.resume_tweaks or primary.resume_tweaks
                    primary.why_company_angle = extra.why_company_angle or primary.why_company_angle
                    primary.should_apply = extra.should_apply
                    primary.ai_model = extra.ai_model or primary.ai_model

                if status_rank.get(extra.status or "new", -1) > status_rank.get(primary.status or "new", -1):
                    primary.status = extra.status

                if extra.applied_at and not primary.applied_at:
                    primary.applied_at = extra.applied_at
                if extra.human_verdict and not primary.human_verdict:
                    primary.human_verdict = extra.human_verdict
                if extra.human_score is not None and primary.human_score is None:
                    primary.human_score = extra.human_score
                if extra.human_feedback and not primary.human_feedback:
                    primary.human_feedback = extra.human_feedback
                if extra.feedback_updated_at and not primary.feedback_updated_at:
                    primary.feedback_updated_at = extra.feedback_updated_at
                if extra.notes and not primary.notes:
                    primary.notes = extra.notes
                if extra.jd_raw and not primary.jd_raw:
                    primary.jd_raw = extra.jd_raw
                if extra.salary and not primary.salary:
                    primary.salary = extra.salary
                if extra.source and not primary.source:
                    primary.source = extra.source
                if extra.last_search_query and not primary.last_search_query:
                    primary.last_search_query = extra.last_search_query
                if extra.url and not primary.url:
                    primary.url = extra.url

                self.db.delete(extra)
                merged += 1

        if merged:
            self.db.commit()

        return merged

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
                    job_copy["status"] = existing.status or "new"
                    jobs_to_score.append(job_copy)
                else:
                    already_scored_count += 1
                continue

            job_copy["first_seen_at"] = now
            job_copy["last_seen_at"] = now
            job_copy["seen_count"] = 1
            job_copy["status"] = "new"
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
        feedback_context = self._build_feedback_context()

        scored_jobs = []

        # Process in batches to avoid rate limits
        for i in range(0, len(jobs), batch_size):
            batch = jobs[i : i + batch_size]
            print(f"   Processing batch {i // batch_size + 1}...")

            scores = await self.scorer.score_batch(
                self.resume_text,
                batch,
                feedback_context=feedback_context,
            )

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
            now = datetime.utcnow()
            existing_job_id = job_data.get("_existing_job_id")
            if existing_job_id:
                job = self.db.query(Job).filter(Job.id == existing_job_id).first()
            else:
                job = None

            if not job:
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
                    status="new",
                    first_seen_at=job_data.get("first_seen_at", now),
                    last_seen_at=job_data.get("last_seen_at", now),
                    seen_count=job_data.get("seen_count", 1),
                    dedupe_key=job_data.get("dedupe_key"),
                    last_search_query=job_data.get("source", ""),
                )
                self.db.add(job)

            job.title = job_data.get("title", job.title)
            job.company = job_data.get("company", job.company)
            job.location = job_data.get("location", job.location)
            job.remote = job_data.get("remote", job.remote)
            job.posted_date = job_data.get("posted_date", job.posted_date)
            job.salary = job_data.get("salary", job.salary)
            job.url = job_data.get("url", job.url)
            job.jd_raw = job_data.get("jd_raw", job.jd_raw)
            job.source = job_data.get("source", job.source)
            job.fit_score = score.get("fit_score", 0)
            job.interview_chance = score.get("interview_chance", "Medium")
            job.apply_priority = score.get("apply_priority", "P3")
            job.why_match = score.get("why_match", "")
            job.biggest_gap = score.get("biggest_gap", "")
            job.resume_tweaks = json.dumps(score.get("resume_tweaks", []))
            job.why_company_angle = score.get("why_company_angle", "")
            job.should_apply = score.get("should_apply", True)
            job.ai_model = self.gemini_model
            job.dedupe_key = job_data.get("dedupe_key", job.dedupe_key)
            job.last_search_query = job_data.get("source", job.last_search_query)
            job.last_seen_at = job_data.get("last_seen_at", now)
            if not job.first_seen_at:
                job.first_seen_at = job_data.get("first_seen_at", now)

            job_data["status"] = job.status or "new"
            saved += 1

        self.db.commit()
        print(f"\n[OK] Saved {saved} jobs to database")
        return saved

    def generate_report(
        self,
        scored_jobs: list[dict],
        output_path: str = "ranked_jobs.md",
        tracking_summary: dict | None = None,
    ) -> str:
        """
        Generate a markdown report of ranked jobs.

        Returns:
            Path to generated report
        """
        tracking_summary = tracking_summary or {}
        if not scored_jobs and not tracking_summary:
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
- **New Jobs This Run:** {tracking_summary.get('new_jobs', len(scored_jobs))}
- **Previously Seen Jobs:** {tracking_summary.get('existing_jobs', 0)}
- **Already Scored Earlier:** {tracking_summary.get('already_scored_jobs', 0)}

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
| Tracking Status | {job.get('status', 'new')} |

**Why it matches:** {score.get('why_match', 'N/A')}

**Biggest gap:** {score.get('biggest_gap', 'N/A')}

**Resume tweaks:**
{chr(10).join('- ' + t for t in score.get('resume_tweaks', []))}

**Why this company:** {score.get('why_company_angle', 'N/A')}

[Apply Here]({job.get('url', '#')})

---

"""

        report += f"""## P2 Jobs (Strong Apply)

{chr(10).join(f"- {j.get('title')} @ {j.get('company')} (Score: {j.get('score', {}).get('fit_score', 0)}, Status: {j.get('status', 'new')})" for j in p2_jobs[:10])}

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

    def _tracked_jobs(self) -> list[Job]:
        """Return tracked jobs ordered for dashboard and tracker views."""
        return (
            self.db.query(Job)
            .order_by(
                Job.status.asc(),
                Job.fit_score.desc().nullslast(),
                Job.updated_at.desc(),
            )
            .all()
        )

    def generate_tracker_markdown(
        self,
        output_path: str = "job_tracker.md",
    ) -> str:
        """Generate a markdown tracker file from the database."""
        jobs = self._tracked_jobs()
        status_order = [
            "new",
            "reviewed",
            "applied",
            "skipped",
            "rejected",
            "interview",
            "offer",
            "archived",
        ]

        grouped_jobs: dict[str, list[Job]] = {status: [] for status in status_order}
        for job in jobs:
            grouped_jobs.setdefault(job.status or "new", []).append(job)

        total = len(jobs)
        applied = len(grouped_jobs.get("applied", []))
        interview = len(grouped_jobs.get("interview", []))
        offer = len(grouped_jobs.get("offer", []))

        lines = [
            "# Job Tracker",
            "",
            f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## Summary",
            "",
            f"- Total tracked jobs: {total}",
            f"- Applied: {applied}",
            f"- Interview: {interview}",
            f"- Offer: {offer}",
            "",
        ]

        for status in status_order:
            jobs_in_status = grouped_jobs.get(status, [])
            if not jobs_in_status:
                continue

            lines.extend([
                f"## {status.title()}",
                "",
                "| ID | Title | Company | AI Score | Human Verdict | Human Score | Seen | Last Seen | Link |",
                "|---|---|---|---:|---|---:|---:|---|---|",
            ])

            for job in jobs_in_status:
                lines.append(
                    "| {id} | {title} | {company} | {ai_score} | {verdict} | {human_score} | {seen} | {last_seen} | {link} |".format(
                        id=job.id,
                        title=(job.title or "").replace("|", "/"),
                        company=(job.company or "").replace("|", "/"),
                        ai_score=job.fit_score if job.fit_score is not None else "",
                        verdict=job.human_verdict or "",
                        human_score=job.human_score if job.human_score is not None else "",
                        seen=job.seen_count or 1,
                        last_seen=job.last_seen_at.strftime("%Y-%m-%d") if job.last_seen_at else "",
                        link=f"[Open]({job.url})" if job.url else "",
                    )
                )
                if job.human_feedback:
                    lines.append(f"|  | Feedback | {job.human_feedback.replace('|', '/')} |  |  |  |  |  |  |")
                elif job.notes:
                    lines.append(f"|  | Notes | {job.notes.replace('|', '/')} |  |  |  |  |  |  |")

            lines.append("")

        output_file = Path(output_path)
        output_file.write_text("\n".join(lines), encoding="utf-8")
        print(f"[OK] Tracker markdown saved to: {output_path}")
        return output_path

    def generate_dashboard_html(
        self,
        output_path: str = "job_dashboard.html",
    ) -> str:
        """Generate a static HTML dashboard from the database."""
        jobs = self._tracked_jobs()
        status_order = [
            "new",
            "reviewed",
            "applied",
            "skipped",
            "rejected",
            "interview",
            "offer",
            "archived",
        ]
        grouped_jobs: dict[str, list[Job]] = {status: [] for status in status_order}
        for job in jobs:
            grouped_jobs.setdefault(job.status or "new", []).append(job)

        summary_cards = [
            ("Tracked Jobs", str(len(jobs))),
            ("Applied", str(len(grouped_jobs.get("applied", [])))),
            ("Interview", str(len(grouped_jobs.get("interview", [])))),
            ("Offers", str(len(grouped_jobs.get("offer", [])))),
            ("Need Review", str(len(grouped_jobs.get("new", [])) + len(grouped_jobs.get("reviewed", [])))),
        ]

        def render_cards() -> str:
            return "".join(
                f"""
                <div class="card">
                  <div class="card-label">{html.escape(label)}</div>
                  <div class="card-value">{html.escape(value)}</div>
                </div>
                """
                for label, value in summary_cards
            )

        def render_rows(items: list[Job]) -> str:
            rows = []
            for job in items:
                badge_class = (job.status or "new").lower()
                rows.append(
                    f"""
                    <tr>
                      <td>{job.id}</td>
                      <td>
                        <div class="job-title">{html.escape(job.title or 'Unknown')}</div>
                        <div class="job-meta">{html.escape(job.company or 'Unknown')} · {html.escape(job.location or 'Unknown')}</div>
                      </td>
                      <td>{job.fit_score if job.fit_score is not None else '-'}</td>
                      <td>{html.escape(job.apply_priority or '-')}</td>
                      <td><span class="badge badge-{badge_class}">{html.escape(job.status or 'new')}</span></td>
                      <td>{html.escape(job.human_verdict or '-')}</td>
                      <td>{job.human_score if job.human_score is not None else '-'}</td>
                      <td>{job.seen_count or 1}</td>
                      <td>{job.last_seen_at.strftime("%Y-%m-%d") if job.last_seen_at else '-'}</td>
                      <td>{f'<a href="{html.escape(job.url)}" target="_blank" rel="noreferrer">Open</a>' if job.url else '-'}</td>
                    </tr>
                    <tr class="detail-row">
                      <td></td>
                      <td colspan="8">
                        <div><strong>AI:</strong> {html.escape(job.why_match or '-')}</div>
                        <div><strong>Gap:</strong> {html.escape(job.biggest_gap or '-')}</div>
                        <div><strong>Feedback:</strong> {html.escape(job.human_feedback or job.notes or '-')}</div>
                      </td>
                      <td></td>
                    </tr>
                    """
                )
            return "".join(rows)

        sections = []
        for status in status_order:
            jobs_in_status = grouped_jobs.get(status, [])
            if not jobs_in_status:
                continue
            sections.append(
                f"""
                <section class="section">
                  <div class="section-head">
                    <h2>{html.escape(status.title())}</h2>
                    <span>{len(jobs_in_status)} job(s)</span>
                  </div>
                  <div class="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>ID</th>
                          <th>Job</th>
                          <th>AI</th>
                          <th>Priority</th>
                          <th>Status</th>
                          <th>Verdict</th>
                          <th>Human</th>
                          <th>Seen</th>
                          <th>Last Seen</th>
                          <th>Link</th>
                        </tr>
                      </thead>
                      <tbody>
                        {render_rows(jobs_in_status)}
                      </tbody>
                    </table>
                  </div>
                </section>
                """
            )

        html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Job Tracker Dashboard</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --panel: #fffaf0;
      --ink: #1f2a1f;
      --muted: #657166;
      --line: #d8d0c2;
      --accent: #1f7a5c;
      --applied: #0f766e;
      --reviewed: #8a6d1a;
      --new: #3b82f6;
      --skipped: #8b5e34;
      --rejected: #9f1239;
      --interview: #7c3aed;
      --offer: #166534;
      --archived: #6b7280;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(31,122,92,0.08), transparent 30%),
        linear-gradient(180deg, #f8f4ec 0%, var(--bg) 100%);
    }}
    .page {{
      width: min(1400px, calc(100% - 32px));
      margin: 24px auto 56px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(31,122,92,0.96), rgba(21,58,45,0.96));
      color: #fef8ef;
      border-radius: 24px;
      padding: 28px;
      box-shadow: 0 18px 50px rgba(31, 42, 31, 0.16);
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3rem);
    }}
    .hero p {{
      margin: 0;
      color: rgba(254,248,239,0.86);
      max-width: 820px;
      line-height: 1.5;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin: 20px 0 28px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 10px 20px rgba(31, 42, 31, 0.06);
    }}
    .card-label {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .card-value {{
      font-size: 2rem;
      margin-top: 8px;
      font-weight: 700;
    }}
    .section {{
      background: rgba(255,250,240,0.88);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      margin-top: 18px;
      overflow: hidden;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 1.3rem;
    }}
    .section-head span {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 1000px;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      font-size: 0.95rem;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      font-size: 0.78rem;
    }}
    .job-title {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .job-meta {{
      color: var(--muted);
      font-size: 0.88rem;
    }}
    .detail-row td {{
      padding-top: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .badge {{
      display: inline-flex;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.78rem;
      text-transform: capitalize;
      color: white;
    }}
    .badge-new {{ background: var(--new); }}
    .badge-reviewed {{ background: var(--reviewed); }}
    .badge-applied {{ background: var(--applied); }}
    .badge-skipped {{ background: var(--skipped); }}
    .badge-rejected {{ background: var(--rejected); }}
    .badge-interview {{ background: var(--interview); }}
    .badge-offer {{ background: var(--offer); }}
    .badge-archived {{ background: var(--archived); }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    @media (max-width: 700px) {{
      .page {{
        width: min(100% - 16px, 1400px);
      }}
      .hero {{
        padding: 20px;
      }}
      .section {{
        padding: 14px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Job Tracker Dashboard</h1>
      <p>Open this file after each run to review AI recommendations, mark applied jobs, compare your own verdicts against the AI score, and keep one visible place for the whole search.</p>
      <p>Generated: {html.escape(datetime.utcnow().strftime('%Y-%m-%d %H:%M'))}</p>
    </section>
    <section class="cards">
      {render_cards()}
    </section>
    {''.join(sections) if sections else '<section class="section"><p>No tracked jobs yet.</p></section>'}
  </div>
</body>
</html>
"""

        output_file = Path(output_path)
        output_file.write_text(html_text, encoding="utf-8")
        print(f"[OK] Dashboard saved to: {output_path}")
        return output_path

    def export_to_json(self, output_path: str = "job_tracker.json") -> str:
        """Export all tracked jobs to JSON for Cloudflare D1 sync."""
        jobs = self._tracked_jobs()

        export_data = {
            "exported_at": datetime.utcnow().isoformat(),
            "version": "1.0",
            "jobs": [],
        }

        for job in jobs:
            export_data["jobs"].append({
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "remote": job.remote,
                "posted_date": job.posted_date,
                "salary": job.salary,
                "url": job.url,
                "jd_raw": job.jd_raw,
                "jd_summary": job.jd_summary,
                "fit_score": job.fit_score,
                "interview_chance": job.interview_chance,
                "apply_priority": job.apply_priority,
                "ai_model": job.ai_model,
                "why_match": job.why_match,
                "biggest_gap": job.biggest_gap,
                "resume_tweaks": job.resume_tweaks,
                "why_company_angle": job.why_company_angle,
                "should_apply": job.should_apply,
                "status": job.status,
                "notes": job.notes,
                "human_verdict": job.human_verdict,
                "human_score": job.human_score,
                "human_feedback": job.human_feedback,
                "feedback_updated_at": job.feedback_updated_at.isoformat() if job.feedback_updated_at else None,
                "applied_at": job.applied_at.isoformat() if job.applied_at else None,
                "seen_count": job.seen_count,
                "source": job.source,
                "apify_run_id": job.apify_run_id,
                "first_seen_at": job.first_seen_at.isoformat() if job.first_seen_at else None,
                "last_seen_at": job.last_seen_at.isoformat() if job.last_seen_at else None,
                "scraped_at": job.scraped_at.isoformat() if job.scraped_at else None,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            })

        output_file = Path(output_path)
        output_file.write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")
        print(f"[OK] JSON export saved to: {output_path} ({len(export_data['jobs'])} jobs)")
        return output_path

    async def run_full_pipeline(
        self,
        searches: list[dict] | None = None,
        generate_report: bool = True,
        report_path: str = "ranked_jobs.md",
        tracker_path: str = "job_tracker.md",
        dashboard_path: str = "job_dashboard.html",
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

        tracking_summary = self.prepare_jobs_for_scoring(search_results["jobs"])

        if not tracking_summary["jobs_to_score"]:
            print("[INFO] No new jobs to score. Everything found this run is already tracked.")
            if generate_report:
                self.generate_report(
                    [],
                    report_path,
                    tracking_summary=tracking_summary,
                )
            self.generate_tracker_markdown(tracker_path)
            self.generate_dashboard_html(dashboard_path)
            self.export_to_json("job_tracker.json")
            return {
                "success": True,
                "total_jobs": search_results["total_jobs"],
                "scored_jobs": 0,
                "top_score": 0,
                "p1_jobs": 0,
                "existing_jobs": tracking_summary["existing_jobs"],
                "new_jobs": tracking_summary["new_jobs"],
                "report_path": report_path if generate_report else None,
                "tracker_path": tracker_path,
                "dashboard_path": dashboard_path,
                "json_export_path": "job_tracker.json",
            }

        # Step 2: Score
        print("\n[STEP 2] Scoring jobs with Gemini")
        print("=" * 50)
        scored_jobs = await self.score_jobs(tracking_summary["jobs_to_score"])

        # Step 3: Save to DB
        print("\n[STEP 3] Saving to database")
        print("=" * 50)
        self.save_to_db(scored_jobs)

        # Step 4: Generate report
        if generate_report:
            print("\n[STEP 4] Generating report")
            print("=" * 50)
            self.generate_report(
                scored_jobs,
                report_path,
                tracking_summary=tracking_summary,
            )

        print("\n[STEP 5] Generating tracker views")
        print("=" * 50)
        self.generate_tracker_markdown(tracker_path)
        self.generate_dashboard_html(dashboard_path)

        print("\n[STEP 6] Exporting to JSON for Cloudflare")
        print("=" * 50)
        self.export_to_json("job_tracker.json")

        print("\n" + "=" * 50)
        print("[OK] Pipeline complete!")
        print("=" * 50)

        return {
            "success": True,
            "total_jobs": search_results["total_jobs"],
            "scored_jobs": len(scored_jobs),
            "top_score": scored_jobs[0].get("score", {}).get("fit_score", 0) if scored_jobs else 0,
            "p1_jobs": len([j for j in scored_jobs if j.get("score", {}).get("apply_priority") == "P1"]),
            "existing_jobs": tracking_summary["existing_jobs"],
            "new_jobs": tracking_summary["new_jobs"],
            "report_path": report_path if generate_report else None,
            "tracker_path": tracker_path,
            "dashboard_path": dashboard_path,
            "json_export_path": "job_tracker.json",
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
