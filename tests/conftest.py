"""
Pytest configuration and shared fixtures.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Set up test environment before imports
os.environ["APIFY_API_KEY"] = "test_apify_key"
os.environ["GEMINI_API_KEY"] = "test_gemini_key"


@pytest.fixture
def test_db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return f"sqlite:///{tmp_path}/test_job_tracker.db"


@pytest.fixture
def sample_job_data() -> dict:
    """Sample job data for testing."""
    return {
        "title": "Senior Product Manager",
        "company": "TechCorp Inc",
        "location": "San Francisco, CA",
        "remote": True,
        "posted_date": "2 days ago",
        "salary": "$150,000 - $200,000",
        "url": "https://example.com/jobs/123",
        "jd_raw": """
We are looking for a Senior Product Manager to lead our AI initiatives.

Requirements:
- 5+ years of product management experience
- Experience with AI/ML products
- Strong technical background
- Excellent communication skills

Responsibilities:
- Define product roadmap and strategy
- Work with engineering teams
- Conduct user research
- Analyze metrics and iterate
""",
    }


@pytest.fixture
def sample_resume_text() -> str:
    """Sample resume text for testing."""
    return """
Source: resume.pdf

EXPERIENCE

Senior Product Manager | WebMobi360 | Jan 2024 - Present
- Led development of AI-powered feature that increased user engagement by 35%
- Managed cross-functional team of 8 engineers and designers
- Defined product roadmap and prioritized backlog based on user feedback

Product Manager | TechStartup | Jun 2022 - Dec 2023
- Launched MVP from concept to 10,000 MAU in 6 months
- Conducted 50+ user interviews to inform product decisions
- Improved conversion rate by 22% through A/B testing

EDUCATION

B.Tech Computer Science | IIT Delhi | 2018-2022
GPA: 9.4/10

SKILLS
- Product Management: Roadmap planning, user research, A/B testing
- Technical: Python, SQL, LangChain, RAG pipelines
- AI/ML: LLM integration, prompt engineering, multi-agent systems
"""


@pytest.fixture
def sample_searches() -> list[dict]:
    """Sample search configurations."""
    return [
        {
            "query_name": "PM Remote",
            "keywords": "Product Manager",
            "location": "Remote",
            "actor": "all_jobs",
            "date_posted": "7 days",
            "limit": 10,
        },
        {
            "query_name": "AI PM",
            "keywords": "AI Product Manager",
            "location": "India",
            "actor": "all_jobs",
            "date_posted": "7 days",
            "limit": 10,
        },
    ]


@pytest.fixture
def mock_apify_client() -> MagicMock:
    """Create a mock Apify client."""
    mock = MagicMock()
    mock.actor = MagicMock()
    mock.run = MagicMock()
    mock.dataset = MagicMock()
    return mock


@pytest.fixture
def mock_gemini_client() -> MagicMock:
    """Create a mock Gemini client."""
    mock = MagicMock()
    mock.models = MagicMock()
    mock.models.generate_content = MagicMock()
    return mock


@pytest.fixture
def sample_score_response() -> dict:
    """Sample Gemini score response."""
    return {
        "fit_score": 82,
        "interview_chance": "High",
        "apply_priority": "P1",
        "should_apply": True,
        "why_match": "Strong technical background with AI/ML experience matches the role requirements.",
        "biggest_gap": "Only 3 years of PM experience vs 5+ years requested.",
        "resume_tweaks": [
            "Emphasize the AI-powered feature launch in current role",
            "Quantify impact of A/B testing improvements",
        ],
        "why_company_angle": "TechCorp's focus on AI initiatives aligns with candidate's recent experience.",
        "score_breakdown": {
            "role_title_relevance": 18,
            "skills_match": 16,
            "experience_match": 10,
            "product_ownership_fit": 8,
            "ai_technical_overlap": 9,
            "company_quality_stage": 8,
            "location_fit": 5,
            "salary_comp_visibility": 5,
            "resume_gap_severity": 3,
        },
    }
