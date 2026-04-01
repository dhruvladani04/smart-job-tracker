"""
Metrics and observability module.
Tracks API latencies, success rates, score distributions, and pipeline performance.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class APIMetric:
    """Single API call metric."""
    endpoint: str
    latency_ms: float
    success: bool
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    error: Optional[str] = None


@dataclass
class ScoreDistribution:
    """Score distribution statistics."""
    total: int = 0
    p1_count: int = 0  # 85-100
    p2_count: int = 0  # 70-84
    p3_count: int = 0  # 55-69
    skip_count: int = 0  # <55
    mean_score: float = 0.0
    median_score: float = 0.0
    min_score: int = 100
    max_score: int = 0


@dataclass
class PipelineMetrics:
    """Full pipeline metrics."""
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: float = 0.0

    # Job counts
    total_jobs_found: int = 0
    unique_jobs: int = 0
    new_jobs: int = 0
    existing_jobs: int = 0
    already_scored: int = 0

    # API metrics
    apify_calls: int = 0
    apify_success: int = 0
    apify_latency_ms: float = 0.0
    gemini_calls: int = 0
    gemini_success: int = 0
    gemini_latency_ms: float = 0.0

    # Score distribution
    score_distribution: ScoreDistribution = field(default_factory=ScoreDistribution)

    # Errors
    errors: list[str] = field(default_factory=list)


class MetricsCollector:
    """
    Collects and aggregates metrics for the job search pipeline.
    """

    def __init__(self, metrics_dir: str = ".metrics"):
        self.metrics_dir = Path(metrics_dir)
        self.metrics_dir.mkdir(exist_ok=True)
        self.api_metrics: list[APIMetric] = []
        self.current_run: Optional[PipelineMetrics] = None

    def start_run(self, run_id: Optional[str] = None) -> PipelineMetrics:
        """Start a new pipeline run."""
        run_id = run_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.current_run = PipelineMetrics(
            run_id=run_id,
            started_at=datetime.utcnow().isoformat(),
        )
        return self.current_run

    def end_run(self, success: bool = True) -> None:
        """End the current pipeline run and save metrics."""
        if not self.current_run:
            return

        self.current_run.completed_at = datetime.utcnow().isoformat()
        start = datetime.fromisoformat(self.current_run.started_at)
        end = datetime.fromisoformat(self.current_run.completed_at)
        self.current_run.duration_seconds = (end - start).total_seconds()

        if success:
            self._save_run_metrics()

    def record_api_call(
        self,
        endpoint: str,
        latency_ms: float,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record a single API call metric."""
        metric = APIMetric(
            endpoint=endpoint,
            latency_ms=latency_ms,
            success=success,
            error=error,
        )
        self.api_metrics.append(metric)

        if self.current_run:
            if "apify" in endpoint.lower():
                self.current_run.apify_calls += 1
                if success:
                    self.current_run.apify_success += 1
                self.current_run.apify_latency_ms = (
                    (self.current_run.apify_latency_ms * (self.current_run.apify_calls - 1) + latency_ms)
                    / self.current_run.apify_calls
                )
            elif "gemini" in endpoint.lower():
                self.current_run.gemini_calls += 1
                if success:
                    self.current_run.gemini_success += 1
                self.current_run.gemini_latency_ms = (
                    (self.current_run.gemini_latency_ms * (self.current_run.gemini_calls - 1) + latency_ms)
                    / self.current_run.gemini_calls
                )

    def record_score(self, score: int, priority: str) -> None:
        """Record a job score for distribution tracking."""
        if not self.current_run:
            return

        dist = self.current_run.score_distribution
        dist.total += 1
        dist.min_score = min(dist.min_score, score)
        dist.max_score = max(dist.max_score, score)

        if score >= 85:
            dist.p1_count += 1
        elif score >= 70:
            dist.p2_count += 1
        elif score >= 55:
            dist.p3_count += 1
        else:
            dist.skip_count += 1

    def record_error(self, error: str) -> None:
        """Record a pipeline error."""
        if self.current_run:
            self.current_run.errors.append(error)

    def compute_score_distribution(self, scores: list[int]) -> ScoreDistribution:
        """Compute distribution statistics from a list of scores."""
        if not scores:
            return ScoreDistribution()

        sorted_scores = sorted(scores)
        n = len(scores)

        dist = ScoreDistribution(
            total=n,
            mean_score=sum(scores) / n,
            median_score=sorted_scores[n // 2],
            min_score=sorted_scores[0],
            max_score=sorted_scores[-1],
        )

        for score in scores:
            if score >= 85:
                dist.p1_count += 1
            elif score >= 70:
                dist.p2_count += 1
            elif score >= 55:
                dist.p3_count += 1
            else:
                dist.skip_count += 1

        return dist

    def _save_run_metrics(self) -> None:
        """Save run metrics to JSON file."""
        if not self.current_run:
            return

        filename = self.metrics_dir / f"run_{self.current_run.run_id}.json"

        data = {
            "run_id": self.current_run.run_id,
            "started_at": self.current_run.started_at,
            "completed_at": self.current_run.completed_at,
            "duration_seconds": self.current_run.duration_seconds,
            "job_counts": {
                "total_jobs_found": self.current_run.total_jobs_found,
                "unique_jobs": self.current_run.unique_jobs,
                "new_jobs": self.current_run.new_jobs,
                "existing_jobs": self.current_run.existing_jobs,
                "already_scored": self.current_run.already_scored,
            },
            "api_metrics": {
                "apify": {
                    "calls": self.current_run.apify_calls,
                    "success": self.current_run.apify_success,
                    "avg_latency_ms": round(self.current_run.apify_latency_ms, 2),
                    "success_rate": round(
                        self.current_run.apify_success / max(self.current_run.apify_calls, 1) * 100, 1
                    ),
                },
                "gemini": {
                    "calls": self.current_run.gemini_calls,
                    "success": self.current_run.gemini_success,
                    "avg_latency_ms": round(self.current_run.gemini_latency_ms, 2),
                    "success_rate": round(
                        self.current_run.gemini_success / max(self.current_run.gemini_calls, 1) * 100, 1
                    ),
                },
            },
            "score_distribution": {
                "total": self.current_run.score_distribution.total,
                "p1_count": self.current_run.score_distribution.p1_count,
                "p2_count": self.current_run.score_distribution.p2_count,
                "p3_count": self.current_run.score_distribution.p3_count,
                "skip_count": self.current_run.score_distribution.skip_count,
                "mean_score": round(self.current_run.score_distribution.mean_score, 1),
                "median_score": self.current_run.score_distribution.median_score,
                "min_score": self.current_run.score_distribution.min_score,
                "max_score": self.current_run.score_distribution.max_score,
            },
            "errors": self.current_run.errors,
        }

        filename.write_text(json.dumps(data, indent=2))

    def get_summary(self) -> str:
        """Get a human-readable summary of the current run."""
        if not self.current_run:
            return "No active run."

        run = self.current_run
        dist = run.score_distribution

        lines = [
            f"\n{'='*50}",
            f"Pipeline Run Summary: {run.run_id}",
            f"{'='*50}",
            f"Duration: {run.duration_seconds:.1f}s",
            "",
            "Job Counts:",
            f"  Total found: {run.total_jobs_found}",
            f"  Unique: {run.unique_jobs}",
            f"  New: {run.new_jobs}",
            f"  Existing: {run.existing_jobs}",
            "",
            "API Performance:",
            f"  Apify: {run.apify_success}/{run.apify_calls} ({run.apify_success/max(run.apify_calls,1)*100:.0f}%) - {run.apify_latency_ms:.0f}ms avg",
            f"  Gemini: {run.gemini_success}/{run.gemini_calls} ({run.gemini_success/max(run.gemini_calls,1)*100:.0f}%) - {run.gemini_latency_ms:.0f}ms avg",
            "",
            "Score Distribution:",
            f"  P1 (85-100): {dist.p1_count}",
            f"  P2 (70-84): {dist.p2_count}",
            f"  P3 (55-69): {dist.p3_count}",
            f"  Skip (<55): {dist.skip_count}",
            f"  Mean: {dist.mean_score:.1f}",
            f"  Median: {dist.median_score}",
            f"  Range: {dist.min_score}-{dist.max_score}",
        ]

        if run.errors:
            lines.extend(["", f"Errors ({len(run.errors)}):"])
            for error in run.errors[:5]:
                lines.append(f"  - {error}")
            if len(run.errors) > 5:
                lines.append(f"  ... and {len(run.errors) - 5} more")

        lines.append("")
        return "\n".join(lines)


class LatencyTracker:
    """Context manager for tracking API latencies."""

    def __init__(self, collector: MetricsCollector, endpoint: str):
        self.collector = collector
        self.endpoint = endpoint
        self.start_time: Optional[float] = None
        self.success: bool = False
        self.error: Optional[str] = None

    def __enter__(self) -> "LatencyTracker":
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed_ms = (time.time() - self.start_time) * 1000 if self.start_time else 0
        self.success = exc_type is None
        if exc_type:
            self.error = str(exc_val)
        self.collector.record_api_call(
            endpoint=self.endpoint,
            latency_ms=elapsed_ms,
            success=self.success,
            error=self.error,
        )


def get_metrics_collector() -> MetricsCollector:
    """Get or create the global metrics collector."""
    return MetricsCollector()
