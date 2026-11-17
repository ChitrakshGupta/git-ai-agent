"""
Runs a queued Review row in the background: agent -> save result -> (webhook) publish.

Used by both entry points. The HTTP request that queued the review has already
returned, so this opens its own DB session and records any failure on the row.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.database import async_session
from app.models import Review as ReviewRow
from app.models import User
from app.services.github_client import GitHubClient, GitHubError
from app.services.llm import GeminiLLM, LLMUnavailable
from app.services.publisher import publish_review
from app.services.review_agent import ReviewAgent

logger = logging.getLogger("runner")

# Tests swap this for a fake.
llm_factory = GeminiLLM


async def run_review(review_id: UUID, publish: bool = False) -> None:
    async with async_session() as db:
        row = (await db.execute(select(ReviewRow).where(ReviewRow.id == review_id))).scalar_one()
        token = None
        if row.user_id:
            user = (await db.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
            token = user.access_token if user else None
        row.status = "running"
        row.tool_trace = []
        row.error = None
        await db.commit()

        async def progress(trace: list[dict]):
            row.tool_trace = list(trace)  # new list so SQLAlchemy sees the JSON change
            await db.commit()

        try:
            async with GitHubClient(token) as gh:
                agent = ReviewAgent(llm_factory(), gh)
                result = await agent.review(row.repo_full_name, row.pr_number, progress)
                row.result = result.review.model_dump(mode="json")
                row.tool_trace = result.trace
                row.head_sha = result.head_sha
                row.status = "done"
                if publish:
                    try:
                        row.github_review_url = await publish_review(
                            gh, row.repo_full_name, row.pr_number, result.head_sha, result.review
                        )
                    except GitHubError as exc:
                        # Keep the review; only the posting step failed.
                        row.error = f"Review is ready but posting to GitHub failed: {exc}"[:1000]
        except LLMUnavailable as exc:
            row.status, row.error = "failed", f"AI model unavailable (free-tier rate limit?): {exc}"[:1000]
        except GitHubError as exc:
            hint = " The repo owner needs to sign in again to refresh the GitHub token." if exc.status == 401 else ""
            row.status, row.error = "failed", f"{exc}{hint}"[:1000]
        except Exception as exc:  # recorded on the row so the UI can show it and offer Retry
            logger.exception("Review %s failed", review_id)
            row.status, row.error = "failed", f"Unexpected error: {exc}"[:1000]
        row.finished_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info("Review %s finished: %s", review_id, row.status)
