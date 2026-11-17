"""
Thin async GitHub REST client for the review agent (read side) and the publisher (write side).
"""

import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger("github")
API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


class GitHubClient:
    def __init__(self, token: str | None = None):
        token = token or settings.GITHUB_TOKEN or None
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=API, headers=headers, timeout=20.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                message = resp.json().get("message", resp.text)
            except ValueError:
                message = resp.text
            if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                message = "GitHub rate limit reached. Sign in or set GITHUB_TOKEN to raise it."
            raise GitHubError(resp.status_code, message[:300])
        return resp

    async def get_pr(self, repo: str, number: int) -> dict:
        return (await self._request("GET", f"/repos/{repo}/pulls/{number}")).json()

    async def list_pr_files(self, repo: str, number: int) -> list[dict]:
        files: list[dict] = []
        page = 1
        while page <= 10:  # GitHub caps this endpoint at 3000 files
            batch = (
                await self._request(
                    "GET", f"/repos/{repo}/pulls/{number}/files", params={"per_page": 100, "page": page}
                )
            ).json()
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    async def get_file_content(self, repo: str, path: str, ref: str) -> str:
        data = (await self._request("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref})).json()
        if isinstance(data, list):
            raise GitHubError(400, f"{path} is a directory")
        if data.get("encoding") != "base64":
            raise GitHubError(400, f"{path} is too large or not a regular file")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    async def get_tree_paths(self, repo: str, ref: str) -> list[str]:
        data = (await self._request("GET", f"/repos/{repo}/git/trees/{ref}", params={"recursive": "1"})).json()
        return [item["path"] for item in data.get("tree", []) if item.get("type") == "blob"]

    async def create_review(self, repo: str, number: int, commit_id: str, body: str, comments: list[dict]) -> dict:
        payload = {"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments}
        return (await self._request("POST", f"/repos/{repo}/pulls/{number}/reviews", json=payload)).json()

    async def create_issue_comment(self, repo: str, number: int, body: str) -> dict:
        return (await self._request("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})).json()
