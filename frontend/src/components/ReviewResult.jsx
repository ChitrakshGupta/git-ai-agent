/**
 * Renders one review: status, live agent trace, summary, and findings grouped by category.
 */

import { useState } from 'react'

const CATEGORIES = [
  { key: 'bug', label: 'Bugs', icon: '🐛' },
  { key: 'risk', label: 'Risks', icon: '⚠️' },
  { key: 'missing_test', label: 'Missing tests', icon: '🧪' },
  { key: 'style', label: 'Style', icon: '🎨' },
]

const SEVERITY = {
  high: 'bg-red-500/15 text-red-300 ring-red-500/30',
  medium: 'bg-amber-500/15 text-amber-300 ring-amber-500/30',
  low: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/30',
}

export function Badge({ level, children }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${SEVERITY[level] || 'bg-gray-700 text-gray-200 ring-gray-600'}`}>
      {children || level}
    </span>
  )
}

export function StatusPill({ status }) {
  const styles = {
    queued: 'bg-gray-700 text-gray-200',
    running: 'bg-indigo-500/20 text-indigo-300',
    done: 'bg-emerald-500/20 text-emerald-300',
    failed: 'bg-red-500/20 text-red-300',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[status] || styles.queued}`}>
      {(status === 'running' || status === 'queued') && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {status}
    </span>
  )
}

function AgentTrace({ trace, open }) {
  const [expanded, setExpanded] = useState(open)
  if (!trace?.length) return null
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm text-gray-300 hover:text-white"
      >
        <span>Agent trace · {trace.filter((t) => t.type === 'tool').length} tool calls</span>
        <span className="text-gray-500">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <ol className="space-y-1.5 border-t border-gray-800 px-4 py-3 font-mono text-xs">
          {trace.map((t, i) => (
            <li key={i} className="flex gap-2">
              <span className={t.ok === false ? 'text-red-400' : t.type === 'tool' ? 'text-indigo-400' : 'text-gray-500'}>
                {t.type === 'tool' ? '⚙' : '•'}
              </span>
              <span className="text-gray-300">
                {t.type === 'tool' ? (
                  <>
                    <span className="text-indigo-300">{t.tool}</span>
                    <span className="text-gray-500">({Object.values(t.args || {}).join(', ')})</span>{' '}
                  </>
                ) : null}
                <span className="text-gray-400">{t.detail}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function FindingCard({ f, repo, sha }) {
  const fileUrl = repo && sha ? `https://github.com/${repo}/blob/${sha}/${f.file}${f.line ? `#L${f.line}` : ''}` : null
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge level={f.severity} />
        <h4 className="font-medium text-white">{f.title}</h4>
      </div>
      <a
        href={fileUrl || undefined}
        target="_blank"
        rel="noreferrer"
        className="mt-1 inline-block font-mono text-xs text-indigo-300 hover:underline"
      >
        {f.file}
        {f.line ? `:${f.line}` : ''}
      </a>
      <p className="mt-2 text-sm leading-relaxed text-gray-300">{f.explanation}</p>
      {f.suggestion && (
        <div className="mt-3 rounded-md bg-gray-950 p-3 text-sm text-gray-300">
          <span className="font-medium text-gray-400">Suggestion: </span>
          <span className="whitespace-pre-wrap">{f.suggestion}</span>
        </div>
      )}
    </div>
  )
}

export default function ReviewResult({ review, onRetry, onPublish, canPublish, publishing }) {
  const result = review.result
  const findings = result?.findings || []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <a href={review.pr_url} target="_blank" rel="noreferrer" className="text-lg font-semibold text-white hover:underline">
            {review.pr_title || `PR #${review.pr_number}`}
          </a>
          <p className="mt-0.5 font-mono text-xs text-gray-500">
            {review.repo_full_name}#{review.pr_number} · {review.head_sha?.slice(0, 7)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={review.status} />
          {result && <Badge level={result.risk_level}>risk: {result.risk_level}</Badge>}
        </div>
      </div>

      {review.status === 'failed' && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          <p>{review.error || 'The review failed.'}</p>
          {onRetry && (
            <button onClick={onRetry} className="mt-3 rounded-md bg-red-500/20 px-3 py-1.5 text-red-100 hover:bg-red-500/30">
              Retry
            </button>
          )}
        </div>
      )}
      {review.status === 'done' && review.error && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">{review.error}</div>
      )}

      <AgentTrace trace={review.tool_trace} open={review.status !== 'done'} key={review.status === 'done' ? 'd' : 'r'} />

      {result && (
        <>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
            <p className="text-sm leading-relaxed text-gray-200">{result.summary}</p>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              {review.github_review_url ? (
                <a href={review.github_review_url} target="_blank" rel="noreferrer" className="text-sm text-emerald-300 hover:underline">
                  ✓ Posted on GitHub →
                </a>
              ) : (
                canPublish && (
                  <button
                    onClick={onPublish}
                    disabled={publishing}
                    className="rounded-md bg-white px-3 py-1.5 text-sm font-medium text-gray-900 hover:bg-gray-200 disabled:opacity-50"
                  >
                    {publishing ? 'Posting…' : 'Post review to GitHub'}
                  </button>
                )
              )}
            </div>
          </div>

          {findings.length === 0 ? (
            <p className="rounded-lg border border-gray-800 bg-gray-900 p-4 text-sm text-gray-400">No issues found. 🎉</p>
          ) : (
            CATEGORIES.map(({ key, label, icon }) => {
              const items = findings.filter((f) => f.category === key)
              return (
                <section key={key}>
                  <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-gray-300">
                    <span>{icon}</span> {label} <span className="text-gray-500">({items.length})</span>
                  </h3>
                  {items.length === 0 ? (
                    <p className="text-sm text-gray-600">None</p>
                  ) : (
                    <div className="space-y-2">
                      {items.map((f, i) => (
                        <FindingCard key={i} f={f} repo={review.repo_full_name} sha={review.head_sha} />
                      ))}
                    </div>
                  )}
                </section>
              )
            })
          )}
        </>
      )}
    </div>
  )
}
