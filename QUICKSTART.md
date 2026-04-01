# Quick Start Guide

## 1. Get API Keys

### Apify (Job Scraping)
1. Go to https://console.apify.com/
2. Sign up for free account
3. Navigate to **Settings → Integrations**
4. Copy your API key

### Gemini API
1. Go to https://aistudio.google.com/app/apikey
2. Sign in with Google
3. Create an API key
4. Copy the key

## 2. Configure

```bash
# Install all local dependencies first
uv sync --all-extras

# Copy example env file
cp .env.example .env

# Edit .env with your keys
# APIFY_API_KEY=your_key_here
# GEMINI_API_KEY=your_key_here
```

## 3. Add Your Resumes

This project now defaults to:
- `Dhruv_Ladani_Resume_PM.pdf`
- `Dhruv_Ladani_Resume_Tech.pdf`

You can also pass custom resume files with `--resume`.

## 4. Run

```bash
# Full pipeline (searches 5 queries, scores all jobs, generates report)
uv run job-scraper run

# Start the FastAPI dashboard locally
uv run --extra web uvicorn job_scraper.web.app:app --reload

# Run tests
uv run --extra dev pytest

# Single custom search
uv run job-scraper search "Product Manager" --location "Bangalore"

# Score existing jobs JSON
uv run job-scraper score --resume Dhruv_Ladani_Resume_PM.pdf --resume Dhruv_Ladani_Resume_Tech.pdf --jobs my_jobs.json
```

## 5. Check Results

After running:
- `ranked_jobs.md` - Markdown report with top jobs
- `job_tracker.db` - SQLite database with all jobs
- `job_scraper/` - Source code

## Default Searches

The pipeline runs these by default:

| Query | Keywords | Location |
|-------|----------|----------|
| APM Remote | Associate Product Manager | Remote |
| PM India | Product Manager | India |
| AI PM India | AI Product Manager | India |
| Product Analyst Bangalore | Product Analyst | Bangalore |
| Technical PM Remote | Technical Product Manager | Remote |

## Customizing Searches

Create `my_searches.json`:

```json
[
  {
    "query_name": "Startup PM",
    "keywords": "Product Manager",
    "location": "India",
    "actor": "all_jobs",
    "date_posted": "7 days",
    "limit": 50
  }
]
```

Run with: `uv run job-scraper run --searches my_searches.json`

## Troubleshooting

**No API key error**: Make sure `.env` file exists with both keys.

**No jobs found**: Try different keywords or check Apify actor availability.

**Rate limit errors**: The system batches requests. Wait a few minutes and retry.

## Next Steps

1. Review `ranked_jobs.md` for top jobs
2. Apply to P1 jobs within 24 hours
3. Use resume tweaks for each application
4. Track your applications in the database
