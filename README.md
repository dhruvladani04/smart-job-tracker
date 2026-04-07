# AI-Powered Job Search System

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/DhruvLadani18/smart-job-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/DhruvLadani18/smart-job-tracker/actions/workflows/ci.yml)

An intelligent job aggregation and scoring pipeline that combines multi-source job scraping with LLM-based candidate matching. Built to demonstrate production-ready async architecture, feedback-driven AI calibration, and end-to-end data pipeline design.

> **Results from personal use:** 1,247 jobs processed → 87% human-AI scoring correlation → 8 interviews → 2 offers

---

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

---

## Key Features

- **Multi-source aggregation**: 100+ job sources (LinkedIn, Indeed, etc.) via Apify API
- **3-tier deduplication**: URL → composite key → fuzzy match (~98% accuracy)
- **LLM-based scoring**: 9-factor weighted rubric evaluating skills, experience, and fit
- **Feedback-driven calibration**: Human verdicts improve AI scoring over time (62% → 87% correlation)
- **Priority ranking**: P1/P2/P3 classification for application prioritization
- **Dual output**: CLI reports + FastAPI web dashboard
- **Semaphore-bounded concurrency**: 5 parallel LLM requests with rate limit handling

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/DhruvLadani18/smart-job-tracker.git
cd smart-job-tracker

# Install dependencies
uv sync --all-extras

# Configure API keys
cp .env.example .env
# Edit .env with your APIFY_API_TOKEN and GEMINI_API_KEY

# Run full pipeline
uv run job-scraper run

# Launch the FastAPI dashboard
uv run --extra web uvicorn job_scraper.web.app:app --reload

# Run the test suite
uv run --extra dev pytest
```

---

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

---

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

---

## Tech Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.12+ |
| Async Runtime | asyncio + asyncio.Semaphore |
| LLM API | Google Gemini Flash Preview |
| Job Data | Apify API (100+ sources) |
| Database | SQLite + SQLAlchemy 2.0 |
| CLI | argparse with subcommands |
| Web UI | FastAPI + vanilla HTML/CSS/JS |
| Packaging | uv + pyproject.toml |
| Containerization | Docker (multi-stage) + docker-compose |
| Testing | pytest + pytest-asyncio + pytest-cov |

---

## Project Structure

```
smart-job-tracker/
├── src/job_scraper/
│   ├── __init__.py         # Package exports
│   ├── apify_scraper.py    # Apify API wrapper with country normalization
│   ├── gemini_scorer.py    # LLM scoring with structured JSON schemas
│   ├── orchestrator.py     # Main pipeline: search → dedupe → score → save
│   ├── models.py           # SQLAlchemy schema with backfill migrations
│   ├── resume_loader.py    # Multi-format resume parsing (PDF/TXT/JSON)
│   ├── metrics.py          # Pipeline observability
│   └── cli.py              # CLI entry point with 8 subcommands
├── src/job_scraper/web/
│   ├── __init__.py
│   ├── app.py              # FastAPI dashboard API
│   └── dashboard.html      # Static HTML dashboard
├── tests/                  # pytest test suite
├── .github/workflows/
│   ├── ci.yml              # Automated testing on push/PR
│   └── cron.yml            # Daily scheduled job scraper runs
├── .env.example            # Environment template
├── pyproject.toml          # Package metadata + uv lock
├── Dockerfile              # Multi-stage production build
├── docker-compose.yml      # Container orchestration
└── Documentation/
    ├── README.md           # Extended feature documentation
    ├── CASE_STUDY.md       # Architecture decisions and trade-offs
    ├── DEPLOYMENT.md       # Cloud deployment guide (Railway, Render, etc.)
    └── QUICKSTART.md       # Step-by-step setup instructions
```

---

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Jobs processed per run | 50-200 |
| API calls per run | 5-10 (Apify) + N (Gemini) |
| Deduplication accuracy | ~98% (empirical) |
| Batch concurrency | 5 parallel Gemini requests |
| Database size after 30 days | ~500 jobs, 2 MB |
| LLM response time | <2s (Gemini Flash) |

---

## Database Schema

```sql
jobs (
  -- Scraping metadata
  id, scraped_at, first_seen_at, last_seen_at, seen_count,
  source, apify_run_id, dedupe_key, last_search_query,
  
  -- Job details
  title, company, location, remote, posted_date, salary, url,
  jd_raw, jd_summary,
  
  -- AI scoring
  fit_score, interview_chance, apply_priority, ai_model,
  why_match, biggest_gap, resume_tweaks, why_company_angle,
  
  -- Application tracking
  should_apply, status, notes,
  human_verdict, human_score, human_feedback, feedback_updated_at,
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

---

## Key Engineering Decisions

| Challenge | Solution | Why |
|-----------|----------|-----|
| **Duplicate jobs across runs** | 3-tier deduplication (URL → dedupe_key → fuzzy match) | Apify returns same jobs with different IDs; need stable identity across 30+ day windows |
| **API rate limiting** | Semaphore-bounded concurrency (5 parallel) + 1s batch delays | Gemini has undocumented rate limits; found through empirical testing |
| **Cold start scoring** | Human feedback history injected into prompt context | LLM has no memory; calibrate by showing past human decisions |
| **Multi-source normalization** | `_normalize_job()` abstraction layer | Different Apify actors return different field names |
| **Schema evolution** | `_ensure_sqlite_schema()` backfill on init | Avoid breaking existing databases when adding columns |

---

## Lessons Learned

1. **Deduplication is harder than expected**: URLs change, titles get reformatted, companies have alternate names. A 3-tier strategy (URL → composite key → fuzzy match) is necessary for production use.

2. **LLM consistency varies**: Gemini Flash preview is fast but occasionally returns malformed JSON. Structured output mode + retry logic is essential.

3. **Human feedback is gold**: The first run's scoring is generic. After 10-20 human verdicts, the calibration improves significantly because the LLM sees patterns in what *you* value.

4. **Async concurrency needs bounds**: Unbounded `asyncio.gather()` on 100 jobs will hit rate limits. Semaphore-bounded batches with delays are more reliable than aggressive parallelism.

---

## Deployment Options

See [DEPLOYMENT.md](Documentation/DEPLOYMENT.md) for detailed guides:

1. **Docker**: `docker-compose up -d`
2. **Railway**: One-click deploy with environment variables
3. **Render**: Free tier with scheduled tasks
4. **GitHub Actions**: Daily cron job (see `.github/workflows/cron.yml`)

---

## Future Enhancements

- [ ] Background scheduler for automatic dashboard refreshes
- [ ] Email/Slack alerts for P1 jobs
- [ ] A/B testing for prompt variations
- [ ] Interview preparation module with company-specific questions
- [ ] RAG-based scoring for better context retention

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

*Built as a demonstration of production-ready async Python, LLM integration patterns, and feedback-driven AI systems.*
