# PR Review Agent

An AI agent that reviews a GitHub pull request and returns structured feedback in four groups:
**bugs, risks, missing tests and style issues**. Each finding has a severity, a `file:line`,
an explanation and a suggested fix.

## What it does

The same agent runs from two entry points:

1. **Review a PR link.** Paste `https://github.com/owner/repo/pull/123`. You watch the agent's
   tool calls live, then read the findings. Public repos need no login. If you are signed in,
   you can post the result on the PR.
2. **Auto-review repos.** Sign in with GitHub OAuth and connect a repo. The app installs a
   webhook, so every PR that is opened, reopened or updated (`synchronize`) is reviewed in the
   background. The result is posted as a GitHub PR review with inline comments, and listed
   on the dashboard.

## How it's built

| Part | Choice |
|---|---|
| Model | Google Gemini, free tier (`google-genai` SDK), behind a small provider interface (`services/llm.py`) |
| Backend | FastAPI, async SQLAlchemy (SQLite locally, Postgres/Neon in production) |
| Frontend | React, Vite, Tailwind |
| Auth / events | GitHub OAuth and HMAC-verified webhooks, reused from my earlier project (gitSlackBot) |

**Agent loop** (`backend/app/services/review_agent.py`):

1. **Fetch.** Plain code pulls the PR metadata and the changed files. Lockfiles, binaries and
   build output are skipped.
2. **Investigate.** A Gemini chat with four read-only tools that I declared myself:
   - `list_changed_files`
   - `get_file_diff`: hunks numbered with new-file line numbers
   - `get_file_content`: the head commit, optionally a line range
   - `find_tests`: related test files, and whether this PR changes any tests

   Automatic function calling is off, so my code runs every call, logs it to the UI trace and
   returns errors to the model as data. The model may request several tools in one turn. The
   loop is capped at 8 turns and 20 tool calls. Small PRs (≤400 changed lines) get every diff
   in the first message, so most reviews need 2–3 model calls.
3. **Judge.** One structured-output call (`response_schema` = a Pydantic `Review` model with
   enum categories and severities) turns the collected evidence and notes into validated JSON.
4. **Check in code.** Findings on files that are not in the PR are dropped. Line numbers that
   are not in the diff become file-level findings. Duplicates are removed and the list is
   sorted by severity.

All PR text, diffs and files are wrapped in `<untrusted>` tags, and the prompt tells the model
never to follow instructions found in them. This guards against prompt injection from the PR
author.

## What went wrong and how I handled it

- **Model names had gone stale.** `gemini-flash-latest` returned 404, and 2.5 Flash is "no longer
  available to new users". I listed the models available to the key, and made the model list
  configurable with automatic fallback.
- **The first review took 4 min 19 s.** Free-tier Flash models answered `503 high demand` or took
  48–79 s even for "Say OK". The fix:
  - treat a 503 as "move to the next model" instead of retrying;
  - add a 60 s timeout per request;
  - put the faster `flash-lite` models first;
  - allow only one Gemini request in flight at a time, so concurrent webhooks don't burn the quota.

  The same review now takes about 16 s.
- **Gemini chat history can't be rebuilt from scratch.** Gemini attaches thought signatures to
  function calls, so the model's turns are replayed exactly as received, and a chat session
  stays on one model.
- **GitHub webhook timeout.** GitHub waits about 10 s for a webhook response, and a review takes
  longer. The webhook therefore returns `202` at once and runs the review as a background task.
  A unique `(repo, PR, head commit)` key makes re-deliveries and duplicate events harmless.
- **Inline comments can sink the whole review.** If any comment points at a line outside the
  diff, GitHub rejects the whole review with `422`. Line numbers are now validated against the
  diff hunks, and if GitHub still refuses, the review is re-posted with everything in the body.
- **LLMs can be confidently wrong, in both directions.** I tested on public PRs that were later
  reverted:
  - **`psf/requests#6667`:** the agent flagged import-time loading of the CA bundle as a risk,
    which is in line with why the PR was reverted. It also recovered by itself after a tool call
    returned 404 for a path it guessed wrong.
  - **`encode/httpx#2523`:** the agent missed that raising `TypeError` is a breaking change, which
    is exactly why that PR was reverted.
  - **`pallets/click#3921`:** one finding (copying an `Enum` sentinel) was speculative.

  The judge prompt asks for evidence-backed findings only. Measuring precision and recall
  properly is the first item below.

## What I'd do next

1. **An eval set.** PRs with known, planted bugs (see `samples/demo-pr/`), so prompt or model
   changes can be scored on precision and recall instead of judged by eye.
2. **Real analysis tools.** Run linters, type checkers and the test suite in a sandbox, and give
   the agent their output as tools.
3. **Incremental reviews.** On `synchronize`, review only the commits added since the last
   review, and resolve comments that have been fixed.
4. **A GitHub App instead of an OAuth app.** Per-repo install tokens, the bot's own identity, and
   finer-grained permissions.
5. **A durable job queue** (for example Redis with RQ/Arq) instead of in-process background
   tasks, plus streaming progress with server-sent events instead of polling.

## Run it

```bash
cp .env.example backend/.env        # set GEMINI_API_KEY (the only required value)
cd backend && python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host localhost --port 8000   # localhost = IPv4 and IPv6
cd ../frontend && npm install && npm run dev    # http://localhost:5173
```

**Command line:** `python -m scripts.review_cli https://github.com/pallets/click/pull/3919`

**Tests:** `pytest`, with 15 tests. They use a scripted fake LLM and a fake GitHub, so they need
no network or tokens.

**Auto-review locally:**
- Create a GitHub OAuth App with callback `http://localhost:8000/auth/callback` and set
  `GITHUB_CLIENT_ID`/`SECRET`.
- Expose the backend with ngrok and set `WEBHOOK_URL` to the https URL, so GitHub can reach it.
