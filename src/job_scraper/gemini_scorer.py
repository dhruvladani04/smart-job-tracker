"""
Gemini API module for job scoring and resume tailoring.
Uses the scoring rubric from Step 7 of the prompt guide.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from dotenv import load_dotenv
from google import genai

load_dotenv()


SCORE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer"},
        "interview_chance": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "apply_priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
        "should_apply": {"type": "boolean"},
        "why_match": {"type": "string"},
        "biggest_gap": {"type": "string"},
        "resume_tweaks": {"type": "array", "items": {"type": "string"}},
        "why_company_angle": {"type": "string"},
        "score_breakdown": {
            "type": "object",
            "properties": {
                "role_title_relevance": {"type": "integer"},
                "skills_match": {"type": "integer"},
                "experience_match": {"type": "integer"},
                "product_ownership_fit": {"type": "integer"},
                "ai_technical_overlap": {"type": "integer"},
                "company_quality_stage": {"type": "integer"},
                "location_fit": {"type": "integer"},
                "salary_comp_visibility": {"type": "integer"},
                "resume_gap_severity": {"type": "integer"},
            },
            "required": [
                "role_title_relevance",
                "skills_match",
                "experience_match",
                "product_ownership_fit",
                "ai_technical_overlap",
                "company_quality_stage",
                "location_fit",
                "salary_comp_visibility",
                "resume_gap_severity",
            ],
        },
    },
    "required": [
        "fit_score",
        "interview_chance",
        "apply_priority",
        "should_apply",
        "why_match",
        "biggest_gap",
        "resume_tweaks",
        "why_company_angle",
        "score_breakdown",
    ],
}

TAILOR_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "top_3_bullets": {"type": "array", "items": {"type": "string"}},
        "cover_note": {"type": "string"},
        "screening_questions": {"type": "array", "items": {"type": "string"}},
        "why_company_answer": {"type": "string"},
    },
    "required": [
        "top_3_bullets",
        "cover_note",
        "screening_questions",
        "why_company_answer",
    ],
}


class GeminiScorer:
    """
    Uses Gemini API to score jobs against resume content.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-3-flash-preview",
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. Set it in .env or pass to constructor."
            )
        self.model = model

    def _client(self) -> genai.Client:
        return genai.Client(api_key=self.api_key)

    def _build_score_prompt(
        self,
        resume_text: str,
        job: dict,
        feedback_context: str = "",
    ) -> str:
        """Build the prompt for job scoring."""
        feedback_section = ""
        if feedback_context:
            feedback_section = f"""
HUMAN FEEDBACK HISTORY:
{feedback_context}

Use this feedback only as calibration for repeated patterns.
Do not copy decisions blindly if the current job is materially different.
"""

        return f"""You are an expert job search copilot specializing in Product Management roles.
Score this job listing against the candidate's resume evidence and provide honest, actionable output.

RULES:
- Use only the evidence present in the provided resume sources.
- Do not invent experience, metrics, tools, or achievements.
- Be critical. The goal is interview probability, not flattery.
- Keep explanations concise and specific.

SCORING RUBRIC (Total = 100 points):
- Role title relevance: 20 pts
- Skills match: 20 pts
- Experience match: 15 pts
- Product ownership fit: 10 pts
- AI/technical overlap: 10 pts
- Company quality/stage: 10 pts
- Location fit: 5 pts
- Salary/comp visibility: 5 pts
- Resume gap severity: 5 pts

RESUME SOURCES:
{resume_text}
{feedback_section}

JOB LISTING:
Title: {job.get('title', 'Unknown')}
Company: {job.get('company', 'Unknown')}
Location: {job.get('location', 'Unknown')}
Posted: {job.get('posted_date', 'Unknown')}
Salary: {job.get('salary', 'Not disclosed')}
URL: {job.get('url', '')}

JOB DESCRIPTION:
{job.get('jd_raw', job.get('description', 'No description available'))}
"""

    def _build_tailor_prompt(
        self,
        resume_text: str,
        job: dict,
        score_result: dict,
    ) -> str:
        """Build the prompt for tailored resume content."""
        return f"""You are tailoring resume guidance for a single job application.

RULES:
- Use only facts from the provided resume sources.
- Do not invent tools, scope, titles, or outcomes.
- Mirror the job language where truthful.
- Keep output practical and concise.

RESUME SOURCES:
{resume_text}

JOB:
Title: {job.get('title', 'Unknown')}
Company: {job.get('company', 'Unknown')}
Description:
{job.get('jd_raw', '')[:4000]}

PREVIOUS SCORE:
{json.dumps(score_result, indent=2)}
"""

    def _generate_json(self, prompt: str, schema: dict) -> dict:
        """Generate structured JSON from Gemini."""
        client = self._client()
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": schema,
                },
            )
            response_text = response.text
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        if not response_text:
            raise RuntimeError("Gemini returned an empty response.")

        return json.loads(response_text)

    async def score_job(
        self,
        resume_text: str,
        job: dict,
        feedback_context: str = "",
    ) -> dict:
        """
        Score a single job against the resume content.
        """
        prompt = self._build_score_prompt(resume_text, job, feedback_context)
        return await asyncio.to_thread(
            self._generate_json,
            prompt,
            SCORE_RESPONSE_SCHEMA,
        )

    async def score_batch(
        self,
        resume_text: str,
        jobs: list[dict],
        feedback_context: str = "",
        concurrency: int = 5,
    ) -> list[dict]:
        """
        Score multiple jobs in batch.
        """

        async def score_with_index(idx: int, job: dict) -> tuple[int, dict]:
            try:
                result = await self.score_job(resume_text, job, feedback_context)
                return (idx, result)
            except Exception as e:
                print(f"Error scoring job {idx}: {e}")
                return (
                    idx,
                    {
                        "fit_score": 50,
                        "interview_chance": "Medium",
                        "apply_priority": "P3",
                        "should_apply": True,
                        "why_match": "Error during Gemini scoring.",
                        "biggest_gap": str(e),
                        "resume_tweaks": [],
                        "why_company_angle": "",
                        "score_breakdown": {
                            "role_title_relevance": 10,
                            "skills_match": 10,
                            "experience_match": 8,
                            "product_ownership_fit": 5,
                            "ai_technical_overlap": 5,
                            "company_quality_stage": 5,
                            "location_fit": 3,
                            "salary_comp_visibility": 2,
                            "resume_gap_severity": 2,
                        },
                    },
                )

        semaphore = asyncio.Semaphore(concurrency)

        async def bounded_score(idx: int, job: dict) -> tuple[int, dict]:
            async with semaphore:
                return await score_with_index(idx, job)

        tasks = [bounded_score(i, job) for i, job in enumerate(jobs)]
        results = await asyncio.gather(*tasks)
        results.sort(key=lambda x: x[0])
        return [result for _, result in results]

    async def tailor_resume(
        self,
        resume_text: str,
        job: dict,
        score_result: dict,
    ) -> dict:
        """
        Generate tailored resume bullets for a specific job.
        """
        prompt = self._build_tailor_prompt(resume_text, job, score_result)
        return await asyncio.to_thread(
            self._generate_json,
            prompt,
            TAILOR_RESPONSE_SCHEMA,
        )
