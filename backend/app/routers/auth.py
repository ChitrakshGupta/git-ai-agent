"""
GitHub OAuth (reused from gitSlackBot, Slack parts removed).
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import create_jwt, decode_jwt, get_current_user
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


def _create_state_token() -> str:
    """Short-lived signed OAuth state: survives restarts and multiple workers, no DB nonce."""
    now = datetime.now(timezone.utc)
    payload = {"purpose": "github_oauth", "iat": now, "exp": now + timedelta(minutes=10)}
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _verify_state_token(token: str) -> None:
    if decode_jwt(token).get("purpose") != "github_oauth":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state parameter")


@router.get("/github")
async def github_login():
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "GitHub OAuth is not configured (GITHUB_CLIENT_ID)")
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.BACKEND_URL}/auth/callback",
        # repo: read PRs (including private) and post reviews. admin:repo_hook: create/delete the webhook.
        "scope": "repo admin:repo_hook",
        "state": _create_state_token(),
    }
    return RedirectResponse(url=f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}")


@router.get("/callback")
async def github_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    if error:
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/?error={error_description or error}")
    if not code or not state:
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/?error=missing_params")
    _verify_state_token(state)

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{settings.BACKEND_URL}/auth/callback",
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to exchange code with GitHub")
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GitHub OAuth error: {token_resp.json().get('error_description')}")

        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to fetch GitHub user profile")

    gh_user = user_resp.json()
    user = (await db.execute(select(User).where(User.github_id == gh_user["id"]))).scalar_one_or_none()
    if user is None:
        user = User(github_id=gh_user["id"], username=gh_user["login"], access_token=access_token)
        db.add(user)
        await db.flush()
    else:
        user.access_token = access_token
        user.username = gh_user["login"]
    # Commit before redirecting: the response leaves as soon as we return.
    await db.commit()

    token = create_jwt(user_id=str(user.id), username=user.username)
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/callback?token={token}")


@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {"id": str(user.id), "username": user.username, "github_id": user.github_id}
