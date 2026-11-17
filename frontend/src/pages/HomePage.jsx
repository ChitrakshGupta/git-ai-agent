/**
 * Two ways to run the same review agent:
 *   1. "Review a PR link"  – paste a GitHub PR URL, watch the agent, read the findings.
 *   2. "Auto-review repos" – sign in with GitHub, connect repos; a webhook reviews every
 *      new or updated PR and posts the review on GitHub.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { apiJson, isSignedIn, loginUrl, signOut } from '../api'
import ReviewResult, { StatusPill } from '../components/ReviewResult'

const POLL_MS = 2000

/** Poll a review until it is done or failed. */
function useReviewPolling() {
  const [review, setReview] = useState(null)
  const timer = useRef(null)

  useEffect(() => {
    if (!review || review.status === 'done' || review.status === 'failed') return
    timer.current = setTimeout(async () => {
      try {
        setReview(await apiJson(`/api/reviews/${review.id}`))
      } catch {
        // transient error: try again on the next tick
        setReview({ ...review })
      }
    }, POLL_MS)
    return () => clearTimeout(timer.current)
  }, [review])

  return [review, setReview]
}

// ─────────────────────────────────────────────────────────────
// Section 1: review a PR link
// ─────────────────────────────────────────────────────────────

function PrLinkSection() {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [review, setReview] = useReviewPolling()
  const [publishing, setPublishing] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      setReview(await apiJson('/api/reviews', { method: 'POST', body: JSON.stringify({ pr_url: url.trim() }) }))
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function retry() {
    try {
      setReview(await apiJson(`/api/reviews/${review.id}/retry`, { method: 'POST' }))
    } catch (err) {
      setError(err.message)
    }
  }

  async function publish() {
    setPublishing(true)
    try {
      setReview(await apiJson(`/api/reviews/${review.id}/publish`, { method: 'POST' }))
    } catch (err) {
      setError(err.message)
    } finally {
      setPublishing(false)
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo/pull/123"
          className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-900 px-4 py-2.5 text-sm text-white placeholder-gray-500 outline-none focus:border-indigo-500"
          required
        />
        <button
          type="submit"
          disabled={submitting || review?.status === 'running' || review?.status === 'queued'}
          className="rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {submitting ? 'Starting…' : 'Review PR'}
        </button>
      </form>
      <p className="-mt-3 text-xs text-gray-500">
        Public repos work without signing in. Sign in (Auto-review tab) to review private repos or post the review on GitHub.
      </p>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</p>}
      {review && (
        <ReviewResult
          review={review}
          onRetry={retry}
          onPublish={publish}
          canPublish={isSignedIn()}
          publishing={publishing}
        />
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// Section 2: GitHub OAuth + webhook auto-review
// ─────────────────────────────────────────────────────────────

function AutoReviewSection() {
  const [me, setMe] = useState(null)
  const [loading, setLoading] = useState(isSignedIn())
  const [repos, setRepos] = useState([])
  const [webhookUrl, setWebhookUrl] = useState('')
  const [ghRepos, setGhRepos] = useState([])
  const [search, setSearch] = useState('')
  const [reviews, setReviews] = useState([])
  const [selected, setSelected] = useState(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const [user, repoList, reviewList] = await Promise.all([
        apiJson('/auth/me'),
        apiJson('/api/repos'),
        apiJson('/api/reviews'),
      ])
      setMe(user)
      setRepos(repoList.repos)
      setWebhookUrl(repoList.webhook_url)
      setReviews(reviewList.reviews)
    } catch (err) {
      if (!isSignedIn()) setMe(null)
      else setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isSignedIn()) load()
  }, [load])

  // Refresh the review list while any review is still in progress.
  useEffect(() => {
    if (!reviews.some((r) => r.status === 'queued' || r.status === 'running')) return
    const t = setTimeout(async () => {
      try {
        setReviews((await apiJson('/api/reviews')).reviews)
      } catch {
        // ignore; next tick retries
      }
    }, 3000)
    return () => clearTimeout(t)
  }, [reviews])

  useEffect(() => {
    if (!me) return
    const t = setTimeout(async () => {
      try {
        setGhRepos((await apiJson(`/api/repos/github?search=${encodeURIComponent(search)}`)).repos)
      } catch (err) {
        setError(err.message)
      }
    }, 300)
    return () => clearTimeout(t)
  }, [me, search])

  async function connect(fullName) {
    setError('')
    setMessage('')
    try {
      const res = await apiJson('/api/repos/connect', { method: 'POST', body: JSON.stringify({ repo_full_name: fullName }) })
      setMessage(res.message)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function disconnect(id) {
    try {
      await apiJson(`/api/repos/${id}`, { method: 'DELETE' })
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>

  if (!me) {
    return (
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center">
        <h2 className="text-lg font-semibold text-white">Review every pull request automatically</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-gray-400">
          Sign in with GitHub and connect a repository. A webhook triggers the same agent whenever a PR is opened or
          updated, and the review is posted on the PR with inline comments.
        </p>
        <a
          href={loginUrl}
          className="mt-6 inline-flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-medium text-gray-900 hover:bg-gray-200"
        >
          Sign in with GitHub
        </a>
      </div>
    )
  }

  const connected = new Set(repos.map((r) => r.repo_full_name))
  const selectedReview = reviews.find((r) => r.id === selected)

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-400">
          Signed in as <span className="font-medium text-white">{me.username}</span>
        </p>
        <button
          onClick={() => {
            signOut()
            setMe(null)
          }}
          className="text-sm text-gray-500 hover:text-gray-300"
        >
          Sign out
        </button>
      </div>

      {message && <p className="whitespace-pre-wrap rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-200">{message}</p>}
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{error}</p>}

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <h3 className="text-sm font-semibold text-white">Connected repositories</h3>
          <p className="mt-1 text-xs text-gray-500">Webhook URL: <span className="font-mono">{webhookUrl}</span></p>
          {repos.length === 0 ? (
            <p className="mt-4 text-sm text-gray-500">None yet. Pick a repo on the right.</p>
          ) : (
            <ul className="mt-4 divide-y divide-gray-800">
              {repos.map((r) => (
                <li key={r.id} className="flex items-center justify-between py-2.5 text-sm">
                  <span className="font-mono text-gray-200">
                    {r.repo_full_name}
                    {!r.webhook_id && <span className="ml-2 text-xs text-amber-400">(no webhook)</span>}
                  </span>
                  <button onClick={() => disconnect(r.id)} className="text-xs text-gray-500 hover:text-red-300">
                    Disconnect
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-gray-800 bg-gray-900 p-5">
          <h3 className="text-sm font-semibold text-white">Connect a repository</h3>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your repos"
            className="mt-3 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-indigo-500"
          />
          <ul className="mt-3 max-h-64 divide-y divide-gray-800 overflow-y-auto">
            {ghRepos.map((r) => (
              <li key={r.full_name} className="flex items-center justify-between gap-2 py-2 text-sm">
                <span className="min-w-0 truncate font-mono text-gray-300">
                  {r.full_name} {r.private && <span className="text-xs text-gray-500">private</span>}
                </span>
                {connected.has(r.full_name) ? (
                  <span className="text-xs text-emerald-400">connected</span>
                ) : (
                  <button onClick={() => connect(r.full_name)} className="shrink-0 rounded-md bg-indigo-600 px-2.5 py-1 text-xs text-white hover:bg-indigo-500">
                    Connect
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section>
        <h3 className="mb-3 text-sm font-semibold text-white">Reviews</h3>
        {reviews.length === 0 ? (
          <p className="text-sm text-gray-500">Open or update a pull request on a connected repo and its review will appear here.</p>
        ) : (
          <ul className="divide-y divide-gray-800 rounded-xl border border-gray-800 bg-gray-900">
            {reviews.map((r) => (
              <li key={r.id}>
                <button
                  onClick={() => setSelected(selected === r.id ? null : r.id)}
                  className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-gray-800/50"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm text-gray-200">{r.pr_title || `PR #${r.pr_number}`}</span>
                    <span className="font-mono text-xs text-gray-500">
                      {r.repo_full_name}#{r.pr_number} · {r.source === 'webhook' ? 'webhook' : 'link'} ·{' '}
                      {r.result ? `${r.result.findings.length} findings` : ''}
                    </span>
                  </span>
                  <StatusPill status={r.status} />
                </button>
                {selected === r.id && selectedReview && (
                  <div className="border-t border-gray-800 p-4">
                    <ReviewResult review={selectedReview} canPublish={false} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────

export default function HomePage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'auto' ? 'auto' : 'link'

  const tabClass = (t) =>
    `rounded-md px-4 py-2 text-sm font-medium transition ${tab === t ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'}`

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="mx-auto max-w-5xl px-4 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-bold text-white">PR Review Agent</h1>
          <p className="mt-1 text-sm text-gray-400">
            An AI agent that reads a pull request with tools and reports bugs, risks, missing tests and style issues.
          </p>
        </header>
        <nav className="mb-8 inline-flex gap-1 rounded-lg border border-gray-800 bg-gray-900 p-1">
          <button className={tabClass('link')} onClick={() => setParams({})}>
            Review a PR link
          </button>
          <button className={tabClass('auto')} onClick={() => setParams({ tab: 'auto' })}>
            Auto-review repos
          </button>
        </nav>
        {tab === 'link' ? <PrLinkSection /> : <AutoReviewSection />}
      </div>
    </div>
  )
}
