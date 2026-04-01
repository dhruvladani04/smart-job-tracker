# Job Search Orchestrator

AI-powered job search pipeline that combines **Apify** job scrapers with **Gemini API** for intelligent job scoring and resume tailoring.

## Overview

This system automates the job search workflow:

```
Apify API (scrapes jobs) -> Gemini API (scores fit) -> SQLite (storage) -> Markdown Report
```

Based on the comprehensive guide from [prompt.txt.txt](./prompt.txt.txt).

## Features

- **Multi-source job scraping** via Apify actors (LinkedIn, Indeed, aggregators)
- **AI-powered scoring** using Gemini with a weighted rubric (Step 7 of the guide)
- **Resume tailoring suggestions** for each job
- **SQLite database** for tracking applications
- **Deduplication** across multiple searches
- **Priority ranking** (P1/P2/P3) based on fit

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Set up API keys

```bash
cp .env.example .env
```

Edit `.env` and add your keys:

- **APIFY_API_KEY**: Get from [Apify Console](https://console.apify.com/account#/integrations)
- **GEMINI_API_KEY**: Get from [Google AI Studio](https://aistudio.google.com/app/apikey)

### 3. Add your resume files

By default the project uses these resume PDFs if they exist:

- `Dhruv_Ladani_Resume_PM.pdf`
- `Dhruv_Ladani_Resume_Tech.pdf`

You can also pass custom resume files with repeated `--resume` flags.

### 4. Run the pipeline

```bash
uv run job-scraper run
```

This will:
1. Search for jobs across 5 predefined queries (APM Remote, PM India, etc.)
2. Score each job using Gemini (0-100 fit score)
3. Save results to `job_tracker.db`
4. Generate `ranked_jobs.md` report

## CLI Commands

```bash
# Initialize project
uv run job-scraper init

# Run full pipeline
uv run job-scraper run

# Run custom search
uv run job-scraper search "AI Product Manager" --location "Remote" --limit 50

# Score jobs from JSON file
uv run job-scraper score --resume Dhruv_Ladani_Resume_PM.pdf --resume Dhruv_Ladani_Resume_Tech.pdf --jobs jobs.json --output scores.json
```

## Configuration

### Default Searches

The pipeline runs these searches by default (from `DEFAULT_SEARCHES` in orchestrator):

| Query | Keywords | Location |
|-------|----------|----------|
| APM Remote | Associate Product Manager | Remote |
| PM India | Product Manager | India |
| AI PM India | AI Product Manager | India |
| Product Analyst Bangalore | Product Analyst | Bangalore |
| Technical PM Remote | Technical Product Manager | Remote |

To customize, create a JSON file:

```json
[
  {
    "query_name": "My Search",
    "keywords": "Product Manager",
    "location": "Mumbai",
    "actor": "all_jobs",
    "date_posted": "7 days",
    "limit": 100
  }
]
```

Then run: `job-scraper run --searches my_searches.json`

### Apify Actors

Three actors are supported:

| Actor | Best For |
|-------|----------|
| `all_jobs` | General multi-source scraping |
| `ai_job_finder` | AI-powered matching with CV upload |
| `linkedin` | LinkedIn-specific searches |

## Scoring Rubric

Jobs are scored 0-100 based on:

| Factor | Weight |
|--------|--------|
| Role title relevance | 20 |
| Skills match | 20 |
| Experience match | 15 |
| Product ownership fit | 10 |
| AI/technical overlap | 10 |
| Company quality/stage | 10 |
| Location fit | 5 |
| Salary visibility | 5 |
| Resume gap severity | 5 |

**Interpretation:**
- 85-100: Apply immediately
- 70-84: Strong apply
- 55-69: Stretch if high upside
- <55: Skip

## Database Schema

Jobs are stored in SQLite with these fields:

- Job details (title, company, location, salary, url)
- Scoring (fit_score, interview_chance, apply_priority)
- AI insights (why_match, biggest_gap, resume_tweaks)
- Tracking (status, applied_at, notes)

## Output Report

The generated `ranked_jobs.md` includes:

- Summary statistics
- P1 (top priority) jobs with full analysis
- P2 jobs list
- Score distribution table
- Direct apply links

## Project Structure

```
job-scraper/
├── src/job_scraper/
│   ├── __init__.py
│   ├── apify_scraper.py    # Apify API wrapper
│   ├── gemini_scorer.py    # Gemini API scoring
│   ├── orchestrator.py     # Main pipeline
│   ├── models.py           # SQLAlchemy models
│   └── cli.py              # CLI entry point
├── Dhruv_Ladani_Resume_PM.pdf
├── Dhruv_Ladani_Resume_Tech.pdf
├── .env                    # API keys
├── .env.example            # Template
├── job_tracker.db          # SQLite database (created on first run)
├── ranked_jobs.md          # Output report
└── pyproject.toml
```

## Tips (from the Guide)

1. **Use fresh jobs**: Set `date_posted` to "7 days" or "1 days" for better response rates
2. **Narrow searches**: Run multiple targeted searches instead of one broad search
3. **Be truthful**: Never let AI invent resume claims
4. **Track everything**: Use the database to learn what converts
5. **Bias toward action**: P1 jobs should be applied to within 24 hours

## Building the Dashboard (Next Phase)

This pipeline is designed to feed into a dashboard:

```python
# Example: Query top jobs from DB
from job_scraper.models import init_db, Job

db = init_db()
top_jobs = db.query(Job).filter(
    Job.fit_score >= 85,
    Job.status == "new"
).order_by(Job.fit_score.desc()).limit(10).all()
```

Future enhancements:
- Next.js/Django dashboard
- Daily automated scraping via cron
- Application tracking UI
- Interview preparation module

## Troubleshooting

**"APIFY_API_KEY not found"**: Ensure `.env` exists and has the key

**"No jobs found"**: Check that your search terms are valid and Apify actor is accessible

**"Gemini returned an empty response"**: This can happen if the API returns an error. Check your `GEMINI_API_KEY`

**Rate limits**: Gemini API has rate limits. The batch processing (5 concurrent) should stay within limits.

## License

MIT
