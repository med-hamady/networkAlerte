/**
 * Server-side proxy for the backend API.
 *
 * The browser calls /api/proxy/<path> ; this handler relays the request to
 * the backend at /api/v1/<path>. Two important properties:
 *
 * - **No more API key injection.** Authentication is now carried by the
 *   `session` cookie set on a successful /auth/login. The cookie is
 *   forwarded transparently both ways (request `Cookie` → upstream,
 *   response `Set-Cookie` → browser). The backend dependency
 *   `require_user_or_api_key` accepts the session cookie alone, so the
 *   dashboard works without ever touching the API key. Admin scripts
 *   that talk directly to /api/v1/ keep using X-API-Key — they don't go
 *   through this proxy.
 *
 * - **Same-origin guard on every method, reads included.** A direct hit
 *   on /api/proxy/... from an external tool is refused (403). The
 *   browser-set `Sec-Fetch-Site: same-origin` header is the primary
 *   signal — it cannot be forged from a page script — with
 *   `Origin`/`Referer` host-match as a fallback for older clients.
 *
 * Born of the 2026-05-17 incident: an automated scanner reached this
 * proxy on a publicly-exposed nginx, the previous version injected the
 * secret API key on every method, and the entire device inventory was
 * destroyed in ~15 hours.
 */

import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://backend:8000'

// Headers we never forward back to the browser — they come from the upstream
// fetch response and would confuse Next.js / break decompression.
// `set-cookie` is deliberately NOT in this set: we want the browser to see
// the session cookie that /auth/login emits.
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'content-encoding',
  'content-length',
])

/**
 * Returns true if a request can be proven to come from our own page (any
 * method — reads included, since the relay carries the user's session
 * cookie). Primary signal: the browser-set `Sec-Fetch-Site` header
 * (unspoofable from scripts). Fallback: `Origin`/`Referer` host equals
 * the request host.
 */
function isSameOriginRequest(req: NextRequest): boolean {
  const secFetchSite = req.headers.get('sec-fetch-site')
  if (secFetchSite) {
    return secFetchSite === 'same-origin'
  }

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

  return false
}

async function proxy(req: NextRequest, ctx: { params: { path: string[] } }) {
  const path = ctx.params.path?.join('/') ?? ''
  const search = req.nextUrl.search
  const target = `${BACKEND_URL}/api/v1/${path}${search}`

  if (!isSameOriginRequest(req)) {
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
  // Forward the session cookie so the backend can identify the user. This is
  // the new authentication carrier (the API key is no longer injected here).
  const cookie = req.headers.get('cookie')
  if (cookie) headers.set('cookie', cookie)

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
