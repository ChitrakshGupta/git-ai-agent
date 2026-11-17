"""
Helpers for unified diffs: PR URL parsing, file filtering, and mapping hunks to
the new-file line numbers GitHub accepts for inline review comments.
"""

import re

PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")

# Files the agent should not spend tokens on.
SKIP_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2", ".ttf", ".snap",
)
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "go.sum"}

MAX_PATCH_CHARS = 12_000  # per file
MAX_FILE_CHARS = 20_000  # get_file_content


def parse_pr_url(url: str) -> tuple[str, int]:
    """Return ("owner/repo", number) or raise ValueError."""
    m = PR_URL_RE.match(url.strip())
    if not m:
        raise ValueError("Expected a GitHub pull request URL like https://github.com/owner/repo/pull/123")
    return f"{m['owner']}/{m['repo']}", int(m["number"])


def should_skip(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in SKIP_NAMES or path.lower().endswith(SKIP_SUFFIXES) or "/dist/" in f"/{path}"


def commentable_lines(patch: str | None) -> set[int]:
    """
    New-file line numbers that appear in the diff (added or context lines).

    GitHub only accepts inline review comments on these lines; one bad line
    number makes the whole "create review" call fail with 422.
    """
    lines: set[int] = set()
    if not patch:
        return lines
    new_line = 0
    for raw in patch.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            new_line = int(m["start"])
            continue
        if raw.startswith("-") or raw.startswith("\\"):
            continue  # removed line or "\ No newline at end of file"
        # "+" added or " " context line: both exist in the new file
        lines.add(new_line)
        new_line += 1
    return lines


def number_patch(patch: str) -> str:
    """Prefix added/context lines with their new-file line number so the model can cite lines."""
    out = []
    new_line = 0
    for raw in patch.splitlines():
        m = HUNK_RE.match(raw)
        if m:
            new_line = int(m["start"])
            out.append(raw)
            continue
        if raw.startswith("-"):
            out.append(f"     {raw}")
        elif raw.startswith("\\"):
            out.append(raw)
        else:
            out.append(f"{new_line:>4} {raw}")
            new_line += 1
    return "\n".join(out)


def truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated {len(text) - limit} characters]", True
