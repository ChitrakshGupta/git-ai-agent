import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import async_session
from app.main import app
from app.models import Repo, User
from app.routers import webhook as webhook_module

SECRET = b"test-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def pr_event(action="opened", sha="sha1", repo="octo/demo", draft=False):
    return {
        "action": action,
        "repository": {"full_name": repo},
        "pull_request": {"number": 5, "title": "Add thing", "html_url": "u", "draft": draft, "head": {"sha": sha}},
    }


@pytest.fixture
def client(monkeypatch):
    queued = []

    async def fake_run(review_id, publish=False):
        queued.append((review_id, publish))

    monkeypatch.setattr(webhook_module, "run_review", fake_run)
    with TestClient(app) as c:
        c.queued = queued
        yield c


@pytest.fixture
async def connected_repo():
    async with async_session() as db:
        user = User(github_id=uuid.uuid4().int % 10**9, username="octo", access_token="t")
        db.add(user)
        await db.flush()
        db.add(Repo(user_id=user.id, repo_full_name="octo/demo"))
        await db.commit()


def post(client, payload, event="pull_request", signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-Hub-Signature-256": signature or sign(body),
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
    )


def test_rejects_bad_signature(client):
    assert post(client, pr_event(), signature="sha256=deadbeef").status_code == 403


def test_ignores_other_events_and_unconnected_repos(client):
    assert post(client, {}, event="push").json()["status"] == "ignored"
    assert post(client, {"zen": "hi"}, event="ping").json()["status"] == "pong"
    assert post(client, pr_event(repo="someone/else")).json()["reason"] == "repo not connected"
    assert post(client, pr_event(action="closed")).json()["status"] == "ignored"
    assert client.queued == []


@pytest.mark.asyncio
async def test_queues_once_per_commit_and_publishes(client, connected_repo):
    first = post(client, pr_event(sha="aaa"))
    assert first.status_code == 202
    assert post(client, pr_event(sha="aaa", action="reopened")).json()["status"] == "duplicate"
    assert post(client, pr_event(sha="bbb", action="synchronize")).status_code == 202
    assert post(client, pr_event(sha="ccc", draft=True)).json()["reason"] == "draft PR"
    assert len(client.queued) == 2
    assert all(publish for _, publish in client.queued)


def test_uptime_and_health_endpoints(client):
    # Test root endpoint
    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert root_resp.json()["status"] == "ok"
    assert client.head("/").status_code == 200

    # Test uptime bot ping endpoint (GET and HEAD)
    ping_resp = client.get("/ping")
    assert ping_resp.status_code == 200
    assert ping_resp.json()["status"] == "pong"
    assert client.head("/ping").status_code == 200

    # Test health endpoints
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert client.head("/health").status_code == 200

    api_health_resp = client.get("/api/health")
    assert api_health_resp.status_code == 200
    assert api_health_resp.json()["status"] == "ok"
