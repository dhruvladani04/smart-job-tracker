"""
CLI entry point for the job scraper.
"""
import asyncio
import argparse
import sys
from datetime import datetime
from pathlib import Path

from .models import Job, init_db
from .orchestrator import JobSearchOrchestrator
from .resume_loader import DEFAULT_RESUME_PATHS, load_resume_bundle


def _resume_paths_from_args(args) -> list[str] | None:
    """Collect resume paths from CLI args while keeping legacy support."""
    paths = []

    if getattr(args, "resume", None):
        paths.extend(args.resume)
    if getattr(args, "profile", None):
        paths.append(args.profile)

    return paths or None


def _refresh_tracker_outputs(args) -> None:
    """Regenerate visible tracker files after DB-changing commands."""
    orchestrator = JobSearchOrchestrator(
        resume_paths=_resume_paths_from_args(args),
        database_url=args.database,
        gemini_model=getattr(args, "model", "gemini-3-flash-preview"),
    )
    tracker_path = getattr(args, "tracker_output", "job_tracker.md")
    dashboard_path = getattr(args, "dashboard_output", "job_dashboard.html")
    orchestrator.generate_tracker_markdown(tracker_path)
    orchestrator.generate_dashboard_html(dashboard_path)


def cmd_run(args):
    """Run the full pipeline."""
    orchestrator = JobSearchOrchestrator(
        resume_paths=_resume_paths_from_args(args),
        database_url=args.database,
        gemini_model=args.model,
    )

    # Load custom searches if provided
    searches = None
    if args.searches:
        import json
        with open(args.searches) as f:
            searches = json.load(f)

    result = asyncio.run(orchestrator.run_full_pipeline(
        searches=searches,
        generate_report=not args.no_report,
        report_path=args.output,
        tracker_path=args.tracker_output,
        dashboard_path=args.dashboard_output,
    ))

    if result["success"]:
        print(
            f"\n[OK] Success! Found {result['p1_jobs']} P1 priority jobs. "
            f"New jobs: {result.get('new_jobs', 0)} | Previously seen: {result.get('existing_jobs', 0)}"
        )
        return 0
    else:
        print(f"\n[ERROR] Pipeline failed: {result.get('error', 'Unknown error')}")
        return 1


def cmd_search(args):
    """Run a single custom search."""
    from .apify_scraper import ApifyScraper

    apify = ApifyScraper()

    result = asyncio.run(apify.search_jobs(
        query_name=args.name,
        keywords=args.keywords,
        location=args.location or "",
        actor=args.actor,
        date_posted=args.date_posted,
        limit=args.limit,
    ))

    print(f"\n[OK] Found {len(result)} jobs")

    if args.output:
        import json
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(f"[OK] Saved to {args.output}")

    return 0


def cmd_score(args):
    """Score jobs from a JSON file."""
    from .gemini_scorer import GeminiScorer
    import json

    with open(args.jobs) as f:
        jobs = json.load(f)

    resume_bundle = load_resume_bundle(_resume_paths_from_args(args))
    scorer = GeminiScorer(model=args.model)

    print(f"Scoring {len(jobs)} jobs...")
    scores = asyncio.run(scorer.score_batch(resume_bundle["resume_text"], jobs))

    # Output results
    results = []
    for job, score in zip(jobs, scores):
        results.append({
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("url"),
            "score": score,
        })

    results.sort(key=lambda x: x["score"]["fit_score"], reverse=True)

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"[OK] Saved scores to {args.output}")
    else:
        # Print top 10
        print("\n[INFO] Top 10 Jobs:")
        for i, r in enumerate(results[:10], 1):
            s = r["score"]
            print(f"{i}. {r['title']} @ {r['company']}")
            print(f"   Score: {s['fit_score']}/100 | Interview: {s['interview_chance']}")
            print(f"   Priority: {s['apply_priority']} | Apply: {'Yes' if s['should_apply'] else 'No'}")
            print()

    return 0


def cmd_init(args):
    """Initialize the project with example files."""
    import shutil

    base = Path(".")

    # Create .env if not exists
    env_file = base / ".env"
    if not env_file.exists():
        example = base / ".env.example"
        if example.exists():
            shutil.copy(example, env_file)
            print("[OK] Created .env file")
        else:
            print("[WARN] .env.example not found, creating .env template")
            env_file.write_text(
                "APIFY_API_KEY=\n"
                "GEMINI_API_KEY=\n"
            )

    default_pdfs = [base / name for name in DEFAULT_RESUME_PATHS]
    if not any(path.exists() for path in default_pdfs):
        print("[WARN] Add your resume PDFs or pass --resume when running commands")

    print("\n[INFO] Next steps:")
    print("1. Edit .env and add your API keys")
    print("2. Make sure your resume PDFs are present")
    print("3. Run: job-scraper run")

    return 0


def cmd_list_jobs(args):
    """List tracked jobs from the database."""
    db = init_db(args.database)
    query = db.query(Job)

    if args.status:
        query = query.filter(Job.status == args.status)
    if args.verdict:
        query = query.filter(Job.human_verdict == args.verdict)
    if not args.include_hidden:
        query = query.filter(Job.status != "archived")

    jobs = query.order_by(Job.updated_at.desc()).limit(args.limit).all()

    if not jobs:
        print("[INFO] No tracked jobs matched your filters.")
        return 0

    for job in jobs:
        print(f"[{job.id}] {job.title} @ {job.company}")
        print(
            f"   Score: {job.fit_score or 'N/A'} | Priority: {job.apply_priority or 'N/A'} | "
            f"Status: {job.status or 'new'} | Verdict: {job.human_verdict or 'N/A'} | "
            f"Seen: {job.seen_count or 1}"
        )
        if job.url:
            print(f"   URL: {job.url}")
        if job.human_feedback:
            print(f"   Feedback: {job.human_feedback}")
        print()

    return 0


def cmd_update_status(args):
    """Update tracked job status."""
    db = init_db(args.database)
    job = db.query(Job).filter(Job.id == args.job_id).first()

    if not job:
        print(f"[ERROR] Job {args.job_id} not found.")
        return 1

    job.status = args.status
    if args.notes:
        job.notes = args.notes
    if args.status == "applied" and not job.applied_at:
        job.applied_at = datetime.utcnow()

    db.commit()
    _refresh_tracker_outputs(args)
    print(f"[OK] Updated job {job.id} to status '{job.status}'.")
    return 0


def cmd_feedback(args):
    """Store human feedback for a tracked job."""
    db = init_db(args.database)
    job = db.query(Job).filter(Job.id == args.job_id).first()

    if not job:
        print(f"[ERROR] Job {args.job_id} not found.")
        return 1

    if args.verdict:
        job.human_verdict = args.verdict
    if args.score is not None:
        job.human_score = args.score
    if args.feedback:
        job.human_feedback = args.feedback
    if args.notes:
        job.notes = args.notes

    job.feedback_updated_at = datetime.utcnow()
    db.commit()
    _refresh_tracker_outputs(args)
    print(f"[OK] Saved feedback for job {job.id}.")
    return 0


def cmd_dashboard(args):
    """Generate visible dashboard and tracker files from the database."""
    orchestrator = JobSearchOrchestrator(
        resume_paths=_resume_paths_from_args(args),
        database_url=args.database,
        gemini_model=args.model,
    )
    if args.merge_duplicates:
        merged = orchestrator.merge_duplicate_jobs()
        print(f"[OK] Merged {merged} duplicate job rows before generating dashboard.")
    tracker_path = orchestrator.generate_tracker_markdown(args.tracker_output)
    dashboard_path = orchestrator.generate_dashboard_html(args.dashboard_output)
    print(f"[OK] Tracker ready at {tracker_path}")
    print(f"[OK] Dashboard ready at {dashboard_path}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="AI-powered job search pipeline with Apify + Gemini",
        prog="job-scraper",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run the full pipeline")
    run_parser.add_argument(
        "--resume", "-r",
        action="append",
        help="Path to a resume source (PDF/TXT/JSON). Pass multiple times to use multiple files.",
    )
    run_parser.add_argument(
        "--profile", "-p",
        help="Legacy alias for a single resume source path.",
    )
    run_parser.add_argument(
        "--database", "-d",
        default="sqlite:///job_tracker.db",
        help="Database URL (default: sqlite:///job_tracker.db)",
    )
    run_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use",
    )
    run_parser.add_argument(
        "--searches", "-s",
        help="Path to custom searches JSON file",
    )
    run_parser.add_argument(
        "--output", "-o",
        default="ranked_jobs.md",
        help="Output path for report (default: ranked_jobs.md)",
    )
    run_parser.add_argument(
        "--tracker-output",
        default="job_tracker.md",
        help="Output path for tracker markdown (default: job_tracker.md)",
    )
    run_parser.add_argument(
        "--dashboard-output",
        default="job_dashboard.html",
        help="Output path for dashboard HTML (default: job_dashboard.html)",
    )
    run_parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip report generation",
    )
    run_parser.set_defaults(func=cmd_run)

    # Search command
    search_parser = subparsers.add_parser("search", help="Run a single job search")
    search_parser.add_argument("keywords", help="Search keywords")
    search_parser.add_argument("--name", "-n", default="Custom Search", help="Search name")
    search_parser.add_argument("--location", "-l", help="Location filter")
    search_parser.add_argument(
        "--actor", "-a",
        choices=["ai_job_finder", "all_jobs", "linkedin"],
        default="all_jobs",
        help="Apify actor to use",
    )
    search_parser.add_argument(
        "--date-posted", "-D",
        default="7 days",
        help="Recency filter (default: 7 days)",
    )
    search_parser.add_argument(
        "--limit", "-L",
        type=int,
        default=100,
        help="Max jobs to fetch (default: 100)",
    )
    search_parser.add_argument(
        "--output", "-o",
        help="Save results to JSON file",
    )
    search_parser.set_defaults(func=cmd_search)

    # Score command
    score_parser = subparsers.add_parser("score", help="Score jobs from JSON file")
    score_parser.add_argument(
        "--resume", "-r",
        action="append",
        help="Path to a resume source (PDF/TXT/JSON). Pass multiple times to use multiple files.",
    )
    score_parser.add_argument(
        "--profile", "-p",
        help="Legacy alias for a single resume source path.",
    )
    score_parser.add_argument("--jobs", "-j", required=True, help="Jobs JSON file")
    score_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use",
    )
    score_parser.add_argument(
        "--output", "-o",
        help="Save scores to JSON file",
    )
    score_parser.set_defaults(func=cmd_score)

    # Init command
    init_parser = subparsers.add_parser("init", help="Initialize project")
    init_parser.set_defaults(func=cmd_init)

    # List tracked jobs
    list_parser = subparsers.add_parser("list", help="List tracked jobs from the database")
    list_parser.add_argument(
        "--database", "-d",
        default="sqlite:///job_tracker.db",
        help="Database URL (default: sqlite:///job_tracker.db)",
    )
    list_parser.add_argument(
        "--status",
        help="Filter by job status",
    )
    list_parser.add_argument(
        "--verdict",
        choices=["apply", "skip", "save", "unsure"],
        help="Filter by stored human verdict",
    )
    list_parser.add_argument(
        "--limit", "-L",
        type=int,
        default=25,
        help="Max jobs to show (default: 25)",
    )
    list_parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include archived jobs",
    )
    list_parser.set_defaults(func=cmd_list_jobs)

    # Update status
    status_parser = subparsers.add_parser("status", help="Update tracked job status")
    status_parser.add_argument("job_id", type=int, help="Tracked job ID")
    status_parser.add_argument(
        "status",
        choices=["new", "reviewed", "applied", "skipped", "rejected", "interview", "offer", "archived"],
        help="New job status",
    )
    status_parser.add_argument(
        "--database", "-d",
        default="sqlite:///job_tracker.db",
        help="Database URL (default: sqlite:///job_tracker.db)",
    )
    status_parser.add_argument(
        "--notes",
        help="Optional notes to store alongside the status update",
    )
    status_parser.add_argument(
        "--tracker-output",
        default="job_tracker.md",
        help="Output path for tracker markdown (default: job_tracker.md)",
    )
    status_parser.add_argument(
        "--dashboard-output",
        default="job_dashboard.html",
        help="Output path for dashboard HTML (default: job_dashboard.html)",
    )
    status_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use for tracker regeneration context",
    )
    status_parser.set_defaults(func=cmd_update_status)

    # Human feedback
    feedback_parser = subparsers.add_parser("feedback", help="Store human feedback for a tracked job")
    feedback_parser.add_argument("job_id", type=int, help="Tracked job ID")
    feedback_parser.add_argument(
        "--database", "-d",
        default="sqlite:///job_tracker.db",
        help="Database URL (default: sqlite:///job_tracker.db)",
    )
    feedback_parser.add_argument(
        "--verdict",
        choices=["apply", "skip", "save", "unsure"],
        help="Your decision on the job",
    )
    feedback_parser.add_argument(
        "--score",
        type=int,
        help="Your score override from 0-100",
    )
    feedback_parser.add_argument(
        "--feedback",
        help="Free-form critique of the AI recommendation",
    )
    feedback_parser.add_argument(
        "--notes",
        help="Additional notes to store on the job",
    )
    feedback_parser.add_argument(
        "--tracker-output",
        default="job_tracker.md",
        help="Output path for tracker markdown (default: job_tracker.md)",
    )
    feedback_parser.add_argument(
        "--dashboard-output",
        default="job_dashboard.html",
        help="Output path for dashboard HTML (default: job_dashboard.html)",
    )
    feedback_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use for tracker regeneration context",
    )
    feedback_parser.set_defaults(func=cmd_feedback)

    dashboard_parser = subparsers.add_parser("dashboard", help="Generate tracker markdown and HTML dashboard")
    dashboard_parser.add_argument(
        "--resume", "-r",
        action="append",
        help="Path to a resume source (PDF/TXT/JSON). Pass multiple times to use multiple files.",
    )
    dashboard_parser.add_argument(
        "--profile", "-p",
        help="Legacy alias for a single resume source path.",
    )
    dashboard_parser.add_argument(
        "--database", "-d",
        default="sqlite:///job_tracker.db",
        help="Database URL (default: sqlite:///job_tracker.db)",
    )
    dashboard_parser.add_argument(
        "--model", "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use",
    )
    dashboard_parser.add_argument(
        "--tracker-output",
        default="job_tracker.md",
        help="Output path for tracker markdown (default: job_tracker.md)",
    )
    dashboard_parser.add_argument(
        "--dashboard-output",
        default="job_dashboard.html",
        help="Output path for dashboard HTML (default: job_dashboard.html)",
    )
    dashboard_parser.add_argument(
        "--merge-duplicates",
        action="store_true",
        help="Merge older duplicate job rows before generating files",
    )
    dashboard_parser.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
