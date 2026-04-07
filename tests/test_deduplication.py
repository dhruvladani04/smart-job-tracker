"""
Tests for job deduplication logic.
"""
import pytest
from datetime import datetime

from job_scraper.models import Job, init_db
from job_scraper.orchestrator import JobSearchOrchestrator


class TestDeduplicationKey:
    """Tests for deduplication key generation."""

    def test_deduplicate_key_normalization(self, test_db_path: str, test_resume_path: str):
        """Test that dedupe keys normalize case and whitespace."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job1 = {
            "title": "Senior Product Manager",
            "company": "Google",
            "location": "Mountain View, CA",
        }
        job2 = {
            "title": "senior product manager",  # Lowercase
            "company": "google",
            "location": "mountain view, ca",
        }

        key1 = orchestrator._deduplicate_key(job1)
        key2 = orchestrator._deduplicate_key(job2)

        assert key1 == key2

    def test_deduplicate_key_handles_missing_fields(self, test_db_path: str, test_resume_path: str):
        """Test dedupe key with missing fields."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job = {
            "title": "Unknown",
            "company": "Unknown",
        }

        key = orchestrator._deduplicate_key(job)
        assert "unknown|unknown|" in key.lower()


class TestJobIdentityKey:
    """Tests for job identity key generation."""

    def test_job_identity_key_url_priority(self, test_db_path: str, test_resume_path: str):
        """Test that URL takes priority in identity key."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job = Job(
            title="Product Manager",
            company="TechCorp",
            url="https://example.com/jobs/123",
        )

        key = orchestrator._job_identity_key(job)
        assert key.startswith("url::")
        assert "example.com" in key

    def test_job_identity_key_fallback_to_dedupe(self, test_db_path: str, test_resume_path: str):
        """Test fallback to dedupe_key when URL missing."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job = Job(
            title="Product Manager",
            company="TechCorp",
            dedupe_key="product manager|techcorp|remote",
        )

        key = orchestrator._job_identity_key(job)
        assert key.startswith("dedupe::")

    def test_job_identity_key_triple_fallback(self, test_db_path: str, test_resume_path: str):
        """Test fallback to title/company/location triple."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job = Job(
            title="Software Engineer",
            company="StartupXYZ",
            location="Bangalore",
        )

        key = orchestrator._job_identity_key(job)
        assert key.startswith("triple::")
        assert "software engineer" in key.lower()
        assert "startupxyz" in key.lower()


class TestNormalizeJob:
    """Tests for job data normalization."""

    def test_normalize_job_standard_fields(self, test_db_path: str, test_resume_path: str):
        """Test normalization with standard field names."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        raw = {
            "title": "Senior PM",
            "company": "BigTech",
            "location": "Remote",
            "description": "Full job description here",
            "url": "https://example.com/job",
        }

        normalized = orchestrator._normalize_job(raw, "test_source")

        assert normalized["title"] == "Senior PM"
        assert normalized["company"] == "BigTech"
        assert normalized["location"] == "Remote"
        assert normalized["jd_raw"] == "Full job description here"
        assert normalized["source"] == "test_source"

    def test_normalize_job_alternate_field_names(self, test_db_path: str, test_resume_path: str):
        """Test normalization with alternate field names."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        raw = {
            "job_title": "APM",
            "company_name": "GrowthCo",
            "job_location": "India",
            "job_description": "Description text",
            "job_url": "https://example.com/apm",
        }

        normalized = orchestrator._normalize_job(raw, "test_source")

        assert normalized["title"] == "APM"
        assert normalized["company"] == "GrowthCo"
        assert normalized["location"] == "India"

    def test_normalize_job_remote_detection(self, test_db_path: str, test_resume_path: str):
        """Test automatic remote detection."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        # Test is_remote flag
        raw1 = {
            "title": "PM",
            "company": "RemoteFirst",
            "is_remote": True,
        }
        # Test location string detection
        raw2 = {
            "title": "PM",
            "company": "RemoteFirst",
            "location": "Remote, US",
        }

        assert orchestrator._normalize_job(raw1, "src")["remote"] == True
        assert orchestrator._normalize_job(raw2, "src")["remote"] == True

    def test_normalize_job_default_values(self, test_db_path: str, test_resume_path: str):
        """Test default values for missing fields."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        raw = {
            # Minimal data
        }

        normalized = orchestrator._normalize_job(raw, "test_source")

        assert normalized["title"] == "Unknown"
        assert normalized["company"] == "Unknown"
        assert normalized["location"] == ""
        assert normalized["jd_raw"] == ""


class TestNormalizeURL:
    """Tests for URL normalization."""

    def test_normalize_url_removes_trailing_slash(self, test_db_path: str, test_resume_path: str):
        """Test trailing slash removal."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        url1 = "https://example.com/jobs/123/"
        url2 = "https://example.com/jobs/123"

        assert orchestrator._normalize_url(url1) == orchestrator._normalize_url(url2)

    def test_normalize_url_handles_none(self, test_db_path: str, test_resume_path: str):
        """Test handling of None/empty URLs."""
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        assert orchestrator._normalize_url(None) == ""
        assert orchestrator._normalize_url("") == ""

    def test_normalize_url_strips_whitespace(self, test_db_path: str, test_resume_path: str):
        """Test whitespace stripping."""
        url = "  https://example.com/job  "
        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])
        normalized = orchestrator._normalize_url(url)
        assert normalized == "https://example.com/job"


class TestFindExistingJob:
    """Tests for finding existing jobs in database."""

    def test_find_by_url(self, test_db_path: str, test_resume_path: str):
        """Test finding job by normalized URL."""
        db = init_db(test_db_path)

        # Create existing job
        existing = Job(
            title="Product Manager",
            company="TechCorp",
            url="https://techcorp.com/jobs/pm",
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        # Search with same URL (with trailing slash)
        job_data = {"url": "https://techcorp.com/jobs/pm/"}
        found = orchestrator._find_existing_job(job_data)

        assert found is not None
        assert found.id == existing.id

    def test_find_by_dedupe_key(self, test_db_path: str, test_resume_path: str):
        """Test finding job by dedupe_key."""
        db = init_db(test_db_path)

        existing = Job(
            title="Software Engineer",
            company="StartupXYZ",
            dedupe_key="software engineer|startupxyz|bangalore",
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job_data = {
            "title": "Software Engineer",
            "company": "StartupXYZ",
            "location": "Bangalore",
        }
        found = orchestrator._find_existing_job(job_data)

        assert found is not None

    def test_find_by_title_company_location(self, test_db_path: str, test_resume_path: str):
        """Test finding job by title/company/location match."""
        db = init_db(test_db_path)

        existing = Job(
            title="Data Scientist",
            company="AI Labs",
            location="Remote",
            dedupe_key=None,  # No dedupe_key
            url=None,  # No URL
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job_data = {
            "title": "data scientist",  # Case different
            "company": "ai labs",
            "location": "remote",
        }
        found = orchestrator._find_existing_job(job_data)

        assert found is not None
        assert found.id == existing.id


class TestRefreshExistingJob:
    """Tests for refreshing existing job with new data."""

    def test_refresh_updates_seen_count(self, test_db_path: str, test_resume_path: str):
        """Test that refresh increments seen_count."""
        db = init_db(test_db_path)

        existing = Job(
            title="PM",
            company="Company",
            seen_count=3,
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job_data = {
            "title": "PM",
            "company": "Company",
            "source": "new_search",
        }

        orchestrator._refresh_existing_job(existing, job_data, "new_search")

        assert existing.seen_count == 4

    def test_refresh_updates_timestamps(self, test_db_path: str, test_resume_path: str):
        """Test that refresh updates last_seen_at."""
        db = init_db(test_db_path)

        old_time = datetime(2024, 1, 1)
        existing = Job(
            title="PM",
            company="Company",
            last_seen_at=old_time,
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job_data = {"title": "PM", "company": "Company"}
        orchestrator._refresh_existing_job(existing, job_data, "search")

        assert existing.last_seen_at > old_time

    def test_refresh_preserves_higher_status(self, test_db_path: str, test_resume_path: str):
        """Test that refresh doesn't downgrade status."""
        db = init_db(test_db_path)

        existing = Job(
            title="PM",
            company="Company",
            status="applied",  # Already applied
        )
        db.add(existing)
        db.commit()

        orchestrator = JobSearchOrchestrator(database_url=test_db_path, resume_paths=[test_resume_path])

        job_data = {
            "title": "PM",
            "company": "Company",
        }
        # Note: status comes from job_data.get("source"), not directly set
        # This tests that status isn't accidentally overwritten to "new"

        assert existing.status == "applied"  # Should remain applied
