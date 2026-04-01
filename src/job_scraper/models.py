"""
Database models for job tracker.
Schema from Step 10 of the prompt guide.
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    Text, Boolean, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class Job(Base):
    """Main job listing table."""
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Scraping metadata
    scraped_at = Column(DateTime, default=datetime.utcnow)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    seen_count = Column(Integer, default=1)
    source = Column(String(100))  # LinkedIn, Indeed, etc.
    apify_run_id = Column(String(200))
    dedupe_key = Column(String(500), index=True)
    last_search_query = Column(String(200))

    # Job details
    title = Column(String(300), nullable=False)
    company = Column(String(200), nullable=False)
    location = Column(String(200))
    remote = Column(Boolean, default=False)
    posted_date = Column(String(50))  # "2 days ago", "1 week ago"
    salary = Column(String(200))
    url = Column(Text)

    # Job description
    jd_raw = Column(Text)  # Full job description
    jd_summary = Column(Text)  # AI-generated summary

    # AI scoring
    fit_score = Column(Integer)  # 0-100
    interview_chance = Column(String(20))  # Low/Medium/High
    apply_priority = Column(String(20))  # P1/P2/P3
    ai_model = Column(String(100))

    # AI-generated insights
    why_match = Column(Text)
    biggest_gap = Column(Text)
    resume_tweaks = Column(Text)  # JSON array of suggested edits
    why_company_angle = Column(Text)

    # Application tracking
    should_apply = Column(Boolean, default=True)  # AI recommendation
    status = Column(String(50), default="new")  # new, reviewed, applied, rejected, interview, offer
    notes = Column(Text)
    human_verdict = Column(String(50))  # apply, skip, save, unsure
    human_score = Column(Integer)
    human_feedback = Column(Text)
    feedback_updated_at = Column(DateTime)

    # Timestamps
    applied_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Job(id={self.id}, title='{self.title}', company='{self.company}')>"


class SearchQuery(Base):
    """Track search queries run against Apify."""
    __tablename__ = "search_queries"

    id = Column(Integer, primary_key=True)
    query_name = Column(String(200))  # e.g., "APM Remote"
    keywords = Column(String(500))
    location = Column(String(200))
    experience_level = Column(String(100))
    date_posted = Column(String(50))  # "1 days", "7 days", etc.
    limit = Column(Integer, default=100)

    # Execution tracking
    last_run = Column(DateTime)
    jobs_found = Column(Integer, default=0)
    apify_run_id = Column(String(200))

    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<SearchQuery(query_name='{self.query_name}')>"


class Company(Base):
    """Company-level tracking for analytics."""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), unique=True, nullable=False)
    industry = Column(String(100))
    size = Column(String(50))  # "10-50", "100-500", etc.
    stage = Column(String(50))  # "Seed", "Series A", "Public", etc.

    # Tracking
    total_jobs = Column(Integer, default=0)
    applications = Column(Integer, default=0)
    interviews = Column(Integer, default=0)

    # Notes
    target_company = Column(Boolean, default=False)  # Priority company
    notes = Column(Text)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Company(name='{self.name}')>"


def init_db(database_url: str = "sqlite:///job_tracker.db"):
    """Initialize database and return session."""
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)
    _ensure_sqlite_schema(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _ensure_sqlite_schema(engine) -> None:
    """Best-effort schema backfill for existing SQLite databases."""
    if engine.dialect.name != "sqlite":
        return

    expected_columns = {
        "first_seen_at": "DATETIME",
        "last_seen_at": "DATETIME",
        "seen_count": "INTEGER DEFAULT 1",
        "dedupe_key": "VARCHAR(500)",
        "last_search_query": "VARCHAR(200)",
        "ai_model": "VARCHAR(100)",
        "human_verdict": "VARCHAR(50)",
        "human_score": "INTEGER",
        "human_feedback": "TEXT",
        "feedback_updated_at": "DATETIME",
    }

    with engine.begin() as connection:
        rows = connection.execute(text("PRAGMA table_info(jobs)")).fetchall()
        existing_columns = {row[1] for row in rows}

        for column_name, column_type in expected_columns.items():
            if column_name in existing_columns:
                continue
            connection.execute(
                text(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_type}")
            )
