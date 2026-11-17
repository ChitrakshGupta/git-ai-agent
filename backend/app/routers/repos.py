"""
Connect / disconnect repos for auto-review (reused from gitSlackBot's dashboard router).
Connecting creates a GitHub webhook subscribed to pull_request events.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Repo, User

router = APIRouter(prefix="/api/repos", tags=["repos"])
logger = logging.getLogger("repos")


class ConnectRepoRequest(BaseModel):
    repo_full_name: str


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {user.access_token}", "Accept": "application/vnd.github+json"}


def _webhook_url() -> str:
    from app.main import public_webhook_url  # set at startup (ngrok URL in local dev)

    return f"{public_webhook_url}/webhook/github"


@router.get("/github")
async def list_github_repos(search: str = "", user: User = Depends(get_current_user)):
    """The signed-in user's GitHub repos, for the searchable picker."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://api.github.com/user/repos",
            params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"},
            headers=_headers(user),
        )
    if resp.status_code == 401:
        # 424, not 401: our session is fine, only the stored GitHub token was revoked or expired.
        raise HTTPException(
            status.HTTP_424_FAILED_DEPENDENCY,
            "Your GitHub authorization has expired or was revoked. Sign out and sign in with GitHub again.",
        )
    if resp.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"GitHub API error: {resp.status_code}")
    repos = resp.json()
    if search:
        repos = [r for r in repos if search.lower() in r["full_name"].lower()]
    return {
        "repos": [
            {"full_name": r["full_name"], "private": r["private"], "description": r.get("description") or "",
             "language": r.get("language") or ""}
            for r in repos[:50]
        ]
    }


@router.get("")
async def list_repos(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Repo).where(Repo.user_id == user.id).order_by(Repo.created_at.desc()))).scalars().all()
    return {
        "repos": [
            {"id": str(r.id), "repo_full_name": r.repo_full_name, "webhook_id": r.webhook_id,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
        "webhook_url": _webhook_url(),
    }


@router.post("/connect", status_code=201)
async def connect_repo(body: ConnectRepoRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    name = body.repo_full_name.strip()
    if name.count("/") != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Expected owner/repo")
    existing = (await db.execute(select(Repo).where(Repo.repo_full_name == name))).scalar_one_or_none()
    if existing:
        mine = existing.user_id == user.id
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already connected" + ("" if mine else " by another user"))

    webhook_url = _webhook_url()
    hook = {
        "name": "web",
        "active": True,
        "events": ["pull_request"],
        "config": {"url": webhook_url, "content_type": "json", "secret": settings.GITHUB_WEBHOOK_SECRET, "insecure_ssl": "0"},
    }
    webhook_id, webhook_error = None, None
    async with httpx.AsyncClient(timeout=10.0) as client:
        check = await client.get(f"https://api.github.com/repos/{name}", headers=_headers(user))
        if check.status_code == 404:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{name} not found or you don't have access")
        resp = await client.post(f"https://api.github.com/repos/{name}/hooks", json=hook, headers=_headers(user))
        if resp.status_code in (200, 201):
            webhook_id = resp.json().get("id")
        elif resp.status_code == 422:
            # 422 also means "a hook with this URL already exists": reuse it.
            hooks = await client.get(f"https://api.github.com/repos/{name}/hooks", headers=_headers(user))
            if hooks.status_code == 200:
                webhook_id = next((h["id"] for h in hooks.json() if h.get("config", {}).get("url") == webhook_url), None)
        if not webhook_id:
            webhook_error = resp.json().get("message", resp.text) if resp.content else str(resp.status_code)
            logger.warning("Webhook creation failed on %s: %s", name, webhook_error)

    repo = Repo(user_id=user.id, repo_full_name=name, webhook_id=webhook_id)
    db.add(repo)
    await db.flush()
    return {
        "id": str(repo.id),
        "repo_full_name": name,
        "webhook_id": webhook_id,
        "webhook_url": webhook_url,
        "message": (
            f"Connected {name}. New and updated PRs will be reviewed automatically."
            if webhook_id
            else f"Connected {name}, but the webhook could not be created ({webhook_error}). "
            f"GitHub cannot reach localhost: run with ngrok, or add a webhook manually "
            f"(payload URL {webhook_url}, content type JSON, event: Pull requests)."
        ),
    }


@router.delete("/{repo_id}")
async def delete_repo(repo_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        rid = uuid.UUID(repo_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid repo id")
    repo = (await db.execute(select(Repo).where(Repo.id == rid, Repo.user_id == user.id))).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repo not found")
    if repo.webhook_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.delete(
                    f"https://api.github.com/repos/{repo.repo_full_name}/hooks/{repo.webhook_id}", headers=_headers(user)
                )
        except httpx.HTTPError as exc:
            logger.warning("Could not delete webhook %s: %s", repo.webhook_id, exc)
    await db.delete(repo)
    return {"message": f"Disconnected {repo.repo_full_name}"}
