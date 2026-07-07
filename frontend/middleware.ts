/**
 * Auth-gating middleware — redirects to /login when the session cookie is
 * missing on a protected route.
 *
 * Runs at the Next.js edge before any page renders, so an unauthenticated
 * visitor never sees the dashboard chrome flicker. The cookie's *presence*
 * is the routing signal here; *validity* is checked by the backend on every
 * API call (an invalid cookie returns 401 and the client clears it). This is
 * intentional: the middleware does not talk to the database, so it cannot
 * verify session tokens itself — and that's fine.
 */

import { NextRequest, NextResponse } from 'next/server'

// Must match SESSION_COOKIE_NAME in backend/app/services/auth_service.py.
const SESSION_COOKIE = 'supervisor_session'

export function middleware(req: NextRequest) {
  const hasSession = req.cookies.get(SESSION_COOKIE) !== undefined

  // Already at the login page — let it render whether or not a cookie exists
  // (a logged-in user can still visit /login; the page itself can redirect).
  if (req.nextUrl.pathname === '/login') {
    return NextResponse.next()
  }

  // TEMP — aperçu topologie sans auth (à retirer avec app/topo-preview).
  if (req.nextUrl.pathname === '/topo-preview') {
    return NextResponse.next()
  }

  if (!hasSession) {
    const url = req.nextUrl.clone()
    url.pathname = '/login'
    // Remember where the user was going so the login page can hand them back.
    url.searchParams.set('next', req.nextUrl.pathname + req.nextUrl.search)
    return NextResponse.redirect(url)
  }

  return NextResponse.next()
}

// Skip the middleware for asset routes, the proxy itself, and Next internals.
// The proxy MUST run unconditionally because /api/proxy/auth/login is exactly
// the call that creates the missing session.
export const config = {
  matcher: [
    /*
     * Match every path EXCEPT:
     *   - /api/proxy/...   (the proxy passes through to the backend)
     *   - /_next/...       (Next.js static & internal)
     *   - /devices/...     (photos produit dans public/ — assets non sensibles)
     *   - /favicon.ico, /robots.txt, ...
     */
    '/((?!api/proxy|_next/static|_next/image|devices/|favicon\\.ico|robots\\.txt).*)',
  ],
}
