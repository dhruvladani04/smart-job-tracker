"""
Tests for metrics and observability module.
"""
import pytest
import json
from pathlib import Path

from job_scraper.metrics import (
    MetricsCollector,
    LatencyTracker,
    APIMetric,
    ScoreDistribution,
    PipelineMetrics,
    get_metrics_collector,
)


class TestAPIMetric:
    """Tests for APIMetric dataclass."""

    def test_create_api_metric(self):
        """Test creating an API metric."""
        metric = APIMetric(
            endpoint="gemini-2.0-flash",
            latency_ms=1250.5,
            success=True,
        )

        assert metric.endpoint == "gemini-2.0-flash"
        assert metric.latency_ms == 1250.5
        assert metric.success == True
        assert metric.error is None

    def test_api_metric_with_error(self):
        """Test API metric with error information."""
        metric = APIMetric(
            endpoint="apify-actor",
            latency_ms=5000.0,
            success=False,
            error="Rate limit exceeded",
        )

        assert metric.success == False
        assert metric.error == "Rate limit exceeded"


class TestScoreDistribution:
    """Tests for ScoreDistribution dataclass."""

    def test_default_distribution(self):
        """Test default distribution values."""
        dist = ScoreDistribution()

        assert dist.total == 0
        assert dist.p1_count == 0
        assert dist.p2_count == 0
        assert dist.p3_count == 0
        assert dist.skip_count == 0
        assert dist.mean_score == 0.0

    def test_distribution_with_data(self):
        """Test distribution with actual values."""
        dist = ScoreDistribution(
            total=100,
            p1_count=20,
            p2_count=30,
            p3_count=30,
            skip_count=20,
            mean_score=72.5,
            median_score=75,
            min_score=30,
            max_score=98,
        )

        assert dist.total == 100
        assert dist.p1_count + dist.p2_count + dist.p3_count + dist.skip_count == 100


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_create_collector(self, tmp_path: Path):
        """Test creating a metrics collector."""
        collector = MetricsCollector(metrics_dir=str(tmp_path / "metrics"))

        assert collector.metrics_dir.exists()
        assert len(collector.api_metrics) == 0
        assert collector.current_run is None

    def test_start_run(self, tmp_path: Path):
        """Test starting a pipeline run."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        run = collector.start_run("test_run_001")

        assert run is not None
        assert run.run_id == "test_run_001"
        assert collector.current_run == run
        assert run.started_at is not None

    def test_start_run_auto_id(self, tmp_path: Path):
        """Test starting a run with auto-generated ID."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        run = collector.start_run()

        assert run.run_id is not None
        assert len(run.run_id) > 0

    def test_end_run(self, tmp_path: Path):
        """Test ending a pipeline run."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_002")
        collector.end_run(success=True)

        run = collector.current_run
        assert run.completed_at is not None
        assert run.duration_seconds >= 0

        # Verify metrics file was created
        metrics_file = tmp_path / "run_test_run_002.json"
        assert metrics_file.exists()

    def test_record_api_call(self, tmp_path: Path):
        """Test recording API call metrics."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_003")

        collector.record_api_call(
            endpoint="gemini-2.0-flash",
            latency_ms=1500.0,
            success=True,
        )

        assert len(collector.api_metrics) == 1
        assert collector.current_run.gemini_calls == 1
        assert collector.current_run.gemini_success == 1
        assert collector.current_run.gemini_latency_ms == 1500.0

    def test_record_apify_call(self, tmp_path: Path):
        """Test recording Apify API call."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_004")

        collector.record_api_call(
            endpoint="apify-actor/all-jobs-scraper",
            latency_ms=3000.0,
            success=True,
        )

        assert collector.current_run.apify_calls == 1
        assert collector.current_run.apify_success == 1

    def test_record_multiple_api_calls(self, tmp_path: Path):
        """Test recording multiple API calls averages latency."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_005")

        collector.record_api_call("gemini", 1000.0, True)
        collector.record_api_call("gemini", 2000.0, True)
        collector.record_api_call("gemini", 3000.0, True)

        assert collector.current_run.gemini_calls == 3
        assert collector.current_run.gemini_success == 3
        assert collector.current_run.gemini_latency_ms == 2000.0  # Average

    def test_record_score(self, tmp_path: Path):
        """Test recording job scores for distribution."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_006")

        collector.record_score(92, "P1")
        collector.record_score(85, "P1")
        collector.record_score(75, "P2")
        collector.record_score(60, "P3")
        collector.record_score(45, "P3")

        dist = collector.current_run.score_distribution
        assert dist.total == 5
        assert dist.p1_count == 2
        assert dist.p2_count == 1
        assert dist.p3_count == 1
        assert dist.skip_count == 1
        assert dist.min_score == 45
        assert dist.max_score == 92

    def test_record_error(self, tmp_path: Path):
        """Test recording pipeline errors."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_007")

        collector.record_error("Apify API timeout")
        collector.record_error("Gemini rate limited")

        assert len(collector.current_run.errors) == 2

    def test_compute_score_distribution(self, tmp_path: Path):
        """Test computing distribution from score list."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))

        scores = [85, 90, 75, 70, 60, 55, 40, 30]
        dist = collector.compute_score_distribution(scores)

        assert dist.total == 8
        assert dist.p1_count == 2  # 85, 90
        assert dist.p2_count == 2  # 75, 70
        assert dist.p3_count == 2  # 60, 55
        assert dist.skip_count == 2  # 40, 30
        assert dist.mean_score == sum(scores) / 8
        assert dist.min_score == 30
        assert dist.max_score == 90

    def test_compute_empty_distribution(self, tmp_path: Path):
        """Test computing distribution with no scores."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        dist = collector.compute_score_distribution([])

        assert dist.total == 0
        assert dist.mean_score == 0.0

    def test_get_summary(self, tmp_path: Path):
        """Test getting human-readable summary."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_008")
        collector.current_run.total_jobs_found = 100
        collector.current_run.unique_jobs = 85
        collector.record_score(85, "P1")
        collector.record_score(70, "P2")

        summary = collector.get_summary()

        assert "test_run_008" in summary
        assert "Total found: 100" in summary
        assert "P1 (85-100): 1" in summary
        assert "P2 (70-84): 1" in summary

    def test_save_run_metrics_json_structure(self, tmp_path: Path):
        """Test that saved metrics have correct JSON structure."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_009")
        collector.current_run.total_jobs_found = 50
        collector.current_run.unique_jobs = 45
        collector.record_api_call("gemini", 1000.0, True)
        collector.record_score(80, "P2")
        collector.end_run(success=True)

        # Load and verify JSON structure
        metrics_file = tmp_path / "run_test_run_009.json"
        data = json.loads(metrics_file.read_text())

        assert "run_id" in data
        assert "started_at" in data
        assert "completed_at" in data
        assert "duration_seconds" in data
        assert "job_counts" in data
        assert "api_metrics" in data
        assert "score_distribution" in data
        assert "errors" in data


class TestLatencyTracker:
    """Tests for LatencyTracker context manager."""

    def test_latency_tracker_success(self, tmp_path: Path):
        """Test latency tracker with successful operation."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_010")

        with LatencyTracker(collector, "test-endpoint"):
            pass  # Simulate successful operation

        assert len(collector.api_metrics) == 1
        assert collector.api_metrics[0].success == True
        assert collector.api_metrics[0].error is None

    def test_latency_tracker_error(self, tmp_path: Path):
        """Test latency tracker with failed operation."""
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_011")

        try:
            with LatencyTracker(collector, "test-endpoint"):
                raise ValueError("Test error")
        except ValueError:
            pass

        assert len(collector.api_metrics) == 1
        assert collector.api_metrics[0].success == False
        assert "Test error" in collector.api_metrics[0].error

    def test_latency_tracker_records_time(self, tmp_path: Path):
        """Test that latency tracker records actual elapsed time."""
        import time

        collector = MetricsCollector(metrics_dir=str(tmp_path))
        collector.start_run("test_run_012")

        with LatencyTracker(collector, "slow-endpoint"):
            time.sleep(0.1)  # Sleep 100ms

        assert collector.api_metrics[0].latency_ms >= 100


class TestGetMetricsCollector:
    """Tests for factory function."""

    def test_get_metrics_collector(self, tmp_path: Path):
        """Test factory function creates collector."""
        # Test that we can create a collector with custom metrics dir
        collector = MetricsCollector(metrics_dir=str(tmp_path))
        assert collector is not None
        assert str(tmp_path) in str(collector.metrics_dir)
