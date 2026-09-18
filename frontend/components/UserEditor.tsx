'use client'

/**
 * Formulaire d'un compte utilisateur — création et modification.
 *
 * ⚠️ Le **nom d'utilisateur** n'est modifiable qu'à la création : c'est la clé
 * qui relie ce compte à ses traces (journaux applicatifs, agent d'une action
 * FAI). Le changer réécrirait le passé à moitié — les anciennes lignes
 * garderaient l'ancien nom sans que rien ne le dise.
 *
 * ⚠️ Le **profil est obligatoire** : un compte sans profil n'a aucun droit et
 * se connecterait sur un écran vide, sans que personne comprenne pourquoi.
 */

import { useState } from 'react'
import type { AccessProfile, ManagedUser } from '@/lib/api'

interface Props {
  profiles: AccessProfile[]
  /** Compte en cours de modification, ou `null` pour une création. */
  user: ManagedUser | null
  saving: boolean
  onCancel: () => void
  onSave: (payload: {
    username?: string
    password?: string
    full_name: string
    enabled: boolean
    profile_id: number
  }) => void
}

export default function UserEditor({ profiles, user, saving, onCancel, onSave }: Props) {
  const isNew = user === null
  const [username, setUsername] = useState(user?.username ?? '')
  const [fullName, setFullName] = useState(user?.full_name ?? '')
  const [password, setPassword] = useState('')
  const [enabled, setEnabled] = useState(user?.enabled ?? true)
  const [profileId, setProfileId] = useState<number | null>(
    user?.profile_id ?? profiles[0]?.id ?? null,
  )

  const passwordTooShort = isNew && password.length > 0 && password.length < 8
  const canSave =
    !saving &&
    profileId !== null &&
    (!isNew || (username.trim().length > 0 && password.length >= 8))

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
      <div className="px-6 py-5 border-b border-slate-200">
        <h2 className="text-lg font-bold text-slate-900">
          {isNew ? 'Nouvel utilisateur' : `Modifier « ${user!.username} »`}
        </h2>
        <p className="text-sm text-slate-500 mt-1">
          {isNew
            ? 'Un identifiant, un mot de passe, et le profil qui définit ses droits.'
            : "Le nom d'utilisateur n'est pas modifiable : il porte l'historique du compte."}
        </p>
      </div>

      <div className="px-6 py-5 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
            Nom d'utilisateur
          </span>
          <input
            value={username}
            disabled={!isNew}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="amadou"
            maxLength={64}
            autoComplete="off"
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                       disabled:bg-slate-100 disabled:text-slate-500
                       focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </label>

        <label className="block">
          <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
            Nom complet (optionnel)
          </span>
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Amadou Ba"
            maxLength={255}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                       focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </label>

        {isNew && (
          <label className="block">
            <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Mot de passe
            </span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="8 caractères minimum"
              autoComplete="new-password"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {passwordTooShort && (
              <span className="text-xs text-red-600 mt-1 block">
                8 caractères minimum.
              </span>
            )}
          </label>
        )}

        <label className="block">
          <span className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
            Profil
          </span>
          <select
            value={profileId ?? ''}
            onChange={(e) => setProfileId(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm bg-white
                       focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_system ? ' — tous les droits' : ` — ${p.permissions.length} droit(s)`}
              </option>
            ))}
          </select>
        </label>

        <label className="sm:col-span-2 flex items-start gap-2.5 rounded-lg border
                          border-slate-200 px-3 py-2.5 cursor-pointer hover:bg-slate-50">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-700 focus:ring-blue-500"
          />
          <span>
            <span className="block text-sm font-medium text-slate-900">Compte actif</span>
            <span className="block text-xs text-slate-500">
              Désactiver ferme immédiatement ses sessions ouvertes, sans effacer
              le compte ni son historique.
            </span>
          </span>
        </label>
      </div>

      <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-2">
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
            ...(isNew ? { username: username.trim().toLowerCase(), password } : {}),
            full_name: fullName.trim(),
            enabled,
            profile_id: profileId!,
          })}
          className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-blue-700
                     hover:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed
                     transition-colors"
        >
          {saving ? 'Enregistrement…' : isNew ? 'Créer le compte' : 'Enregistrer'}
        </button>
      </div>
    </div>
  )
}
