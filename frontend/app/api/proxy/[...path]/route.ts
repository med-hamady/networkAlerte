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
 */

import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://backend:8000'
const API_KEY = process.env.API_KEY ?? ''

// Headers we never forward back to the browser (they come from the upstream
// fetch response and would confuse Next.js / break decompression).
const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'content-encoding',
  'content-length',
])

async function proxy(req: NextRequest, ctx: { params: { path: string[] } }) {
  const path = ctx.params.path?.join('/') ?? ''
  const search = req.nextUrl.search
  const target = `${BACKEND_URL}/api/v1/${path}${search}`

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
