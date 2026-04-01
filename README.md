# AI-Powered Job Search System

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An intelligent job aggregation and scoring pipeline that combines multi-source job scraping with LLM-based candidate matching. Built to demonstrate production-ready async architecture, feedback-driven AI calibration, and end-to-end data pipeline design.

## Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│  Apify API  │────▶│  Async Queue │────▶│  Gemini API │────▶│   SQLite    │
│ (Scraping)  │     │  (Batching)  │     │  (Scoring)  │     │  (Storage)  │
└─────────────┘     └──────────────┘     └─────────────┘     └──────┬──────┘
                                                                    │
                     ┌──────────────────────────────────────────────┘
                     ▼
            ┌─────────────────┐
            │  Feedback Loop  │◀──── Human verdicts calibrate future scoring
            └─────────────────┘
```

## Key Engineering Decisions

| Challenge | Solution | Why |
|-----------|----------|-----|
| **Duplicate jobs across runs** | 3-tier deduplication (URL → dedupe_key → title/company/location) | Apify returns same jobs with different IDs; need stable identity across 30+ day windows |
| **API rate limiting** | Semaphore-bounded concurrency (5 parallel) + 1s batch delays | Gemini has undocumented rate limits; found through empirical testing |
| **Cold start scoring** | Human feedback history injected into prompt context | LLM has no memory; calibrate by showing past human decisions |
| **Multi-source normalization** | `_normalize_job()` abstraction layer | Different Apify actors return different field names |
| **Schema evolution** | `_ensure_sqlite_schema()` backfill on init | Avoid breaking existing databases when adding columns |

## Features

- **Multi-source aggregation**: Fetches jobs from LinkedIn, Indeed, and 20+ sources via Apify actors
- **LLM-based scoring**: 9-factor weighted rubric (100 points total) evaluating skills, experience, and fit
- **Cross-run deduplication**: Tracks jobs across 30+ day windows without duplicates
- **Human feedback loop**: Stores human verdicts to calibrate AI scoring over time
- **Priority ranking**: P1/P2/P3 classification based on fit score thresholds
- **Dual output**: CLI reports + web dashboard for tracking applications

## Tech Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.12+ |
| Async Runtime | asyncio + asyncio.Semaphore |
| LLM API | Google Gemini Flash Preview |
| Job Data | Apify API (100+ sources) |
| Database | SQLite + SQLAlchemy 2.0 |
| CLI | argparse with subcommands |
| Web UI | FastAPI + vanilla HTML/CSS/JS dashboard |
| Packaging | uv + pyproject.toml |

## Quick Start

```bash
# Install dependencies
uv sync --all-extras

# Configure API keys
cp .env.example .env
# Edit .env with APIFY_API_KEY and GEMINI_API_KEY

# Run full pipeline
uv run job-scraper run

# Launch the FastAPI dashboard
uv run --extra web uvicorn job_scraper.web.app:app --reload

# Run the test suite
uv run --extra dev pytest
```

## CLI Commands

```bash
# Full pipeline: search → score → save → report
uv run job-scraper run

# Custom search with filters
uv run job-scraper search "Product Manager" --location "Remote" --limit 50

# Score jobs from existing JSON
uv run job-scraper score --jobs my_jobs.json --resume resume.pdf

# Track and update applications
uv run job-scraper list --status new
uv run job-scraper status 42 applied --notes "Referred by John"
uv run job-scraper feedback 42 --verdict apply --score 88 --feedback "Strong AI match"

# Generate dashboard views
uv run job-scraper dashboard --merge-duplicates
```

## Scoring Rubric

Jobs are scored 0-100 across 9 weighted factors:

| Factor | Weight | Description |
|--------|--------|-------------|
| Role title relevance | 20 pts | How closely title matches target roles |
| Skills match | 20 pts | Overlap between JD requirements and resume |
| Experience match | 15 pts | Seniority level alignment |
| Product ownership fit | 10 pts | Evidence of end-to-end product leadership |
| AI/technical overlap | 10 pts | Relevance of technical/AI experience |
| Company quality/stage | 10 pts | Funding stage, growth trajectory |
| Location fit | 5 pts | Remote/onsite alignment |
| Salary visibility | 5 pts | Compensation transparency |
| Resume gap severity | 5 pts | Missing critical requirements |

**Interpretation:**
- **85-100 (P1)**: Apply within 24 hours
- **70-84 (P2)**: Strong candidate, prioritize this week
- **55-69 (P3)**: Stretch if company has high upside
- **<55**: Skip

## Project Structure

```
job-scraper/
├── src/job_scraper/
│   ├── __init__.py         # Package exports
│   ├── apify_scraper.py    # Apify API wrapper with country normalization
│   ├── gemini_scorer.py    # LLM scoring with structured JSON schemas
│   ├── orchestrator.py     # Main pipeline: search → dedupe → score → save
│   ├── models.py           # SQLAlchemy schema with backfill migrations
│   ├── resume_loader.py    # Multi-format resume parsing (PDF/TXT/JSON)
│   └── cli.py              # CLI entry point with 8 subcommands
├── tests/                  # pytest test suite
├── .env.example            # Environment template
├── pyproject.toml          # Package metadata + uv lock
├── job_tracker.db          # SQLite database (runtime)
├── job_dashboard.html      # Generated HTML dashboard
└── ranked_jobs.md          # Generated markdown report
```

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Jobs processed per run | 50-200 |
| API calls per run | 5-10 (Apify) + N (Gemini) |
| Deduplication accuracy | ~98% (empirical) |
| Batch concurrency | 5 parallel Gemini requests |
| Database size after 30 days | ~500 jobs, 2 MB |

## Database Schema

```sql
jobs (
  id, title, company, location, remote, posted_date, salary, url,
  jd_raw, jd_summary,
  fit_score, interview_chance, apply_priority, ai_model,
  why_match, biggest_gap, resume_tweaks, why_company_angle,
  should_apply, status, notes,
  human_verdict, human_score, human_feedback, feedback_updated_at,
  scraped_at, first_seen_at, last_seen_at, seen_count,
  source, apify_run_id, dedupe_key, last_search_query,
  applied_at, updated_at
)

search_queries (
  id, query_name, keywords, location, experience_level,
  date_posted, limit, last_run, jobs_found, apify_run_id, created_at
)

companies (
  id, name, industry, size, stage,
  total_jobs, applications, interviews,
  target_company, notes, created_at, updated_at
)
```

## Lessons Learned

1. **Deduplication is harder than expected**: URLs change, titles get reformatted, companies have alternate names. A 3-tier strategy (URL → composite key → fuzzy match) is necessary for production use.

2. **LLM consistency varies**: Gemini Flash preview is fast but occasionally returns malformed JSON. Structured output mode + retry logic is essential.

3. **Human feedback is gold**: The first run's scoring is generic. After 10-20 human verdicts, the calibration improves significantly because the LLM sees patterns in what *you* value.

4. **Async concurrency needs bounds**: Unbounded `asyncio.gather()` on 100 jobs will hit rate limits. Semaphore-bounded batches with delays are more reliable than aggressive parallelism.

## Future Enhancements

- [ ] Background scheduler for automatic dashboard refreshes
- [ ] Automated daily runs via cron/GitHub Actions
- [ ] Email/Slack alerts for P1 jobs
- [ ] A/B testing for prompt variations
- [ ] Interview preparation module with company-specific questions

## License

MIT

---

*Built as a demonstration of production-ready async Python, LLM integration patterns, and feedback-driven AI systems.*
