"""
Apify scraper module.
Fetches jobs from Apify actors (AI Job Finder, All Jobs Scraper, LinkedIn Jobs).
"""
import os
from typing import Optional

from apify_client import ApifyClientAsync
from dotenv import load_dotenv

load_dotenv()


class ApifyScraper:
    """
    Wrapper around Apify API for job scraping.

    Supported actors:
    - stefanie-rink/ai-job-finder: AI-powered search helper
    - agentx/all-jobs-scraper: Multi-source aggregator
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("APIFY_API_KEY")
        if not self.api_key:
            raise ValueError(
                "APIFY_API_KEY not found. Set it in .env or pass to constructor."
            )
        self.client = ApifyClientAsync(token=self.api_key)
        self.default_country = os.getenv("APIFY_DEFAULT_COUNTRY", "India")

    def _resolve_country_location(self, location: str) -> tuple[str, str, bool]:
        """Split free-form location into actor-friendly country/location fields."""
        clean = (location or "").strip()
        normalized = clean.lower()

        country_aliases = {
            "india": "India",
            "united states": "United States",
            "usa": "United States",
            "us": "United States",
            "united kingdom": "United Kingdom",
            "uk": "United Kingdom",
            "uae": "United Arab Emirates",
        }

        if not clean:
            return self.default_country, "", False

        if normalized == "remote":
            return self.default_country, "", True

        if normalized in country_aliases:
            return country_aliases[normalized], "", False

        return self.default_country, clean, False

    async def _call_actor(self, actor_id: str, run_input: dict) -> dict:
        """Run an Apify actor and wrap common errors with the actor slug."""
        try:
            run = await self.client.actor(actor_id).call(run_input=run_input)
        except Exception as exc:
            message = str(exc)
            if "Actor with this name was not found" in message:
                raise RuntimeError(
                    f"Actor '{actor_id}' was not found on Apify. "
                    "The actor slug in code is likely outdated."
                ) from exc
            raise

        return {
            "run_id": run["id"],
            "status": run["status"],
            "actor_id": actor_id,
        }

    async def run_ai_job_finder(
        self,
        keywords: str,
        location: str = "",
        experience_level: str = "entry level",
        date_posted: str = "7 days",
        limit: int = 100,
        cv_text: Optional[str] = None,
    ) -> dict:
        """
        Run the AI Job Finder actor.

        Args:
            keywords: Job titles/keywords (e.g., "Associate Product Manager")
            location: Location filter (e.g., "Remote", "Bangalore")
            experience_level: Experience level filter
            date_posted: Recency filter ("1 days", "7 days", "2 weeks")
            limit: Max jobs to fetch
            cv_text: Optional CV for AI matching

        Returns:
            Apify run information with dataset ID
        """
        actor_id = "stefanie-rink/ai-job-finder"
        prompt = f"Find {keywords} roles"
        if location:
            prompt += f" in {location}"
        prompt += f", posted in the last {date_posted}."

        run_input = {
            "prompt": prompt,
        }

        return await self._call_actor(actor_id, run_input)

    async def run_all_jobs_scraper(
        self,
        keywords: str,
        location: str = "",
        date_posted: str = "7 days",
        limit: int = 100,
    ) -> dict:
        """
        Run the All Jobs Scraper actor (multi-source).

        Args:
            keywords: Job titles/keywords
            location: Location filter
            date_posted: Recency filter ("1 days", "7 days", "2 weeks")
            limit: Max jobs to fetch

        Returns:
            Apify run information
        """
        actor_id = "agentx/all-jobs-scraper"
        country, refined_location, remote_only = self._resolve_country_location(location)

        run_input = {
            "search_terms": [keywords],
            "country": country,
            "location": refined_location,
            "posted_since": date_posted,
            "max_results": limit,
            "remote_only": remote_only,
        }

        try:
            return await self._call_actor(actor_id, run_input)
        except Exception as exc:
            message = str(exc)
            if "Field input.keyword is required" not in message:
                raise

        legacy_run_input = {
            "keyword": keywords,
            "country": country,
            "location": refined_location,
            "posted_since": date_posted,
            "max_results": limit,
            "remote_only": remote_only,
        }

        return await self._call_actor(actor_id, legacy_run_input)

    async def run_linkedin_jobs(
        self,
        keywords: str,
        location: str = "",
        date_posted: str = "w",  # "w"=week, "m"=month, "d"=day
        limit: int = 100,
    ) -> dict:
        """
        Run LinkedIn Jobs Search actor.

        Args:
            keywords: Job titles/keywords
            location: Location filter
            date_posted: Recency ("d", "w", "m")
            limit: Max jobs to fetch

        Returns:
            Apify run information
        """
        # Reuse the maintained multi-source actor for LinkedIn-style searches.
        return await self.run_all_jobs_scraper(
            keywords=keywords,
            location=location,
            date_posted=date_posted,
            limit=limit,
        )

    async def fetch_dataset_items(
        self, run_id: str, limit: Optional[int] = None
    ) -> list[dict]:
        """
        Fetch results from an Apify dataset.

        Args:
            run_id: The Apify run ID
            limit: Optional limit on items returned

        Returns:
            List of job items
        """
        run = await self.client.run(run_id).get()
        if not run or run.get("status") != "SUCCEEDED":
            raise RuntimeError(f"Run {run_id} did not complete successfully")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(f"No dataset found for run {run_id}")

        dataset = self.client.dataset(dataset_id)

        items = []
        async for item in dataset.iterate_items(limit=limit):
            items.append(item)

        return items

    async def search_jobs(
        self,
        query_name: str,
        keywords: str,
        location: str = "",
        actor: str = "all_jobs",
        date_posted: str = "7 days",
        limit: int = 100,
        cv_text: Optional[str] = None,
    ) -> list[dict]:
        """
        High-level search method that runs an actor and fetches results.

        Args:
            query_name: Name for this search (for logging)
            keywords: Job search keywords
            location: Location filter
            actor: Which actor to use ("ai_job_finder", "all_jobs", "linkedin")
            date_posted: Recency filter
            limit: Max jobs to fetch
            cv_text: Optional CV for AI matching

        Returns:
            List of job items
        """
        print(f"[SEARCH] Running: {query_name}")
        print(f"   Keywords: {keywords}")
        print(f"   Location: {location or 'Any'}")
        print(f"   Actor: {actor}")

        # Select and run appropriate actor
        if actor == "ai_job_finder":
            run_info = await self.run_ai_job_finder(
                keywords=keywords,
                location=location,
                date_posted=date_posted,
                limit=limit,
                cv_text=cv_text,
            )
        elif actor == "linkedin":
            run_info = await self.run_linkedin_jobs(
                keywords=keywords,
                location=location,
                date_posted=date_posted if len(date_posted) == 1 else "w",
                limit=limit,
            )
        else:  # all_jobs
            run_info = await self.run_all_jobs_scraper(
                keywords=keywords,
                location=location,
                date_posted=date_posted,
                limit=limit,
            )

        print(f"   Run ID: {run_info['run_id']}")
        print(f"   Status: {run_info['status']}")

        # Wait and fetch results
        if run_info["status"] == "SUCCEEDED":
            items = await self.fetch_dataset_items(run_info["run_id"])
            print(f"   [OK] Found {len(items)} jobs")
            return items

        # Wait for completion if still running
        print("   Waiting for actor to complete...")
        await self.client.run(run_info["run_id"]).wait_for_finish()

        items = await self.fetch_dataset_items(run_info["run_id"])
        print(f"   [OK] Found {len(items)} jobs")
        return items
