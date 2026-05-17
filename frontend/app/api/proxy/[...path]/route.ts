/**
 * Server-side proxy for the backend API.
 *
 * The browser calls /api/proxy/<path> ; this handler appends the path to
 * the backend base URL and injects the X-API-Key header on the server side.
 * The API key never leaves the server, so it stays out of any client bundle.
 *
 * Configure with two server-only env vars (no NEXT_PUBLIC_ prefix):
 *   - BACKEND_URL : where the backend lives (default http://backend:8000)
 *   - API_KEY     : the secret expected by the FastAPI verify_api_key dependency
 *
 * ───────────────────────────────────────────────────────────────────────────
 * SECURITY — Fetch Metadata same-origin guard (incident 2026-05-17)
 *
 * This proxy injects the secret API key on EVERY request with no auth of its
 * own — by design, so the unauthenticated dashboard can talk to the backend.
 * On 2026-05-17 an automated scanner (subnet 85.203.47.0/24) reached the
 * publicly-exposed site and called POST/PUT/DELETE /api/proxy/devices/...
 * directly: the proxy dutifully added the key and the whole device inventory
 * was wiped (Rockets, Switch, Power, modems).
 *
 * Defense in depth: a state-changing request (POST/PUT/PATCH/DELETE) is only
 * relayed if it provably originates from our own page. We trust the browser-
 * set `Sec-Fetch-Site: same-origin` header — it is a forbidden header name, so
 * page scripts (and therefore XHR/fetch) cannot set or spoof it; tools like
 * curl/sqlmap simply don't send it. `Origin`/`Referer` host-match is kept as a
 * fallback for older clients. GET/HEAD stay open (read-only, and the real
 * perimeter is now nginx bound to 127.0.0.1 + SSH tunnel — not the Internet).
 * ───────────────────────────────────────────────────────────────────────────
 */

import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://backend:8000'
const API_KEY = process.env.API_KEY ?? ''

// The same-origin guard applies to EVERY method, reads included: the proxy
// injects the secret API key, so a relayed GET is an authenticated read. An
// attacker hitting /api/proxy/... directly must not get data back either
// (information disclosure). The dashboard's own fetch() calls are same-origin
// and therefore unaffected.

// Headers we never forward back to the browser (they come from the upstream
// fetch response and would confuse Next.js / break decompression).
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'content-encoding',
  'content-length',
])

/**
 * Returns true if a request can be proven to come from our own page (any
 * method — reads included, since the proxy adds the secret key). Primary
 * signal: the browser-set `Sec-Fetch-Site` header (unspoofable from scripts).
 * Fallback: `Origin`/`Referer` host equals the request host.
 */
function isSameOriginRequest(req: NextRequest): boolean {
  const secFetchSite = req.headers.get('sec-fetch-site')
  if (secFetchSite) {
    // Browser-supplied and script-immutable. The dashboard is a single origin
    // (no subdomains), so only "same-origin" is legitimate here.
    return secFetchSite === 'same-origin'
  }

  // Older clients without Fetch Metadata: fall back to Origin/Referer host.
  const selfHost = req.headers.get('host')
  if (!selfHost) return false

  const origin = req.headers.get('origin')
  if (origin) {
    try {
      return new URL(origin).host === selfHost
    } catch {
      return false
    }
  }

  const referer = req.headers.get('referer')
  if (referer) {
    try {
      return new URL(referer).host === selfHost
    } catch {
      return false
    }
  }

  // No Sec-Fetch-Site, no Origin, no Referer → not a real browser request
  // from our app. Reject (this is the curl/sqlmap shape).
  return false
}

async function proxy(req: NextRequest, ctx: { params: { path: string[] } }) {
  const path = ctx.params.path?.join('/') ?? ''
  const search = req.nextUrl.search
  const target = `${BACKEND_URL}/api/v1/${path}${search}`

  if (!isSameOriginRequest(req)) {
    // Do NOT forward — and do not inject the API key. Log for incident
    // visibility (shows up in `docker compose logs frontend`).
    console.warn(
      `[proxy] BLOCKED cross-origin ${req.method} /${path} ` +
        `sec-fetch-site=${req.headers.get('sec-fetch-site') ?? 'none'} ` +
        `origin=${req.headers.get('origin') ?? 'none'} ` +
        `ua=${req.headers.get('user-agent') ?? 'none'}`,
    )
    return NextResponse.json(
      { detail: 'Forbidden: cross-origin requests are not allowed.' },
      { status: 403 },
    )
  }

  const headers = new Headers()
  // Forward content-type only — we don't trust browser-supplied auth headers.
  const ct = req.headers.get('content-type')
  if (ct) headers.set('content-type', ct)
  if (API_KEY) headers.set('x-api-key', API_KEY)

  const init: RequestInit = {
    method: req.method,
    headers,
    // Body is forwarded for non-GET methods. Next.js exposes it as a stream.
    body: ['GET', 'HEAD'].includes(req.method) ? undefined : await req.arrayBuffer(),
    cache: 'no-store',
    redirect: 'follow',
  }

  let upstream: Response
  try {
    upstream = await fetch(target, init)
  } catch (err) {
    return NextResponse.json(
      { detail: `Upstream unreachable: ${(err as Error).message}` },
      { status: 502 },
    )
  }

  const respHeaders = new Headers()
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) respHeaders.set(key, value)
  })

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: respHeaders,
  })
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const PATCH = proxy
export const DELETE = proxy
