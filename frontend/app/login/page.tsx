'use client'

import { useState, FormEvent, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import { firstAllowedRoute } from '@/lib/permissions'

/**
 * Login page — deux volets, sans sidebar : le formulaire à gauche, le
 * panneau de marque A2 ICT à droite (masqué sur petit écran).
 *
 * Posts {username, password} to /api/proxy/auth/login. On success the
 * backend sets the session cookie via Set-Cookie (forwarded by the proxy),
 * and we redirect to the page the user was originally trying to visit
 * (carried in `?next=` by the auth middleware) — or `/` by default.
 */
function LoginForm() {
  const searchParams = useSearchParams()
  const next = searchParams.get('next') || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPassword, setShowPassword] = useState(false)

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
      // ⚠️ Rechargement COMPLET, pas `router.replace` : une navigation douce
      // garde toute la mémoire de la page précédente, cache SWR compris. Or
      // celui-ci retient l'erreur 401 de la session qui vient d'expirer, sur
      // la clé /auth/me — et la barre latérale, en se remontant, la recevait
      // du cache avant même d'avoir redemandé quoi que ce soit : elle
      // repartait vers /login alors que le cookie tout neuf était valide.
      // L'utilisateur devait s'y reprendre à plusieurs fois, sans jamais voir
      // d'erreur, ses identifiants étant corrects depuis le début. Les logs
      // nginx du 2026-09-21 le montrent en creux : POST /auth/login 200, puis
      // AUCUN appel à /auth/me, puis un second POST — la décision de le
      // renvoyer au login était prise sans que le serveur ait été consulté.
      //
      // Un rechargement repart d'une page vierge : aucun état ne survit à la
      // connexion, ce qui est de toute façon ce qu'on veut au changement
      // d'utilisateur (les données du compte précédent ne traînent pas). Il
      // coûte un chargement de page, une fois par connexion.
      window.location.assign(target)
    } catch (err) {
      setError((err as Error).message || 'Erreur réseau.')
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex bg-white">
      {/* ── Formulaire (gauche) ─────────────────────────────────────────── */}
      <div className="w-full lg:w-[440px] shrink-0 flex flex-col px-8 sm:px-14 py-10">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brand/a2ict-logo.png" alt="A2 ICT" className="w-32 h-auto" />

        <div className="flex-1 flex flex-col justify-center py-10">
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Connexion</h1>
          <p className="text-slate-500 text-sm mt-1">
            Accédez à la supervision du réseau.
          </p>

          <form onSubmit={onSubmit} className="mt-10 space-y-7">
            <Field
              id="username"
              label="Nom d'utilisateur"
              type="text"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={setUsername}
            />
            <Field
              id="password"
              label="Mot de passe"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={setPassword}
              trailing={
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="text-xs font-medium text-blue-600 hover:text-blue-900 transition-colors"
                  tabIndex={-1}
                >
                  {showPassword ? 'Masquer' : 'Afficher'}
                </button>
              }
            />

            {error && (
              <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !username || !password}
              className="w-full py-3 rounded-lg text-sm font-semibold text-white shadow-sm
                         bg-gradient-to-r from-blue-900 to-blue-600
                         hover:from-blue-950 hover:to-blue-700
                         disabled:from-blue-200 disabled:to-blue-100 disabled:text-blue-400 disabled:shadow-none
                         transition-colors"
            >
              {submitting ? 'Connexion…' : 'Se connecter'}
            </button>
          </form>
        </div>

        <p className="text-xs text-slate-400">
          © {new Date().getFullYear()} A2 ICT — Accès interne
        </p>
      </div>

      {/* ── Panneau de marque (droite) — masqué sur petit écran ───────────── */}
      <div className="hidden lg:flex flex-1 relative overflow-hidden items-center justify-center
                      bg-gradient-to-br from-blue-950 via-blue-900 to-blue-600">
        {/* Halos lumineux */}
        <div className="absolute -top-40 -right-40 w-[36rem] h-[36rem] rounded-full bg-blue-300/20 blur-3xl" />
        <div className="absolute -bottom-48 -left-32 w-[32rem] h-[32rem] rounded-full bg-blue-200/10 blur-3xl" />
        {/* Symbole ruban en filigrane */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/brand/a2ict-mark-white.png"
          alt=""
          aria-hidden
          className="absolute -right-24 -bottom-24 w-[34rem] h-auto opacity-[0.07] select-none pointer-events-none"
        />
        {/* Anneaux fins */}
        <svg className="absolute inset-0 w-full h-full opacity-[0.08]" aria-hidden>
          <circle cx="15%" cy="20%" r="140" fill="none" stroke="white" strokeWidth="1" />
          <circle cx="15%" cy="20%" r="220" fill="none" stroke="white" strokeWidth="1" />
          <circle cx="15%" cy="20%" r="300" fill="none" stroke="white" strokeWidth="1" />
        </svg>

        <div className="relative z-10 text-center px-12">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/a2ict-logo-white.png" alt="A2 ICT" className="mx-auto w-[26rem] max-w-full h-auto drop-shadow-lg" />
          <div className="mt-12 inline-flex items-center gap-3 px-4 py-1.5 rounded-full border border-white/20 bg-white/5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span className="text-white/90 text-sm font-medium tracking-wide">Network Management</span>
          </div>
          <p className="mt-4 text-blue-100/80 text-sm max-w-md mx-auto leading-relaxed">
            Gestion et supervision du réseau radio, des sites et des accès clients.
          </p>
        </div>
      </div>
    </div>
  )
}

/** Champ souligné, dans l'esprit de l'écran de connexion UISP. */
function Field({
  id, label, type, autoComplete, autoFocus, value, onChange, trailing,
}: {
  id: string
  label: string
  type: string
  autoComplete: string
  autoFocus?: boolean
  value: string
  onChange: (v: string) => void
  trailing?: React.ReactNode
}) {
  return (
    <div className="group">
      <label
        htmlFor={id}
        className="block text-xs font-medium text-slate-500 group-focus-within:text-blue-600 transition-colors"
      >
        {label}
      </label>
      <div className="flex items-center border-b border-slate-300 group-focus-within:border-blue-600 transition-colors">
        <input
          id={id}
          type={type}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          required
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 min-w-0 bg-transparent py-2 text-sm text-slate-800 focus:outline-none"
        />
        {trailing}
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
    '/site-links': 'lr_health.view',
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
