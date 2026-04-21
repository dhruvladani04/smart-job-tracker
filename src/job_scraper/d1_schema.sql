-- D1 Database Schema for Job Tracker
-- Corresponds to models.py (Job, SearchQuery, Company)

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Scraping metadata
    scraped_at TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    seen_count INTEGER DEFAULT 1,
    source TEXT,
    apify_run_id TEXT,
    dedupe_key TEXT,
    last_search_query TEXT,

    -- Job details
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    remote INTEGER DEFAULT 0,
    posted_date TEXT,
    salary TEXT,
    url TEXT,

    -- Job description
    jd_raw TEXT,
    jd_summary TEXT,

    -- AI scoring
    fit_score INTEGER,
    interview_chance TEXT,
    apply_priority TEXT,
    ai_model TEXT,

    -- AI-generated insights
    why_match TEXT,
    biggest_gap TEXT,
    resume_tweaks TEXT,
    why_company_angle TEXT,

    -- Application tracking
    should_apply INTEGER DEFAULT 1,
    status TEXT DEFAULT 'new',
    notes TEXT,
    human_verdict TEXT,
    human_score INTEGER,
    human_feedback TEXT,
    feedback_updated_at TEXT,

    -- Timestamps
    applied_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(apply_priority);
CREATE INDEX IF NOT EXISTS idx_jobs_verdict ON jobs(human_verdict);
CREATE INDEX IF NOT EXISTS idx_jobs_fit_score ON jobs(fit_score);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe_key ON jobs(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_name TEXT,
    keywords TEXT,
    location TEXT,
    experience_level TEXT,
    date_posted TEXT,
    "limit" INTEGER DEFAULT 100,

    -- Execution tracking
    last_run TEXT,
    jobs_found INTEGER DEFAULT 0,
    apify_run_id TEXT,

    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_search_queries_last_run ON search_queries(last_run);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    industry TEXT,
    size TEXT,
    stage TEXT,

    -- Tracking
    total_jobs INTEGER DEFAULT 0,
    applications INTEGER DEFAULT 0,
    interviews INTEGER DEFAULT 0,

    -- Notes
    target_company INTEGER DEFAULT 0,
    notes TEXT,

    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_target ON companies(target_company);
