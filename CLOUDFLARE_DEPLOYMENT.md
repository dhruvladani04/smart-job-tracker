# Cloudflare Deployment Guide (Free Tier)

This guide walks you through deploying your job tracker dashboard on Cloudflare for **$0/month**.

## Architecture

```
GitHub Actions (Python)          Cloudflare (JS/Edge)
─────────────────────            ────────────────────
scrape + Gemini scoring    →    API (Workers)  →  Dashboard (Pages)
save to job_tracker.json   →    D1 Database
push to repo               →    serve HTML/JS
```

## Prerequisites

- Cloudflare account (free)
- Wrangler CLI installed: `npm install -g wrangler`
- GitHub repository with the job-scraper code

---

## Step 1: Create D1 Database

```bash
# Login to Cloudflare
wrangler login

# Create a new D1 database
wrangler d1 create job-tracker-db

# Copy the database_id from the output and paste it into wrangler.toml
# Also update the name in wrangler.toml under [[d1_databases]]
```

Edit `wrangler.toml`:
```toml
[[d1_databases]]
binding = "JOB_TRACKER_DB"
database_name = "job-tracker-db"
database_id = "PASTE_YOUR_DATABASE_ID_HERE"  # from the command above
```

## Step 2: Initialize D1 Schema

```bash
# Create tables in your D1 database
wrangler d1 execute job-tracker-db --file=./src/job_scraper/d1_schema.sql --remote
```

> **Note:** The `--remote` flag applies to the production D1 database. For local development, omit `--remote`.

## Step 3: Deploy the Cloudflare Worker

```bash
# Deploy to Cloudflare Workers
wrangler deploy

# Your Worker will be available at:
# https://job-tracker-api.<your-subdomain>.workers.dev/api/
```

To find your Cloudflare subdomain:
1. Go to [dash.cloudflare.com](https://dash.cloudflare.com)
2. Click on your account → Workers & Pages
3. Your subdomain is shown at the top (e.g., `xyz.workers.dev`)

## Step 4: Deploy Dashboard to Cloudflare Pages

```bash
# Install the Cloudflare Pages plugin
npm install -g @cloudflare/pages-plugin_application-rules

# Or use the Pages dashboard:
# 1. Go to dash.cloudflare.com → Workers & Pages → Create application
# 2. Select Pages → Connect to GitHub
# 3. Select your repository
# 4. Set build command: (leave empty for static HTML)
# 5. Set output directory: `src/job_scraper/web`
# 6. Add environment variable: API_BASE = https://job-tracker-api.YOUR_SUBDOMAIN.workers.dev/api
```

Or deploy via Wrangler:

```bash
# Create pages project
wrangler pages project create job-tracker-dashboard

# Deploy the dashboard
wrangler pages deploy src/job_scraper/web --project-name=job-tracker-dashboard
```

**Important:** Set the `API_BASE` environment variable in Cloudflare Pages to your Worker URL:
- If using Pages dashboard: Settings → Environment Variables
- Value: `https://job-tracker-api.ABCD.workers.dev/api`

## Step 5: Configure GitHub Secrets & Variables

Go to your GitHub repository → Settings → Secrets and variables → Actions:

### Secrets (Repository Secrets)
You must set these secrets in your GitHub repository for the scraper to run correctly and sync with Cloudflare.

- `GEMINI_API_KEY` — your Gemini API key (Get from: https://aistudio.google.com/app/apikey).
- `APIFY_API_KEY` — your Apify API key. If you run out of free tier tokens, simply create a new free account on Apify, grab the new API Key, and update this repository secret.
- `CF_API_TOKEN` — Cloudflare API token (for automated syncing in GitHub Actions).
- `CF_API_BASE` — The base URL to your Cloudflare Worker (e.g. `https://job-tracker-api.YOUR_SUBDOMAIN.workers.dev/api`). Without this, the automatic sync will be skipped!

To create `CF_API_TOKEN`:
1. Go to [dash.cloudflare.com → API Tokens](https://dash.cloudflare.com/profile/api-tokens)
2. Create Custom Token with Edit permissions for Workers

### Variables (Repository Variables)
- `CF_ACCOUNT_ID` — Found in Cloudflare dashboard URL or via `wrangler whoami`
- `APIFY_DEFAULT_COUNTRY` (Optional) — Defaults to "India". Set this to another country (e.g., "United States") if you want the scraper to default to a different location.

## Step 6: Test the Full Flow Locally

```bash
# Run the scraper (this will also generate job_tracker.json)
uv run job-scraper run

# Verify JSON export was created
cat job_tracker.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"Exported {len(d['jobs'])} jobs")'

# Test Worker locally
wrangler dev --local

# In another terminal, test the API:
curl http://127.0.0.1:8787/api/stats
```

## Step 7: Initial Data Sync

The first time, you'll need to populate D1 with your existing jobs:

```bash
# Make sure job_tracker.json exists (from Step 6)
# The JSON is also tracked in git, so it's available in CI

# Trigger a manual workflow run:
# GitHub → Actions → "Daily Job Scraper Run" → Run workflow
```

Or sync directly via API once your Worker is deployed:

```bash
# Read the exported JSON and POST to sync endpoint
curl -X POST "https://job-tracker-api.YOUR_SUBDOMAIN.workers.dev/api/sync" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_CF_API_TOKEN" \
  --data @job_tracker.json
```

## Step 8: Verify Everything Works

1. Open your Cloudflare Pages URL (dashboard)
2. You should see stats and jobs loaded from D1
3. Try clicking a job → update status → refresh page → status persists
4. Go to GitHub Actions → run the scraper workflow manually
5. After it completes, refresh dashboard → new jobs appear

---

## How It Works

| Component | Platform | What it does |
|-----------|----------|--------------|
| **Scraper** | GitHub Actions | Runs every 2 days, scrapes jobs, Gemini scoring |
| **JSON Export** | GitHub Actions | Saves `job_tracker.json` to repo after each run |
| **API** | Cloudflare Workers | Serves job data from D1, handles updates |
| **Database** | Cloudflare D1 | Stores all jobs, persists updates |
| **Dashboard** | Cloudflare Pages | Serves HTML/JS, calls Worker API |

---

## Updating the Worker

```bash
# Edit src/cloudflare_worker.js
# Redeploy
wrangler deploy
```

## Updating the Dashboard

```bash
# Edit src/job_scraper/web/dashboard.html
# Redeploy
wrangler pages deploy src/job_scraper/web --project-name=job-tracker-dashboard
```

Or push to GitHub — if connected, Pages will rebuild automatically.

---

## Troubleshooting

### "Database not found" error
- Run `wrangler d1 execute job-tracker-db --file=./src/job_scraper/d1_schema.sql --remote` again
- Check `wrangler.toml` has correct `database_id`

### Dashboard shows "Failed to load"
- Check browser console for errors
- Verify `API_BASE` environment variable in Pages matches your Worker URL
- Test API directly: `curl https://job-tracker-api.YOUR_SUBDOMAIN.workers.dev/api/stats`

### No jobs showing in dashboard
- Run the scraper locally first: `uv run job-scraper run`
- Check `job_tracker.json` was created
- Manually POST to `/api/sync` with the JSON

### Rate limited
- The Worker has basic rate limiting (100 req/min per IP)
- For high traffic, consider Cloudflare Pro plan or optimize requests

---

## Cost Summary

| Service | Free Tier Limit | Cost |
|---------|-----------------|------|
| Cloudflare Workers | 100,000 req/day | $0 |
| Cloudflare D1 | 5GB storage, 10K reads/writes/day | $0 |
| Cloudflare Pages | 500 builds/month | $0 |
| GitHub Actions | 2,000 min/month | $0 |
| **Total** | | **$0/month** |
