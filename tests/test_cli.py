"""
Tests for CLI commands.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from io import StringIO
import sys

from job_scraper.cli import (
    main,
    cmd_run,
    cmd_search,
    cmd_score,
    cmd_init,
    cmd_list_jobs,
    cmd_update_status,
    cmd_feedback,
    cmd_dashboard,
)
from job_scraper.orchestrator import JobSearchOrchestrator


@pytest.fixture
def mock_orchestrator():
    """Create a mock orchestrator."""
    mock = MagicMock()
    mock.run_full_pipeline = AsyncMock(return_value={
        "success": True,
        "p1_jobs": 5,
        "new_jobs": 20,
        "existing_jobs": 10,
    })
    mock.generate_tracker_markdown = MagicMock(return_value="tracker.md")
    mock.generate_dashboard_html = MagicMock(return_value="dashboard.html")
    mock.merge_duplicate_jobs = MagicMock(return_value=0)
    return mock


@pytest.fixture
def mock_apify_scraper():
    """Create a mock Apify scraper."""
    mock = MagicMock()
    mock.search_jobs = AsyncMock(return_value=[
        {"title": "Job 1", "company": "Company A"},
        {"title": "Job 2", "company": "Company B"},
    ])
    return mock


@pytest.fixture
def mock_gemini_scorer():
    """Create a mock Gemini scorer."""
    mock = MagicMock()
    mock.score_batch = AsyncMock(return_value=[
        {
            "fit_score": 85,
            "apply_priority": "P1",
            "interview_chance": "High",
            "should_apply": True,
        },
        {
            "fit_score": 75,
            "apply_priority": "P2",
            "interview_chance": "Medium",
            "should_apply": True,
        },
    ])
    return mock


class TestCLIInitialization:
    """Tests for CLI initialization and help."""

    def test_main_no_command_shows_help(self, capsys):
        """Test that running without command shows help."""
        with patch('sys.argv', ['job-scraper']):
            result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "help" in captured.out.lower()


class TestCmdRun:
    """Tests for the run command."""

    def test_cmd_run_success(
        self,
        capsys,
        mock_orchestrator,
        monkeypatch,
    ):
        """Test successful pipeline run."""
        args = MagicMock()
        args.resume = None
        args.database = "sqlite:///test.db"
        args.model = "gemini-2.0-flash"
        args.searches = None
        args.no_report = False
        args.output = "ranked_jobs.md"
        args.tracker_output = "job_tracker.md"
        args.dashboard_output = "job_dashboard.html"

        with patch(
            'job_scraper.cli.JobSearchOrchestrator',
            return_value=mock_orchestrator,
        ):
            result = cmd_run(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Success" in captured.out
        assert "P1" in captured.out

    def test_cmd_run_failure(
        self,
        capsys,
        mock_orchestrator,
    ):
        """Test failed pipeline run."""
        mock_orchestrator.run_full_pipeline = AsyncMock(return_value={
            "success": False,
            "error": "API key missing",
        })

        args = MagicMock()
        args.resume = None
        args.database = "sqlite:///test.db"
        args.model = "gemini-2.0-flash"
        args.searches = None
        args.no_report = False
        args.output = "ranked_jobs.md"
        args.tracker_output = "job_tracker.md"
        args.dashboard_output = "job_dashboard.html"

        with patch(
            'job_scraper.cli.JobSearchOrchestrator',
            return_value=mock_orchestrator,
        ):
            result = cmd_run(args)

        assert result == 1
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()

    def test_cmd_run_with_custom_searches(
        self,
        mock_orchestrator,
        tmp_path,
    ):
        """Test run command with custom searches file."""
        searches_file = tmp_path / "searches.json"
        searches_file.write_text('[{"query_name": "Test"}]')

        args = MagicMock()
        args.resume = None
        args.database = "sqlite:///test.db"
        args.model = "gemini-2.0-flash"
        args.searches = str(searches_file)
        args.no_report = False
        args.output = "ranked_jobs.md"
        args.tracker_output = "job_tracker.md"
        args.dashboard_output = "job_dashboard.html"

        with patch(
            'job_scraper.cli.JobSearchOrchestrator',
            return_value=mock_orchestrator,
        ):
            result = cmd_run(args)

        assert result == 0
        mock_orchestrator.run_full_pipeline.assert_called_once()


class TestCmdSearch:
    """Tests for the search command."""

    def test_cmd_search_success(
        self,
        capsys,
        mock_apify_scraper,
    ):
        """Test successful job search."""
        args = MagicMock()
        args.name = "Test Search"
        args.keywords = "Product Manager"
        args.location = "Remote"
        args.actor = "all_jobs"
        args.date_posted = "7 days"
        args.limit = 10
        args.output = None

        with patch(
            'job_scraper.apify_scraper.ApifyScraper',
            return_value=mock_apify_scraper,
        ):
            result = cmd_search(args)

        # Note: cmd_search returns 0 directly, not async
        assert result == 0
        captured = capsys.readouterr()
        assert "Found 2 jobs" in captured.out


class TestCmdScore:
    """Tests for the score command."""

    def test_cmd_score_success(
        self,
        capsys,
        mock_gemini_scorer,
        tmp_path,
    ):
        """Test successful job scoring."""
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text('[{"title": "Job 1", "company": "Company A"}]')

        args = MagicMock()
        args.resume = None
        args.jobs = str(jobs_file)
        args.model = "gemini-2.0-flash"
        args.output = None

        with patch(
            'job_scraper.gemini_scorer.GeminiScorer',
            return_value=mock_gemini_scorer,
        ):
            with patch(
                'job_scraper.cli.load_resume_bundle',
                return_value={"resume_text": "Resume content"},
            ):
                result = cmd_score(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Top 10" in captured.out or "Scoring" in captured.out


class TestCmdListJobs:
    """Tests for the list command."""

    def test_cmd_list_no_jobs(self, capsys, test_db_path: str):
        """Test list when no jobs exist."""
        args = MagicMock()
        args.database = test_db_path
        args.status = None
        args.verdict = None
        args.include_hidden = False
        args.limit = 25

        result = cmd_list_jobs(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "No tracked jobs" in captured.out

    def test_cmd_list_with_status_filter(self, capsys, test_db_path: str):
        """Test list with status filter."""
        from job_scraper.models import init_db, Job

        db = init_db(test_db_path)
        job = Job(
            title="Test Job",
            company="Test Corp",
            status="applied",
        )
        db.add(job)
        db.commit()

        args = MagicMock()
        args.database = test_db_path
        args.status = "applied"
        args.verdict = None
        args.include_hidden = False
        args.limit = 25

        result = cmd_list_jobs(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Test Job" in captured.out
        assert "Test Corp" in captured.out


class TestCmdUpdateStatus:
    """Tests for the status command."""

    def test_cmd_update_status_success(self, capsys, test_db_path: str):
        """Test updating job status."""
        from job_scraper.models import init_db, Job

        db = init_db(test_db_path)
        job = Job(
            title="Test Job",
            company="Test Corp",
            status="new",
        )
        db.add(job)
        db.commit()
        job_id = job.id

        args = MagicMock()
        args.job_id = job_id
        args.status = "applied"
        args.notes = "Applied via test"
        args.database = test_db_path
        args.tracker_output = "tracker.md"
        args.dashboard_output = "dashboard.html"
        args.model = "gemini-2.0-flash"
        args.resume = None
        args.profile = None

        with patch('job_scraper.cli._refresh_tracker_outputs'):
            result = cmd_update_status(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Updated" in captured.out


class TestCmdFeedback:
    """Tests for the feedback command."""

    def test_cmd_feedback_success(self, capsys, test_db_path: str):
        """Test adding feedback to a job."""
        from job_scraper.models import init_db, Job

        db = init_db(test_db_path)
        job = Job(
            title="Test Job",
            company="Test Corp",
        )
        db.add(job)
        db.commit()
        job_id = job.id

        args = MagicMock()
        args.job_id = job_id
        args.verdict = "apply"
        args.score = 85
        args.feedback = "Great role fit"
        args.notes = None
        args.database = test_db_path
        args.tracker_output = "tracker.md"
        args.dashboard_output = "dashboard.html"
        args.model = "gemini-2.0-flash"
        args.resume = None
        args.profile = None

        with patch('job_scraper.cli._refresh_tracker_outputs'):
            result = cmd_feedback(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Saved feedback" in captured.out


class TestCmdDashboard:
    """Tests for the dashboard command."""

    def test_cmd_dashboard_success(
        self,
        capsys,
        mock_orchestrator,
    ):
        """Test dashboard generation."""
        args = MagicMock()
        args.resume = None
        args.database = "sqlite:///test.db"
        args.model = "gemini-2.0-flash"
        args.tracker_output = "tracker.md"
        args.dashboard_output = "dashboard.html"
        args.merge_duplicates = False

        with patch(
            'job_scraper.cli.JobSearchOrchestrator',
            return_value=mock_orchestrator,
        ):
            result = cmd_dashboard(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "Tracker ready" in captured.out
        assert "Dashboard ready" in captured.out
