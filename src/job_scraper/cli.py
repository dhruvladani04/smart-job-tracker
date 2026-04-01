"""
CLI entry point for the job scraper.
"""
import asyncio
import argparse
import sys
from pathlib import Path

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
    ))

    if result["success"]:
        print(f"\n[OK] Success! Found {result['p1_jobs']} P1 priority jobs.")
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
