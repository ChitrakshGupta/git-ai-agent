"""
Review API.

  POST /api/reviews               paste a PR link -> queued review (202), works signed out for public repos
  GET  /api/reviews/{id}          poll status, tool trace and result
  GET  /api/reviews               signed-in user's reviews (dashboard)
  POST /api/reviews/{id}/retry    re-run a failed review
  POST /api/reviews/{id}/publish  post a finished review on the PR (signed in)
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import decode_jwt, get_current_user
from app.models import Repo, User
from app.models import Review as ReviewRow
from app.services import diff_utils
from app.services.github_client import GitHubClient, GitHubError
from app.services.publisher import publish_review
from app.services.review_runner import run_review
from app.services.schemas import Review

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
_optional_bearer = HTTPBearer(auto_error=False)


class CreateReviewRequest(BaseModel):
    pr_url: str


async def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    if credentials is None:
        return None
    try:
        payload = decode_jwt(credentials.credentials)
    except HTTPException:
        return None
    return (await db.execute(select(User).where(User.id == UUID(payload["sub"])))).scalar_one_or_none()


def serialize(row: ReviewRow) -> dict:
    return {
        "id": str(row.id),
        "repo_full_name": row.repo_full_name,
        "pr_number": row.pr_number,
        "pr_title": row.pr_title,
        "pr_url": row.pr_url,
        "head_sha": row.head_sha,
        "source": row.source,
        "status": row.status,
        "result": row.result,
        "tool_trace": row.tool_trace or [],
        "error": row.error,
        "github_review_url": row.github_review_url,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_review(
    body: CreateReviewRequest,
    background: BackgroundTasks,
    user: User | None = Depends(optional_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        repo, number = diff_utils.parse_pr_url(body.pr_url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    # Validate the PR exists and get the head commit before queueing.
    try:
        async with GitHubClient(user.access_token if user else None) as gh:
            pr = await gh.get_pr(repo, number)
    except GitHubError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.status == 404 else status.HTTP_502_BAD_GATEWAY
        detail = "PR not found (private repos need you to sign in)" if exc.status == 404 else str(exc)
        raise HTTPException(code, detail)

    head_sha = pr["head"]["sha"]
    existing = (
        await db.execute(
            select(ReviewRow).where(
                ReviewRow.repo_full_name == repo,
                ReviewRow.pr_number == number,
                ReviewRow.head_sha == head_sha,
                ReviewRow.source == "link",
            )
        )
    ).scalar_one_or_none()
    if existing and existing.status in ("queued", "running", "done"):
        # Same commit already reviewed or in progress: return it instead of spending quota again.
        return serialize(existing)

    row = existing or ReviewRow(repo_full_name=repo, pr_number=number, head_sha=head_sha, source="link")
    row.user_id = user.id if user else None
    row.pr_title, row.pr_url = pr.get("title"), pr.get("html_url")
    row.status, row.result, row.error, row.tool_trace = "queued", None, None, []
    db.add(row)
    await db.commit()
    background.add_task(run_review, row.id)
    return serialize(row)


@router.get("")
async def list_reviews(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    repo_names = select(Repo.repo_full_name).where(Repo.user_id == user.id)
    rows = (
        await db.execute(
            select(ReviewRow)
            .where(or_(ReviewRow.user_id == user.id, ReviewRow.repo_full_name.in_(repo_names)))
            .order_by(ReviewRow.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return {"reviews": [serialize(r) for r in rows]}


async def _get_row(review_id: str, db: AsyncSession) -> ReviewRow:
    try:
        rid = UUID(review_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid review id")
    row = (await db.execute(select(ReviewRow).where(ReviewRow.id == rid))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review not found")
    return row


@router.get("/{review_id}")
async def get_review(review_id: str, db: AsyncSession = Depends(get_db)):
    return serialize(await _get_row(review_id, db))


@router.post("/{review_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_review(review_id: str, background: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    row = await _get_row(review_id, db)
    if row.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed reviews can be retried")
    row.status, row.error, row.tool_trace = "queued", None, []
    await db.commit()
    background.add_task(run_review, row.id, row.source == "webhook")
    return serialize(row)


@router.post("/{review_id}/publish")
async def publish(review_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    row = await _get_row(review_id, db)
    if row.status != "done" or not row.result:
        raise HTTPException(status.HTTP_409_CONFLICT, "Review is not finished")
    if row.github_review_url:
        return serialize(row)
    try:
        async with GitHubClient(user.access_token) as gh:
            row.github_review_url = await publish_review(
                gh, row.repo_full_name, row.pr_number, row.head_sha, Review.model_validate(row.result)
            )
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not post the review: {exc}")
    await db.commit()
    return serialize(row)
