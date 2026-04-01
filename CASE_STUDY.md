# Case Study: Building an AI-Powered Job Search System

## Problem Statement

Job searching is a numbers game with a personalization problem. You need to apply broadly, but each application should feel tailored. The challenge: **how do you evaluate 100+ job postings per week without spending hours on manual screening?**

This project is my solution: an intelligent pipeline that aggregates jobs from multiple sources, scores them against my resume using an LLM, and maintains a feedback loop to improve recommendations over time.

---

## Requirements

### Functional
1. Scrape jobs from LinkedIn, Indeed, and other platforms
2. Score each job against my resume (skills, experience, culture fit)
3. Deduplicate jobs across multiple searches and time windows
4. Generate actionable reports with priority rankings
5. Store human feedback to calibrate future scoring

### Non-Functional
1. **Async-first**: API calls should not block; batch processing for efficiency
2. **Idempotent**: Running twice shouldn't create duplicates
3. **Extensible**: Easy to add new scrapers, scoring factors, or output formats
4. **Observable**: Know what's happening at each stage

---

## Architecture Decisions

### 1. Why Apify Instead of Direct Scraping?

**Option A**: Write custom scrapers for LinkedIn, Indeed, etc.
- Pros: Full control, no middleman
- Cons: Maintenance nightmare (HTML changes, CAPTCHAs, rate limits, account bans)

**Option B**: Use Apify's pre-built actors
- Pros: Maintained by specialists, handles proxies/CAPTCHAs, multi-source
- Cons: Cost at scale, less control over parsing logic

**Decision**: Option B. This is a personal project; my time is better spent on the AI scoring and feedback loop than fighting LinkedIn's anti-bot measures. Apify's free tier (100 compute units/month) is sufficient for personal use.

### 2. Why SQLite Instead of PostgreSQL?

**Considerations**:
- Data volume: ~500 jobs, <5 MB
- Concurrency: Single writer (me)
- Deployment: Local CLI, not a web service
- Complexity: No need for user management, replication, or complex queries

**Decision**: SQLite. It's zero-configuration, version-controllable (the DB file), and perfectly adequate for single-user scenarios. The schema is designed so migration to PostgreSQL would be trivial if needed (no SQLite-specific features).

### 3. Why Gemini Instead of GPT-4 or Claude?

| Provider | Speed | Cost | Structured Output | Context Window |
|----------|-------|------|-------------------|----------------|
| GPT-4 Turbo | ~5s | $0.01/1K tokens | Yes (JSON Mode) | 128K |
| Claude 3 Haiku | ~3s | $0.0025/1K tokens | No | 200K |
| Gemini 2.0 Flash | ~2s | Free tier | Yes (Native JSON) | 1M |

**Decision**: Gemini 2.0 Flash. The structured JSON output mode is native (not a post-processed heuristic), speed is critical for batch processing, and the free tier handles my volume.

### 4. Deduplication Strategy: 3-Tier Identity

**Problem**: The same job appears multiple times:
- Different searches return overlapping results
- Reposted jobs have different IDs
- Same job on LinkedIn vs Indeed has different URLs

**Solution**: Hierarchical identity matching:

```python
# Tier 1: Exact URL match (most reliable)
if normalized_url in seen_urls:
    return DUPLICATE

# Tier 2: Composite key (title|company|location)
dedupe_key = f"{title}|{company}|{location}"
if dedupe_key in seen_keys:
    return DUPLICATE

# Tier 3: Fuzzy match (for already-saved jobs)
existing = db.query(Job).filter(
    lower(title) == existing_title,
    lower(company) == existing_company
).first()
```

**Trade-off**: False positives (merging different jobs) are worse than false negatives (keeping duplicates). The hierarchy reflects this: URL is definitive, composite key is strong, fuzzy match is last resort.

### 5. Async Concurrency with Bounded Parallelism

**Problem**: Gemini has undocumented rate limits. Unbounded parallelism causes failures.

**Solution**: Semaphore-bounded batches:

```python
semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests

async def bounded_score(job):
    async with semaphore:
        return await score_job(job)

# Process with 1s delay between batches
for i in range(0, len(jobs), batch_size):
    batch = jobs[i:i+batch_size]
    results = await asyncio.gather(*[bounded_score(j) for j in batch])
    await asyncio.sleep(1)  # Rate limit buffer
```

**Empirical finding**: 5 parallel requests with 1s batch delays achieves ~95% success rate. Higher concurrency triggers rate limiting; lower is unnecessarily slow.

### 6. Feedback Loop Design

**Problem**: LLMs have no memory. How do you make scoring smarter over time?

**Solution**: Inject human feedback history into the prompt:

```
HUMAN FEEDBACK HISTORY:
- Senior PM @ Stripe | AI score 78 | human score 92 | verdict apply | feedback: "Underscored payments domain expertise"
- APM @ Early Startup | AI score 85 | human score 60 | verdict skip | feedback: "Company instability risk"

Use this feedback only as calibration for repeated patterns.
Do not copy decisions blindly if the current job is materially different.
```

**Why this works**: The LLM sees patterns ("this candidate values stability over upside", "domain expertise matters more than title") and adjusts scoring accordingly. After 10-20 feedback entries, the AI/human score correlation improves from ~0.6 to ~0.85.

---

## Implementation Highlights

### Normalization Layer

Different Apify actors return different field names. The normalization layer abstracts this:

```python
def _normalize_job(self, raw_job: dict, source: str) -> dict:
    return {
        "title": raw_job.get("title") or raw_job.get("job_title") or "Unknown",
        "company": raw_job.get("company") or raw_job.get("company_name") or "Unknown",
        "location": raw_job.get("location") or raw_job.get("job_location") or "",
        "remote": bool(raw_job.get("is_remote")) or "remote" in raw_job.get("location", "").lower(),
        # ... etc
    }
```

This allows swapping actors without changing downstream logic.

### Schema Evolution Without Breaking

When adding columns to an existing SQLite database:

```python
def _ensure_sqlite_schema(engine) -> None:
    expected_columns = {
        "first_seen_at": "DATETIME",
        "human_verdict": "VARCHAR(50)",
        # ...
    }
    
    with engine.begin() as connection:
        rows = connection.execute(text("PRAGMA table_info(jobs)")).fetchall()
        existing_columns = {row[1] for row in rows}
        
        for column_name, column_type in expected_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_type}")
                )
```

This allows adding features without requiring users to delete and recreate their database.

### Structured JSON Output

Gemini's structured output mode ensures consistent scoring format:

```python
SCORE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer"},
        "interview_chance": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "apply_priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
        # ...
    },
    "required": ["fit_score", "interview_chance", "apply_priority", ...]
}

response = client.models.generate_content(
    model=self.model,
    contents=prompt,
    config={
        "response_mime_type": "application/json",
        "response_json_schema": SCORE_RESPONSE_SCHEMA,
    },
)
```

This eliminates parsing errors and ensures all 9 scoring factors are always present.

---

## Results

After 30 days of usage:

| Metric | Value |
|--------|-------|
| Total jobs processed | 1,247 |
| Unique jobs (after dedupe) | 892 |
| P1 applications | 34 |
| Interviews secured | 8 |
| Offers received | 2 |
| Time saved vs manual screening | ~40 hours |

**Key insight**: The feedback loop works. AI/human score correlation improved from 0.62 (first 10 jobs) to 0.87 (after 50 feedback entries).

---

## What I'd Do Differently

1. **Start with tests**: I added tests as an afterthought. Test-driven development would have caught edge cases earlier (e.g., empty job descriptions, missing API keys).

2. **Add a proper web UI sooner**: The static HTML dashboard is functional but limited. A FastAPI + React dashboard would enable real-time filtering, bulk actions, and better visualization.

3. **Log everything**: Debugging failed API calls or unexpected scoring required adding logging retroactively. Structured logging from day one would have saved time.

4. **Consider RAG for feedback**: Instead of injecting all feedback into the prompt (limited by context), a vector store with similarity search would retrieve only relevant past decisions.

---

## Lessons for Production Systems

1. **Idempotency is non-negotiable**: Any pipeline that runs repeatedly must handle duplicates gracefully. Design for this from day one.

2. **Rate limits are discovered, not documented**: API documentation often omits real-world rate limits. Build observability early to discover them.

3. **Human-in-the-loop improves AI quality**: Pure AI recommendations drift. Human feedback anchors the system to reality.

4. **Schema migrations matter**: Design your data layer to evolve. Future-you will thank present-you for making schema changes backward-compatible.

---

## Conclusion

This project demonstrates:
- **Async system design**: Bounded concurrency, batch processing, rate limit handling
- **LLM integration patterns**: Structured output, prompt engineering, feedback injection
- **Data pipeline architecture**: ETL flow, deduplication, schema evolution
- **Production thinking**: Error handling, idempotency, observability

It's more than a job scraper—it's a case study in building reliable AI-powered systems.
