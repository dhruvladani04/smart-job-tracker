"""
Tests for database models and schema.
"""
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_scraper.models import (
    Base,
    Job,
    SearchQuery,
    Company,
    init_db,
    _ensure_sqlite_schema,
)


@pytest.fixture
def test_session(test_db_path: str):
    """Create a test database session."""
    engine = create_engine(test_db_path.replace("sqlite:///", "sqlite:///"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestJobModel:
    """Tests for Job model."""

    def test_create_job(self, test_session):
        """Test creating a basic job."""
        job = Job(
            title="Software Engineer",
            company="TechCorp",
            location="Remote",
            url="https://example.com/job/1",
        )
        test_session.add(job)
        test_session.commit()

        retrieved = test_session.query(Job).filter(Job.id == job.id).first()
        assert retrieved is not None
        assert retrieved.title == "Software Engineer"
        assert retrieved.company == "TechCorp"
        assert retrieved.location == "Remote"
        assert retrieved.status == "new"
        assert retrieved.should_apply == True

    def test_job_repr(self, test_session):
        """Test job string representation."""
        job = Job(
            title="Product Manager",
            company="StartupXYZ",
            location="Bangalore",
        )
        test_session.add(job)
        test_session.commit()

        assert "Product Manager" in repr(job)
        assert "StartupXYZ" in repr(job)

    def test_job_scoring_fields(self, test_session):
        """Test job scoring field defaults and values."""
        job = Job(
            title="Senior PM",
            company="BigTech",
            fit_score=85,
            interview_chance="High",
            apply_priority="P1",
            should_apply=True,
        )
        test_session.add(job)
        test_session.commit()

        assert job.fit_score == 85
        assert job.interview_chance == "High"
        assert job.apply_priority == "P1"
        assert job.should_apply == True

    def test_job_human_feedback(self, test_session):
        """Test human feedback fields."""
        job = Job(
            title="APM",
            company="GrowthCo",
            human_verdict="apply",
            human_score=90,
            human_feedback="Great culture fit, strong growth potential",
        )
        test_session.add(job)
        test_session.commit()

        assert job.human_verdict == "apply"
        assert job.human_score == 90
        assert "culture fit" in job.human_feedback

    def test_job_deduplication_fields(self, test_session):
        """Test deduplication-related fields."""
        job = Job(
            title="Duplicate Test",
            company="TestCorp",
            dedupe_key="duplicate test|testcorp|remote",
            first_seen_at=datetime(2024, 1, 1),
            last_seen_at=datetime(2024, 1, 15),
            seen_count=5,
        )
        test_session.add(job)
        test_session.commit()

        assert job.dedupe_key == "duplicate test|testcorp|remote"
        assert job.seen_count == 5
        assert job.first_seen_at < job.last_seen_at


class TestSearchQueryModel:
    """Tests for SearchQuery model."""

    def test_create_search_query(self, test_session):
        """Test creating a search query record."""
        query = SearchQuery(
            query_name="PM Remote",
            keywords="Product Manager",
            location="Remote",
            limit=50,
        )
        test_session.add(query)
        test_session.commit()

        retrieved = test_session.query(SearchQuery).first()
        assert retrieved.query_name == "PM Remote"
        assert retrieved.keywords == "Product Manager"
        assert retrieved.limit == 50
        assert retrieved.jobs_found == 0

    def test_search_query_execution_tracking(self, test_session):
        """Test tracking search execution."""
        query = SearchQuery(
            query_name="AI PM India",
            keywords="AI Product Manager",
            location="India",
            jobs_found=25,
            apify_run_id="run_abc123",
        )
        test_session.add(query)
        test_session.commit()

        assert query.jobs_found == 25
        assert query.apify_run_id == "run_abc123"


class TestCompanyModel:
    """Tests for Company model."""

    def test_create_company(self, test_session):
        """Test creating a company record."""
        company = Company(
            name="Stripe",
            industry="Fintech",
            size="1000-5000",
            stage="Private",
        )
        test_session.add(company)
        test_session.commit()

        retrieved = test_session.query(Company).first()
        assert retrieved.name == "Stripe"
        assert retrieved.industry == "Fintech"
        assert retrieved.total_jobs == 0
        assert retrieved.applications == 0

    def test_company_unique_constraint(self, test_session):
        """Test company name uniqueness."""
        company1 = Company(name="UniqueCorp", industry="Tech")
        test_session.add(company1)
        test_session.commit()

        # Attempting to add duplicate should fail
        company2 = Company(name="UniqueCorp", industry="Other")
        test_session.add(company2)
        with pytest.raises(Exception):
            test_session.commit()

    def test_company_tracking_fields(self, test_session):
        """Test company tracking statistics."""
        company = Company(
            name="TargetCo",
            total_jobs=10,
            applications=5,
            interviews=2,
            target_company=True,
        )
        test_session.add(company)
        test_session.commit()

        assert company.total_jobs == 10
        assert company.applications == 5
        assert company.interviews == 2
        assert company.target_company == True


class TestInitDB:
    """Tests for database initialization."""

    def test_init_db_creates_tables(self, test_db_path: str):
        """Test that init_db creates all tables."""
        session = init_db(test_db_path)

        # Verify tables exist
        from sqlalchemy import inspect
        inspector = inspect(session.bind)
        tables = inspector.get_table_names()

        assert "jobs" in tables
        assert "search_queries" in tables
        assert "companies" in tables

    def test_init_db_returns_session(self, test_db_path: str):
        """Test that init_db returns a valid session."""
        session = init_db(test_db_path)

        # Should be able to query without errors
        result = session.query(Job).all()
        assert result == []


class TestEnsureSqliteSchema:
    """Tests for schema backfill functionality."""

    def test_backfill_missing_columns(self, test_db_path: str):
        """Test adding missing columns to existing table."""
        from sqlalchemy import text
        # Create table with minimal columns
        engine = create_engine(test_db_path.replace("sqlite:///", "sqlite:///"))

        # Create a minimal jobs table
        with engine.begin() as conn:
            Base.metadata.tables['jobs'].create(bind=conn)
            conn.execute(text("""
                INSERT INTO jobs (title, company, scraped_at)
                VALUES ('Test Job', 'Test Corp', datetime('now'))
            """))

        # Run schema backfill
        _ensure_sqlite_schema(engine)

        # Verify new columns were added
        from sqlalchemy import inspect
        inspector = inspect(engine)
        columns = {col['name'] for col in inspector.get_columns('jobs')}

        assert "first_seen_at" in columns
        assert "last_seen_at" in columns
        assert "dedupe_key" in columns
        assert "human_verdict" in columns
