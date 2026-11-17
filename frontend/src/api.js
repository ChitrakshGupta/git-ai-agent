/**
 * API helper: attaches the JWT when signed in. Signed-out calls are allowed
 * (reviewing a public PR link needs no account).
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

export const isSignedIn = () => Boolean(localStorage.getItem('token'))

export function signOut() {
  localStorage.removeItem('token')
}

export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('token')
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  })
  if (res.status === 401 && token) {
    // Expired or invalid session: drop it and let the page show the signed-out state.
    signOut()
  }
  return res
}

export async function apiJson(path, options = {}) {
  const res = await apiFetch(path, options)
  let data = null
  try {
    data = await res.json()
  } catch {
    // empty body
  }
  if (!res.ok) {
    throw new Error(data?.detail || `Request failed (${res.status})`)
  }
  return data
}

export const loginUrl = `${API_BASE}/auth/github`
