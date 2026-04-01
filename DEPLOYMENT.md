# Deployment Guide

This guide covers deploying the Job Search Orchestrator to cloud platforms for automated daily runs.

## Prerequisites

- Docker installed locally
- API keys for Apify and Gemini
- Account on target platform (Render, Railway, or GitHub)

---

## Option 1: Docker (Local or Self-Hosted)

### Quick Start

```bash
# Build the image
docker build -t job-scraper .

# Run with environment variables
docker run -it \
  -e APIFY_API_KEY=your_key \
  -e GEMINI_API_KEY=your_key \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/metrics:/app/metrics \
  job-scraper run
```

### Using Docker Compose

```bash
# Create .env file
echo "APIFY_API_KEY=your_key" > .env
echo "GEMINI_API_KEY=your_key" >> .env

# Run with docker-compose
docker-compose up job-scraper

# Run with scheduler (daily runs)
docker-compose --profile scheduled up scheduler
```

### Cron Scheduling (Linux/macOS)

Add to crontab for daily 9 AM runs:

```bash
crontab -e

# Add this line:
0 9 * * * docker run --rm -v /path/to/data:/app/data -e APIFY_API_KEY=xxx -e GEMINI_API_KEY=xxx job-scraper run >> /var/log/job-scraper.log 2>&1
```

---

## Option 2: Render (Free Tier)

Render offers free web services and cron jobs.

### Step 1: Prepare Repository

1. Push your code to GitHub
2. Ensure `Dockerfile` and `pyproject.toml` are in the root

### Step 2: Create Render Account

1. Go to [render.com](https://render.com)
2. Sign up with GitHub
3. Connect your repository

### Step 3: Create a Cron Job

1. Click **New +** → **Cron Job**
2. Configure:
   - **Name**: `job-scraper-daily`
   - **Docker Command**: `job-scraper run`
   - **Frequency**: `0 9 * * *` (daily at 9 AM UTC)
   - **Dockerfile Path**: `Dockerfile`

3. Add environment variables:
   - `APIFY_API_KEY`
   - `GEMINI_API_KEY`
   - `JOB_SCRAPER_DB=sqlite:///data/job_tracker.db`

4. Add persistent disk (for database):
   - **Mount Path**: `/app/data`
   - **Size**: 1 GB (free tier)

### Step 4: Deploy

Click **Create Cron Job**. Render will build and deploy.

---

## Option 3: Railway (Free Tier with Limits)

Railway offers easy deployment with GitHub integration.

### Step 1: Connect GitHub

1. Go to [railway.app](https://railway.app)
2. Sign in with GitHub
3. Click **New Project** → **Deploy from GitHub repo**

### Step 2: Configure

1. Select your repository
2. Add environment variables:
   ```
   APIFY_API_KEY=your_key
   GEMINI_API_KEY=your_key
   JOB_SCRAPER_DB=sqlite:///data/job_tracker.db
   ```

3. Add volume for persistence:
   - Go to **Volumes** tab
   - Add `/app/data` mount

### Step 3: Set Up Scheduling

Railway doesn't have native cron, but you can:

**Option A**: Use GitHub Actions (see below)

**Option B**: Deploy as a service with a loop:

```python
# Add a scheduled_run.py file
import asyncio
import time
from job_scraper.orchestrator import JobSearchOrchestrator

async def main():
    orchestrator = JobSearchOrchestrator()
    while True:
        await orchestrator.run_full_pipeline()
        await asyncio.sleep(86400)  # 24 hours

asyncio.run(main())
```

---

## Option 4: GitHub Actions (Free, 2000 minutes/month)

Automate runs using GitHub Actions cron.

### Step 1: Create Workflow File

Create `.github/workflows/job-scraper.yml`:

```yaml
name: Job Scraper Daily Run

on:
  schedule:
    - cron: '0 9 * * *'  # Daily at 9 AM UTC
  workflow_dispatch:  # Manual trigger

jobs:
  run-scraper:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: uv sync --all-extras

      - name: Run job scraper
        env:
          APIFY_API_KEY: ${{ secrets.APIFY_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: uv run job-scraper run

      - name: Commit and push results
        run: |
          git config --local user.email "action@github.com"
          git config --local user.name "GitHub Action"
          git add ranked_jobs.md job_tracker.md job_dashboard.html || true
          git commit -m "Update job tracker [skip ci]" || true
          git push
```

### Step 2: Add Secrets

1. Go to repository **Settings** → **Secrets and variables** → **Actions**
2. Add repository secrets:
   - `APIFY_API_KEY`
   - `GEMINI_API_KEY`

### Step 3: Enable Workflow

Workflows are enabled by default. Trigger manually via **Actions** tab.

---

## Option 4: VPS (DigitalOcean, EC2, etc.)

For full control, deploy to a VPS.

### Step 1: Set Up Server

```bash
# SSH into server
ssh user@your-server-ip

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Install docker-compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### Step 2: Deploy Application

```bash
# Clone repository
git clone https://github.com/your-username/job-scraper.git
cd job-scraper

# Create .env file
nano .env
# Add APIFY_API_KEY and GEMINI_API_KEY

# Start with docker-compose
docker-compose --profile scheduled up -d
```

### Step 3: Set Up Log Rotation

```bash
# Create logrotate config
sudo nano /etc/logrotate.d/job-scraper

# Add:
/var/log/job-scraper.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    create 0640 app app
}
```

---

## Monitoring and Alerts

### Health Checks

The Dockerfile includes a health check. Monitor with:

```bash
# Check container health
docker inspect --format='{{.State.Health.Status}}' job-scraper

# View logs
docker logs job-scraper

# View metrics
docker exec job-scraper cat /app/metrics/run_*.json
```

### Slack Alerts (Optional)

Add to your cron job or GitHub Actions:

```yaml
- name: Notify Slack on failure
  if: failure()
  uses: slackapi/slack-github-action@v1.24.0
  with:
    payload: |
      {
        "text": "Job Scraper failed: ${{ github.job }}"
      }
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

---

## Troubleshooting

### Container Exits Immediately

Check logs:
```bash
docker logs job-scraper
```

Common issues:
- Missing API keys
- Invalid database path
- Permission errors on mounted volumes

### Database Lock Errors

SQLite doesn't support concurrent writes. Ensure:
- Only one instance runs at a time
- Use `restart: "no"` in docker-compose to prevent auto-restart loops

### Rate Limit Errors

If hitting API rate limits:
- Increase batch delay in `orchestrator.py`
- Reduce concurrency in `gemini_scorer.py`
- Add retry logic with exponential backoff

---

## Cost Estimates

| Platform | Cost | Notes |
|----------|------|-------|
| Docker (local) | Free | Your electricity |
| GitHub Actions | Free | 2000 minutes/month, ~60 runs |
| Render Cron | ~$5/month | Basic plan for private repos |
| Railway | ~$5/month | Hobby plan |
| DigitalOcean Droplet | $6/month | Basic droplet |

---

## Next Steps

After deployment:

1. Verify first run completes successfully
2. Check generated reports in output directory
3. Set up monitoring/alerts
4. If deploying the dashboard service, install the `web` extra and run `uv run --extra web uvicorn job_scraper.web.app:app --host 0.0.0.0 --port 8000`
