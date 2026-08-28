// The one place that knows how to talk to /api.
//
// Every mutating request echoes the non-HttpOnly `sundial_csrf` cookie back in
// an `X-CSRF-Token` header — the double-submit half of §12. Errors arrive as
// RFC 9457 problem+json (§11).

export type ConnectionState = 'connected' | 'needs_reconnect' | 'disconnected'

export interface Connection {
  state: ConnectionState
  email: string | null
  scopes: string[]
  connected_at: string | null
}

export interface Me {
  env: 'dev' | 'prod'
  connection: Connection
}

export interface Problem {
  type: string
  title: string
  status: number
  detail?: string
  instance?: string
}

export class ApiError extends Error {
  readonly problem: Problem

  constructor(problem: Problem) {
    super(problem.title)
    this.name = 'ApiError'
    this.problem = problem
  }
}

function csrfToken(): string | undefined {
  return document.cookie
    .split('; ')
    .find((entry) => entry.startsWith('sundial_csrf='))
    ?.slice('sundial_csrf='.length)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)

  if (method !== 'GET' && method !== 'HEAD') {
    const token = csrfToken()
    if (token) headers.set('X-CSRF-Token', token)
  }

  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: 'same-origin',
  })

  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as Problem | null
    throw new ApiError(
      problem ?? { type: 'about:blank', title: response.statusText, status: response.status },
    )
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}

export const api = {
  me: () => request<Me>('/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  loginUrl: '/api/auth/login',
}
