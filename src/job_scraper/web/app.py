"""
FastAPI application for job tracker dashboard.
"""
import os
from pathlib import Path
from contextlib import asynccontextmanager
from statistics import median
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi import APIRouter
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from sqlalchemy import or_

from ..models import init_db, Job, SearchQuery, Company


# Database session dependency
def get_db():
    """Get database session."""
    db_url = os.getenv("JOB_SCRAPER_DB", "sqlite:///job_tracker.db")
    db = init_db(db_url)
    try:
        yield db
    finally:
        db.close()


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    app.state.db = init_db(os.getenv("JOB_SCRAPER_DB", "sqlite:///job_tracker.db"))
    yield
    # Shutdown
    app.state.db.close()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="Job Search Tracker",
        description="AI-powered job search dashboard with scoring and tracking",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    app.include_router(api_router, prefix="/api")
    app.include_router(pages_router)

    return app


# Pydantic models for API responses
class JobSummary(BaseModel):
    """Summary of a job for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    company: str
    location: str
    fit_score: Optional[int]
    apply_priority: Optional[str]
    status: str
    human_verdict: Optional[str]
    url: Optional[str]
    first_seen_at: datetime

class JobDetail(BaseModel):
    """Full job details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    company: str
    location: str
    remote: bool
    posted_date: str
    salary: str
    url: str
    jd_raw: str
    fit_score: Optional[int]
    interview_chance: Optional[str]
    apply_priority: Optional[str]
    should_apply: bool
    why_match: str
    biggest_gap: str
    resume_tweaks: str
    why_company_angle: str
    status: str
    human_verdict: Optional[str]
    human_score: Optional[int]
    human_feedback: Optional[str]
    notes: Optional[str]
    seen_count: int
    first_seen_at: datetime
    last_seen_at: datetime

class DashboardStats(BaseModel):
    """Dashboard statistics."""
    total_jobs: int
    new_jobs: int
    applied_jobs: int
    interview_jobs: int
    offer_jobs: int
    rejected_jobs: int
    p1_jobs: int
    p2_jobs: int
    mean_score: float
    median_score: float


class UpdateStatusRequest(BaseModel):
    """Request body for status update."""
    status: str = Field(..., description="New status value")
    notes: Optional[str] = None


class FeedbackRequest(BaseModel):
    """Request body for human feedback."""
    verdict: Optional[str] = Field(None, description="apply/skip/save/unsure")
    score: Optional[int] = Field(None, ge=0, le=100, description="Human score 0-100")
    feedback: Optional[str] = Field(None, description="Free-form feedback")
    notes: Optional[str] = None


class BulkActionRequest(BaseModel):
    """Request body for bulk actions."""
    job_ids: List[int]
    action: str  # "update_status", "add_verdict", "delete"
    value: Optional[str] = None


# API Router
api_router = APIRouter()
pages_router = APIRouter()
VALID_STATUSES = ["new", "reviewed", "applied", "skipped", "rejected", "interview", "offer", "archived"]
VALID_VERDICTS = ["apply", "skip", "save", "unsure"]
DASHBOARD_TEMPLATE = Path(__file__).with_name("dashboard.html")


@pages_router.get("/", response_class=HTMLResponse)
async def dashboard_home():
    """Serve the main dashboard HTML."""
    return FileResponse(DASHBOARD_TEMPLATE)


@api_router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(db=Depends(get_db)):
    """Get dashboard statistics."""
    total = db.query(Job).count()
    new_count = db.query(Job).filter(Job.status == "new").count()
    applied_count = db.query(Job).filter(Job.status == "applied").count()
    interview_count = db.query(Job).filter(Job.status == "interview").count()
    offer_count = db.query(Job).filter(Job.status == "offer").count()
    rejected_count = db.query(Job).filter(Job.status == "rejected").count()

    scores = db.query(Job.fit_score).filter(Job.fit_score.isnot(None)).all()
    score_values = [s[0] for s in scores] if scores else []

    p1_count = db.query(Job).filter(Job.apply_priority == "P1").count()
    p2_count = db.query(Job).filter(Job.apply_priority == "P2").count()

    if score_values:
        mean_score = sum(score_values) / len(score_values)
        median_score = median(score_values)
    else:
        mean_score = 0.0
        median_score = 0

    return DashboardStats(
        total_jobs=total,
        new_jobs=new_count,
        applied_jobs=applied_count,
        interview_jobs=interview_count,
        offer_jobs=offer_count,
        rejected_jobs=rejected_count,
        p1_jobs=p1_count,
        p2_jobs=p2_count,
        mean_score=round(mean_score, 1),
        median_score=median_score,
    )


@api_router.get("/jobs", response_model=List[JobSummary])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority P1/P2/P3"),
    verdict: Optional[str] = Query(None, description="Filter by human verdict"),
    min_score: Optional[int] = Query(None, ge=0, le=100, description="Minimum AI score"),
    search: Optional[str] = Query(None, description="Search title, company, or location"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db=Depends(get_db),
):
    """List jobs with optional filters."""
    query = db.query(Job)

    if status:
        query = query.filter(Job.status == status)
    if priority:
        query = query.filter(Job.apply_priority == priority)
    if verdict:
        query = query.filter(Job.human_verdict == verdict)
    if min_score is not None:
        query = query.filter(Job.fit_score >= min_score)
    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Job.title.ilike(search_term),
                Job.company.ilike(search_term),
                Job.location.ilike(search_term),
            )
        )

    # Order by score desc, then updated_at desc
    query = query.order_by(Job.fit_score.desc().nullslast(), Job.updated_at.desc())

    jobs = query.offset(offset).limit(limit).all()
    return [
        JobSummary(
            id=job.id,
            title=job.title,
            company=job.company,
            location=job.location or "",
            fit_score=job.fit_score,
            apply_priority=job.apply_priority,
            status=job.status or "new",
            human_verdict=job.human_verdict,
            url=job.url,
            first_seen_at=job.first_seen_at or datetime.utcnow(),
        )
        for job in jobs
    ]


@api_router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(job_id: int, db=Depends(get_db)):
    """Get full details for a specific job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetail(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location or "",
        remote=job.remote or False,
        posted_date=job.posted_date or "",
        salary=job.salary or "",
        url=job.url or "",
        jd_raw=job.jd_raw or "",
        fit_score=job.fit_score,
        interview_chance=job.interview_chance,
        apply_priority=job.apply_priority,
        should_apply=True if job.should_apply is None else job.should_apply,
        why_match=job.why_match or "",
        biggest_gap=job.biggest_gap or "",
        resume_tweaks=job.resume_tweaks or "",
        why_company_angle=job.why_company_angle or "",
        status=job.status or "new",
        human_verdict=job.human_verdict,
        human_score=job.human_score,
        human_feedback=job.human_feedback,
        notes=job.notes,
        seen_count=job.seen_count or 1,
        first_seen_at=job.first_seen_at or datetime.utcnow(),
        last_seen_at=job.last_seen_at or datetime.utcnow(),
    )


@api_router.post("/jobs/{job_id}/status")
async def update_job_status(
    job_id: int,
    request: UpdateStatusRequest,
    db=Depends(get_db),
):
    """Update job status."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {VALID_STATUSES}")

    job.status = request.status
    if request.notes:
        job.notes = request.notes
    if request.status == "applied" and not job.applied_at:
        job.applied_at = datetime.utcnow()

    db.commit()
    db.refresh(job)

    return {"success": True, "job_id": job_id, "new_status": request.status}


@api_router.post("/jobs/{job_id}/feedback")
async def add_feedback(
    job_id: int,
    request: FeedbackRequest,
    db=Depends(get_db),
):
    """Add human feedback for a job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if request.verdict:
        if request.verdict not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail=f"Invalid verdict. Must be one of: {VALID_VERDICTS}")
        job.human_verdict = request.verdict

    if request.score is not None:
        job.human_score = request.score

    if request.feedback:
        job.human_feedback = request.feedback

    if request.notes:
        job.notes = request.notes

    job.feedback_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)

    return {"success": True, "job_id": job_id}


@api_router.post("/jobs/bulk")
async def bulk_action(
    request: BulkActionRequest,
    db=Depends(get_db),
):
    """Perform bulk action on multiple jobs."""
    if not request.job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")

    jobs = db.query(Job).filter(Job.id.in_(request.job_ids)).all()
    if not jobs:
        raise HTTPException(status_code=404, detail="No jobs found for given IDs")

    if request.action == "update_status":
        if request.value not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status value")
        for job in jobs:
            job.status = request.value
            if request.value == "applied" and not job.applied_at:
                job.applied_at = datetime.utcnow()

    elif request.action == "add_verdict":
        if request.value not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="Invalid verdict value")
        for job in jobs:
            job.human_verdict = request.value

    elif request.action == "delete":
        for job in jobs:
            db.delete(job)

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")

    db.commit()

    return {"success": True, "affected_jobs": len(jobs)}


@api_router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, db=Depends(get_db)):
    """Delete a job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    db.delete(job)
    db.commit()

    return {"success": True, "job_id": job_id}


@api_router.get("/searches")
async def list_searches(db=Depends(get_db)):
    """List all search queries."""
    searches = db.query(SearchQuery).order_by(SearchQuery.last_run.desc()).all()
    return [
        {
            "id": sq.id,
            "query_name": sq.query_name,
            "keywords": sq.keywords,
            "location": sq.location,
            "last_run": sq.last_run.isoformat() if sq.last_run else None,
            "jobs_found": sq.jobs_found,
        }
        for sq in searches
    ]


@api_router.get("/companies")
async def list_companies(db=Depends(get_db)):
    """List all companies."""
    companies = db.query(Company).order_by(Company.total_jobs.desc()).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "industry": c.industry,
            "stage": c.stage,
            "total_jobs": c.total_jobs,
            "applications": c.applications,
            "interviews": c.interviews,
            "target_company": c.target_company,
        }
        for c in companies
    ]


# Create the app instance
app = create_app()
