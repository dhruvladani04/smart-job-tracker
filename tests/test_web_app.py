"""
Tests for the FastAPI dashboard application.
"""
from fastapi.testclient import TestClient

from job_scraper.models import Job, init_db
from job_scraper.web.app import create_app


def _seed_job(db_url: str, **overrides) -> int:
    db = init_db(db_url)
    payload = {
        "title": "Associate Product Manager",
        "company": "Acme",
        "location": "Remote",
        "fit_score": 88,
        "apply_priority": "P1",
        "should_apply": True,
        "status": "new",
        "human_verdict": None,
        "human_score": None,
        "human_feedback": None,
        "jd_raw": "Own roadmap and work cross-functionally.",
        "url": "https://example.com/jobs/apm",
    }
    payload.update(overrides)
    job = Job(**payload)
    db.add(job)
    db.commit()
    db.refresh(job)
    job_id = job.id
    db.close()
    return job_id


def test_jobs_endpoint_supports_search(monkeypatch, test_db_path: str):
    """Search filter should match title/company/location."""
    monkeypatch.setenv("JOB_SCRAPER_DB", test_db_path)
    _seed_job(test_db_path, title="Product Analyst", company="Northwind", location="Bangalore")
    _seed_job(test_db_path, title="Backend Engineer", company="Contoso", location="Pune")

    with TestClient(create_app()) as client:
        response = client.get("/api/jobs", params={"search": "Bangalore"})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Product Analyst"


def test_job_detail_preserves_false_should_apply(monkeypatch, test_db_path: str):
    """False AI recommendations should not be coerced to true."""
    monkeypatch.setenv("JOB_SCRAPER_DB", test_db_path)
    job_id = _seed_job(
        test_db_path,
        title="Senior Staff PM",
        should_apply=False,
        human_verdict="skip",
        human_score=32,
        human_feedback="Too senior for current profile.",
        notes="Save for later.",
    )

    with TestClient(create_app()) as client:
        response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["should_apply"] is False
    assert data["human_verdict"] == "skip"
    assert data["notes"] == "Save for later."


def test_feedback_and_status_updates_round_trip(monkeypatch, test_db_path: str):
    """Dashboard endpoints should update and persist human review state."""
    monkeypatch.setenv("JOB_SCRAPER_DB", test_db_path)
    job_id = _seed_job(test_db_path, status="reviewed")

    with TestClient(create_app()) as client:
        feedback_response = client.post(
            f"/api/jobs/{job_id}/feedback",
            json={
                "verdict": "apply",
                "score": 91,
                "feedback": "Strong PM fit with clear ownership scope.",
                "notes": "Apply this week.",
            },
        )
        status_response = client.post(
            f"/api/jobs/{job_id}/status",
            json={"status": "applied", "notes": "Applied via company site."},
        )
        detail_response = client.get(f"/api/jobs/{job_id}")

    assert feedback_response.status_code == 200
    assert status_response.status_code == 200
    assert detail_response.status_code == 200

    data = detail_response.json()
    assert data["status"] == "applied"
    assert data["human_verdict"] == "apply"
    assert data["human_score"] == 91
    assert data["human_feedback"] == "Strong PM fit with clear ownership scope."
    assert data["notes"] == "Applied via company site."
