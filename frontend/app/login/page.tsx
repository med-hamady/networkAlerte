'use client'

import { useState, FormEvent, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'

/**
 * Login page — single-screen form, no sidebar.
 *
 * Posts {username, password} to /api/proxy/auth/login. On success the
 * backend sets the session cookie via Set-Cookie (forwarded by the proxy),
 * and we redirect to the page the user was originally trying to visit
 * (carried in `?next=` by the auth middleware) — or `/` by default.
 */
function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const next = searchParams.get('next') || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const res = await fetch('/api/proxy/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) {
        // Generic message — never disclose whether the user existed.
        const detail = await res.json().catch(() => ({}))
        setError(detail?.detail || 'Identifiants invalides.')
        setSubmitting(false)
        return
      }
      // Cookie now set by the backend — go to the requested page.
      router.replace(next)
    } catch (err) {
      setError((err as Error).message || 'Erreur réseau.')
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-white to-blue-50 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-white border border-blue-100 shadow-sm p-2 mb-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/a2-logo.png" alt="A2 Holding" className="w-full h-full object-contain" />
          </div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">
            Network Supervisor
          </h1>
          <p className="text-blue-400 text-sm mt-1">Connexion administrateur</p>
        </div>

        <form
          onSubmit={onSubmit}
          className="bg-white border border-blue-100 rounded-2xl shadow-sm p-6 space-y-4"
        >
          <div className="space-y-1.5">
            <label htmlFor="username" className="text-xs font-semibold text-blue-700 uppercase tracking-wider">
              Nom d&apos;utilisateur
            </label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              required
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-400"
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="password" className="text-xs font-semibold text-blue-700 uppercase tracking-wider">
              Mot de passe
            </label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-400"
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="w-full bg-blue-700 hover:bg-blue-800 disabled:bg-blue-300 text-white font-medium py-2.5 rounded-lg transition-colors text-sm"
          >
            {submitting ? 'Connexion…' : 'Se connecter'}
          </button>
        </form>

        <p className="text-center text-xs text-blue-300 mt-6">
          Accès interne — A2 Holding
        </p>
      </div>
    </div>
  )
}

export default function LoginPage() {
  // useSearchParams() needs a Suspense boundary in the app router.
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  )
}
