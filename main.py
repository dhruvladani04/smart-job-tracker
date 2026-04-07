"""
AI-Powered Job Search System - Entry Point

This package provides a CLI tool for running the job scraper pipeline.
Use via: uv run job-scraper <command>

Commands:
    run         Execute full pipeline (search -> score -> save -> report)
    search      Search for jobs with custom filters
    score       Score jobs from JSON file
    init        Initialize project structure
    list        List tracked jobs
    status      Update job status
    feedback    Store human feedback for calibration
    dashboard   Generate HTML dashboard

For more info: uv run job-scraper --help
"""

from job_scraper.cli import main

if __name__ == "__main__":
    main()
