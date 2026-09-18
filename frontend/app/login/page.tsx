'use client'

import { useState, FormEvent, Suspense } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { firstAllowedRoute } from '@/lib/permissions'

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
      // ⚠️ La destination dépend des DROITS du compte, pas d'une valeur fixe.
      // `/` est lui-même une permission (`dashboard.view`) : un profil qui ne
      // l'a pas atterrissait sur « Accès refusé » juste après s'être connecté
      // — l'application paraissait cassée alors qu'elle fonctionnait. La
      // réponse du login porte déjà les droits (`user.permissions`), donc on
      // sait tout de suite où l'envoyer, sans requête de plus.
      const payload = await res.json().catch(() => null)
      const granted = new Set<string>(payload?.user?.permissions ?? [])
      const isAdmin = payload?.user?.is_admin === true
      const can = (...keys: string[]) =>
        isAdmin || keys.some((k) => granted.has(k))

      // Une destination demandée (`?next=`) n'est honorée que si le compte a
      // le droit de la voir : sinon un signet partagé renverrait l'agent sur
      // un refus, au lieu de son écran de travail.
      const nextPermission = PAGE_PERMISSION_FOR(next)
      // Clé vide = page non gardée, donc ouverte à tout compte connecté.
      const nextAllowed = next !== '/'
        && (nextPermission === '' || can(nextPermission))
      const target = nextAllowed ? next : firstAllowedRoute(can) ?? '/'
      router.replace(target)
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

/**
 * Le droit qui ouvre une destination `?next=`, ou une chaîne vide si la page
 * n'est pas gardée (elle est alors ouverte à tout compte connecté).
 *
 * ⚠️ Volontairement approximatif — il ne sert qu'à choisir où ATTERRIR. Le
 * vrai contrôle est celui d'`AppShell` à l'affichage, et celui du backend sur
 * chaque route. Se tromper ici ne donne accès à rien : au pire l'utilisateur
 * arrive sur l'écran « Accès refusé », qui lui propose une sortie.
 */
function PAGE_PERMISSION_FOR(path: string): string {
  const route = path.split('?')[0]
  const key = Object.entries({
    '/': 'dashboard.view',
    '/sites': 'sites.view',
    '/lr-health': 'lr_health.view',
    '/clients': 'clients.view',
    '/capacity': 'capacity.view',
    '/topology': 'topology.view',
    '/map': 'map.view',
    '/traffic': 'traffic.view',
    '/access': 'fai.view',
    '/fai-requests': 'fai.requests.view',
    '/fai-journal': 'fai.journal.view',
    '/router-rules': 'fai.router_rules.view',
    '/content-block': 'fai.content_filter.view',
    '/incidents': 'incidents.view',
    '/access-diagnostics': 'access_diagnostics.view',
    '/reports': 'reports.view',
    '/settings': 'thresholds.view',
    '/admin': 'admin.access',
  }).find(([r]) => r === route)?.[1]
  return key ?? ''
}
