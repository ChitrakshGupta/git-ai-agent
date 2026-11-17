"""
GitHub webhook receiver: pull_request opened / reopened / synchronize -> background review -> posted on the PR.

Security and reliability (signature check and dedupe reused from gitSlackBot):
  - HMAC-SHA256 verification of X-Hub-Signature-256 on the raw body
  - Returns 202 immediately; the review runs in the background. GitHub waits ~10 s for a
    webhook response and an agent review takes 30-120 s.
  - Idempotent: one review per (repo, PR, head commit), so re-deliveries and duplicate
    "synchronize" events never post twice.
"""

import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Repo
from app.models import Review as ReviewRow
from app.services.review_runner import run_review

router = APIRouter(tags=["webhook"])
logger = logging.getLogger("webhook")

REVIEW_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def verify_signature(body: bytes, signature_header: str | None) -> bool:
    if not settings.GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set: skipping signature verification (dev only)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])  # constant-time


@router.post("/webhook/github")
async def receive_github_webhook(
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(None),
    x_github_delivery: str | None = Header(None),
    x_github_event: str | None = Header(None),
):
    body = await request.body()  # HMAC must run on the exact bytes GitHub signed
    if not verify_signature(body, x_hub_signature_256):
        return Response(content="Invalid signature", status_code=403)

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event {x_github_event} not handled"}

    try:
        payload = await request.json()
    except ValueError:
        return Response(content="Invalid JSON", status_code=400)

    action = payload.get("action")
    pr = payload.get("pull_request") or {}
    if action not in REVIEW_ACTIONS:
        return {"status": "ignored", "reason": f"action {action}"}
    if pr.get("draft"):
        return {"status": "ignored", "reason": "draft PR"}

    repo_full = (payload.get("repository") or {}).get("full_name")
    repo = (await db.execute(select(Repo).where(Repo.repo_full_name == repo_full))).scalar_one_or_none()
    if repo is None:
        return {"status": "ignored", "reason": "repo not connected"}

    head_sha = pr["head"]["sha"]
    row = ReviewRow(
        user_id=repo.user_id,
        repo_full_name=repo_full,
        pr_number=pr["number"],
        pr_title=pr.get("title"),
        pr_url=pr.get("html_url"),
        head_sha=head_sha,
        source="webhook",
        status="queued",
        delivery_id=x_github_delivery,
        tool_trace=[],
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.info("Review for %s#%s@%s already exists, skipping", repo_full, pr["number"], head_sha[:7])
        return {"status": "duplicate"}

    background.add_task(run_review, row.id, True)
    logger.info("Queued review %s for %s#%s (%s)", row.id, repo_full, pr["number"], action)
    return Response(content='{"status":"queued"}', status_code=202, media_type="application/json")
