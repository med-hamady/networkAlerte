'use client'

/**
 * Formulaire d'un profil — le catalogue complet des fonctionnalités, à cocher.
 *
 * ⚠️ **Rien n'est écrit en dur ici.** Les groupes, les libellés et les
 * descriptions viennent de `GET /access-control/permissions`, donc de
 * `app/core/permissions.py`. Ajouter une fonctionnalité cochable côté backend
 * la fait apparaître dans cet écran sans qu'on y touche — c'est la raison
 * d'être du catalogue.
 *
 * ⚠️ **Les « pages » et les « actions » sont séparées visuellement**, parce que
 * la distinction porte tout l'intérêt du système : un agent peut voir la page
 * FAI sans pouvoir couper un abonné. Les mélanger dans une liste à plat ferait
 * cocher les deux ensemble sans y penser.
 */

import { useMemo, useState } from 'react'
import type { AccessProfile, PermissionGroup } from '@/lib/api'

interface Props {
  groups: PermissionGroup[]
  /** Profil en cours d'édition, ou `null` pour une création. */
  profile: AccessProfile | null
  saving: boolean
  onCancel: () => void
  onSave: (payload: { name: string; description: string; permissions: string[] }) => void
}

export default function ProfileEditor({ groups, profile, saving, onCancel, onSave }: Props) {
  const [name, setName] = useState(profile?.name ?? '')
  const [description, setDescription] = useState(profile?.description ?? '')
  const [checked, setChecked] = useState<Set<string>>(
    () => new Set(profile?.permissions ?? []),
  )

  const total = useMemo(
    () => groups.reduce((n, g) => n + g.permissions.length, 0),
    [groups],
  )

  const toggle = (key: string) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const toggleGroup = (group: PermissionGroup, on: boolean) => {
    setChecked((prev) => {
      const next = new Set(prev)
      for (const perm of group.permissions) {
        if (on) next.add(perm.key)
        else next.delete(perm.key)
      }
      return next
    })
  }

  const canSave = name.trim().length > 0 && !saving

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm">

      {/* En-tête : identité du profil */}
      <div className="px-6 py-5 border-b border-slate-200">
        <h2 className="text-lg font-bold text-slate-900">
          {profile ? `Modifier « ${profile.name} »` : 'Nouveau profil'}
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          Coche ce que ce profil a le droit de voir et de faire. Les comptes qui
          le portent n'auront accès à rien d'autre.
        </p>

        <div className="grid gap-4 sm:grid-cols-2 mt-4">
          <label className="block">
            <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Nom du profil
            </span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Agent"
              maxLength={64}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="block">
            <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Description (optionnelle)
            </span>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Support niveau 1 — consultation et diagnostics"
              maxLength={500}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
        </div>
      </div>

      {/* Le catalogue */}
      <div className="px-6 py-5 space-y-6 max-h-[55vh] overflow-y-auto">
        {groups.map((group) => {
          const pages = group.permissions.filter((p) => p.kind === 'page')
          const actions = group.permissions.filter((p) => p.kind === 'action')
          const onCount = group.permissions.filter((p) => checked.has(p.key)).length
          const allOn = onCount === group.permissions.length

          return (
            <section key={group.key}>
              <div className="flex items-start justify-between gap-4 pb-2 border-b border-slate-200">
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-slate-900">{group.label}</h3>
                  <p className="text-xs text-slate-500 mt-0.5">{group.description}</p>
                </div>
                <button
                  type="button"
                  onClick={() => toggleGroup(group, !allOn)}
                  className="shrink-0 text-xs font-medium text-blue-700 hover:text-blue-900
                             hover:underline whitespace-nowrap"
                >
                  {allOn ? 'Tout décocher' : 'Tout cocher'}
                  <span className="ml-1 text-slate-400">
                    ({onCount}/{group.permissions.length})
                  </span>
                </button>
              </div>

              {pages.length > 0 && (
                <PermissionBlock
                  title="Interfaces visibles"
                  hint="Les écrans que ce profil peut ouvrir."
                  items={pages}
                  checked={checked}
                  onToggle={toggle}
                />
              )}
              {actions.length > 0 && (
                <PermissionBlock
                  title="Actions autorisées"
                  hint="Les gestes que ce profil peut poser."
                  items={actions}
                  checked={checked}
                  onToggle={toggle}
                />
              )}
            </section>
          )
        })}
      </div>

      {/* Pied : compteur + validation */}
      <div className="px-6 py-4 border-t border-slate-200 flex items-center justify-between gap-4">
        <p className="text-sm text-slate-600">
          <span className="font-bold text-slate-900">{checked.size}</span>
          <span className="text-slate-400"> / {total}</span> droit(s) accordé(s)
          {checked.size === 0 && (
            <span className="ml-2 text-amber-700">
              — un profil sans aucun droit se connecte sur un écran vide.
            </span>
          )}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-700
                       hover:bg-slate-100 transition-colors"
          >
            Annuler
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={() => onSave({
              name: name.trim(),
              description: description.trim(),
              permissions: [...checked],
            })}
            className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-blue-700
                       hover:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed
                       transition-colors"
          >
            {saving ? 'Enregistrement…' : profile ? 'Enregistrer' : 'Créer le profil'}
          </button>
        </div>
      </div>
    </div>
  )
}


function PermissionBlock({
  title, hint, items, checked, onToggle,
}: {
  title: string
  hint: string
  items: PermissionGroup['permissions']
  checked: Set<string>
  onToggle: (key: string) => void
}) {
  return (
    <div className="mt-3">
      <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
        {title} <span className="font-normal normal-case tracking-normal">— {hint}</span>
      </p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        {items.map((perm) => {
          const on = checked.has(perm.key)
          return (
            <label
              key={perm.key}
              className={`flex gap-2.5 items-start rounded-lg border px-3 py-2 cursor-pointer
                          transition-colors ${
                on
                  ? 'border-blue-300 bg-blue-50'
                  : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              <input
                type="checkbox"
                checked={on}
                onChange={() => onToggle(perm.key)}
                className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-blue-700
                           focus:ring-blue-500"
              />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-slate-900">{perm.label}</span>
                <span className="block text-xs text-slate-500 leading-snug">
                  {perm.description}
                </span>
              </span>
            </label>
          )
        })}
      </div>
    </div>
  )
}
