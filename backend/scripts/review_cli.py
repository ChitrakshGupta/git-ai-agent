"""
Run the review agent from the terminal, without the web app or database.

    python -m scripts.review_cli https://github.com/owner/repo/pull/123 [--json]
"""

import argparse
import logging
import asyncio
import json
import sys

from app.services.diff_utils import parse_pr_url
from app.services.github_client import GitHubClient
from app.services.llm import GeminiLLM
from app.services.review_agent import ReviewAgent

ICON = {"bug": "🐛", "risk": "⚠️ ", "missing_test": "🧪", "style": "🎨"}


async def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="  ! %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="AI review of a GitHub pull request")
    parser.add_argument("pr_url")
    parser.add_argument("--json", action="store_true", help="print the raw Review JSON")
    args = parser.parse_args()
    repo, number = parse_pr_url(args.pr_url)

    async def progress(trace):
        e = trace[-1]
        label = f"{e['tool']}({json.dumps(e.get('args', {}))})" if e["type"] == "tool" else e["type"]
        print(f"  · {label}: {e.get('detail', '')}", file=sys.stderr)

    async with GitHubClient() as gh:
        result = await ReviewAgent(GeminiLLM(), gh).review(repo, number, progress)

    review = result.review
    if args.json:
        print(review.model_dump_json(indent=2))
        return 0
    print(f"\n{repo}#{number}: {result.pr.get('title')}\nRisk: {review.risk_level.value}\n\n{review.summary}\n")
    for f in review.findings:
        loc = f"{f.file}:{f.line}" if f.line else f.file
        print(f"{ICON[f.category.value]} [{f.severity.value}] {f.title}  ({loc})\n   {f.explanation}")
        if f.suggestion:
            print(f"   → {f.suggestion}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
