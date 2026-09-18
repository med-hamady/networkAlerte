'use client'

/**
 * Section Administration — profils d'accès et comptes utilisateurs.
 *
 * Le parcours que cette page sert, dans cet ordre :
 *   1. définir un PROFIL en cochant, dans le catalogue complet du système, ce
 *      qu'il a le droit de voir et de faire ;
 *   2. créer un UTILISATEUR (identifiant + mot de passe) et l'affecter à ce
 *      profil.
 *
 * ⚠️ Le catalogue de l'étape 1 est servi par le backend
 * (`GET /access-control/permissions`) et n'est recopié nulle part ici : une
 * fonctionnalité ajoutée au superviseur devient cochable sans toucher à cette
 * page. C'est ce qui empêche l'écran de mentir en oubliant une nouveauté.
 *
 * ⚠️ Ce que montre cette page n'est PAS le cloisonnement : masquer un écran
 * n'empêche rien. Chaque route du backend porte son propre contrôle de droits
 * (`deps.require_permission`), et c'est lui qui refuse.
 */

import { useCallback, useEffect, useState } from 'react'
import useSWR from 'swr'
import ProfileEditor from '@/components/ProfileEditor'
import UserEditor from '@/components/UserEditor'
import {
  createManagedUser,
  createProfile,
  deleteManagedUser,
  deleteProfile,
  endpoints,
  fetcher,
  resetManagedUserPassword,
  updateManagedUser,
  updateProfile,
  type AccessProfile,
  type ManagedUser,
  type PermissionGroup,
} from '@/lib/api'
import { PERM, usePermissions } from '@/lib/permissions'

type Tab = 'profiles' | 'users'

export default function AdminPage() {
  const { can, loading: permsLoading, user: me } = usePermissions()
  const [tab, setTab] = useState<Tab>('profiles')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [editingProfile, setEditingProfile] = useState<AccessProfile | null | undefined>(undefined)
  const [editingUser, setEditingUser] = useState<ManagedUser | null | undefined>(undefined)

  const { data: catalog } = useSWR<{ groups: PermissionGroup[] }>(
    endpoints.accessPermissions, fetcher,
  )
  const { data: profiles, mutate: reloadProfiles } = useSWR<AccessProfile[]>(
    endpoints.accessProfiles, fetcher,
  )
  const { data: users, mutate: reloadUsers } = useSWR<ManagedUser[]>(
    endpoints.accessUsers, fetcher,
  )

  const canEditProfiles = can(PERM.adminProfiles)
  const canEditUsers = can(PERM.adminUsers)

  // Un message de confirmation qui reste affiché indéfiniment finit par décrire
  // une action ancienne : on l'efface au bout de quelques secondes.
  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(t)
  }, [notice])

  const run = useCallback(async (fn: () => Promise<unknown>, ok: string) => {
    setSaving(true)
    setError(null)
    try {
      await fn()
      setNotice(ok)
      await Promise.all([reloadProfiles(), reloadUsers()])
      setEditingProfile(undefined)
      setEditingUser(undefined)
    } catch (e: unknown) {
      // Le `detail` du backend est rédigé pour l'opérateur (« ce compte est le
      // dernier administrateur actif… ») : le remplacer par un message
      // générique lui retirerait la seule indication de ce qu'il doit faire.
      setError(e instanceof Error ? e.message : 'Erreur')
    } finally {
      setSaving(false)
    }
  }, [reloadProfiles, reloadUsers])

  if (permsLoading) return null
  if (!can(PERM.adminAccess)) {
    return (
      <div className="max-w-xl mx-auto mt-16 rounded-xl border border-slate-200 bg-white p-8 text-center">
        <h1 className="text-lg font-bold text-slate-900">Accès refusé</h1>
        <p className="mt-2 text-sm text-slate-600">
          Ton profil ne donne pas accès à l'administration des comptes.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">

      <header>
        <h1 className="text-2xl font-bold text-slate-900">Profils et accès</h1>
        <p className="text-sm text-slate-600 mt-1">
          Définis des profils en cochant ce que chacun a le droit de voir et de
          faire, puis crée des comptes et affecte-leur un profil.
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </div>
      )}

      {/* Onglets */}
      <div className="flex gap-1 border-b border-slate-200">
        {([['profiles', 'Profils'], ['users', 'Utilisateurs']] as Array<[Tab, string]>)
          .map(([key, label]) => (
            <button
              key={key}
              onClick={() => { setTab(key); setEditingProfile(undefined); setEditingUser(undefined) }}
              className={`px-4 py-2.5 text-sm font-semibold border-b-2 -mb-px transition-colors ${
                tab === key
                  ? 'border-blue-700 text-blue-800'
                  : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {label}
              <span className="ml-1.5 text-xs font-normal text-slate-400">
                {key === 'profiles' ? profiles?.length ?? '' : users?.length ?? ''}
              </span>
            </button>
          ))}
      </div>

      {/* --- Profils --- */}
      {tab === 'profiles' && (
        editingProfile !== undefined && catalog ? (
          <ProfileEditor
            groups={catalog.groups}
            profile={editingProfile}
            saving={saving}
            onCancel={() => setEditingProfile(undefined)}
            onSave={(payload) => run(
              () => editingProfile
                ? updateProfile(editingProfile.id, payload)
                : createProfile(payload),
              editingProfile ? 'Profil enregistré.' : 'Profil créé.',
            )}
          />
        ) : (
          <ProfileList
            profiles={profiles}
            canEdit={canEditProfiles}
            onCreate={() => setEditingProfile(null)}
            onEdit={(p) => setEditingProfile(p)}
            onDelete={(p) => {
              if (!confirm(`Supprimer le profil « ${p.name} » ?`)) return
              run(() => deleteProfile(p.id), 'Profil supprimé.')
            }}
          />
        )
      )}

      {/* --- Utilisateurs --- */}
      {tab === 'users' && (
        editingUser !== undefined && profiles ? (
          <UserEditor
            profiles={profiles}
            user={editingUser}
            saving={saving}
            onCancel={() => setEditingUser(undefined)}
            onSave={(payload) => run(
              () => editingUser
                ? updateManagedUser(editingUser.id, {
                    full_name: payload.full_name,
                    enabled: payload.enabled,
                    profile_id: payload.profile_id,
                  })
                : createManagedUser({
                    username: payload.username!,
                    password: payload.password!,
                    profile_id: payload.profile_id,
                    full_name: payload.full_name,
                    enabled: payload.enabled,
                  }),
              editingUser ? 'Compte enregistré.' : 'Compte créé.',
            )}
          />
        ) : (
          <UserList
            users={users}
            canEdit={canEditUsers}
            currentUserId={me?.id ?? null}
            onCreate={() => setEditingUser(null)}
            onEdit={(u) => setEditingUser(u)}
            onResetPassword={(u) => {
              const pw = prompt(
                `Nouveau mot de passe pour « ${u.username} » (8 caractères minimum).\n`
                + 'Toutes ses sessions ouvertes seront fermées.',
              )
              if (pw === null) return
              if (pw.length < 8) { setError('Mot de passe trop court (8 minimum).'); return }
              run(() => resetManagedUserPassword(u.id, pw), 'Mot de passe réinitialisé.')
            }}
            onDelete={(u) => {
              if (!confirm(`Supprimer définitivement le compte « ${u.username} » ?`)) return
              run(() => deleteManagedUser(u.id), 'Compte supprimé.')
            }}
          />
        )
      )}
    </div>
  )
}


// ---------------------------------------------------------------------------

function ProfileList({
  profiles, canEdit, onCreate, onEdit, onDelete,
}: {
  profiles: AccessProfile[] | undefined
  canEdit: boolean
  onCreate: () => void
  onEdit: (p: AccessProfile) => void
  onDelete: (p: AccessProfile) => void
}) {
  if (!profiles) return <p className="text-sm text-slate-500">Chargement…</p>

  return (
    <div className="space-y-4">
      {canEdit && (
        <button
          onClick={onCreate}
          className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-blue-700
                     hover:bg-blue-800 transition-colors"
        >
          + Nouveau profil
        </button>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        {profiles.map((p) => (
          <div key={p.id} className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="font-bold text-slate-900 truncate">{p.name}</h3>
                {p.is_system && (
                  <span className="inline-block mt-1 px-2 py-0.5 rounded-full text-[10px]
                                   font-bold uppercase tracking-wide bg-amber-100 text-amber-800">
                    Profil système
                  </span>
                )}
              </div>
              <span className="shrink-0 text-xs text-slate-500">
                {p.user_count} compte{p.user_count > 1 ? 's' : ''}
              </span>
            </div>

            <p className="mt-2 text-sm text-slate-600 leading-snug">
              {p.description || <span className="text-slate-400">Aucune description.</span>}
            </p>

            <p className="mt-3 text-xs text-slate-500">
              {p.is_system
                ? 'Tous les droits, y compris les fonctionnalités ajoutées plus tard.'
                : `${p.permissions.length} droit(s) accordé(s).`}
            </p>

            {canEdit && !p.is_system && (
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => onEdit(p)}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium text-blue-800
                             bg-blue-50 hover:bg-blue-100 transition-colors"
                >
                  Modifier
                </button>
                <button
                  onClick={() => onDelete(p)}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium text-red-700
                             hover:bg-red-50 transition-colors"
                >
                  Supprimer
                </button>
              </div>
            )}
            {p.is_system && (
              <p className="mt-3 text-xs text-slate-400">
                Ce profil ne se modifie pas : il est ce qui garantit qu'il reste
                toujours quelqu'un pour administrer le superviseur.
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


function UserList({
  users, canEdit, currentUserId, onCreate, onEdit, onResetPassword, onDelete,
}: {
  users: ManagedUser[] | undefined
  canEdit: boolean
  currentUserId: number | null
  onCreate: () => void
  onEdit: (u: ManagedUser) => void
  onResetPassword: (u: ManagedUser) => void
  onDelete: (u: ManagedUser) => void
}) {
  if (!users) return <p className="text-sm text-slate-500">Chargement…</p>

  return (
    <div className="space-y-4">
      {canEdit && (
        <button
          onClick={onCreate}
          className="px-4 py-2 rounded-lg text-sm font-semibold text-white bg-blue-700
                     hover:bg-blue-800 transition-colors"
        >
          + Nouvel utilisateur
        </button>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left">
            <tr className="text-[11px] uppercase tracking-wider text-slate-500">
              <th className="px-4 py-3 font-bold">Utilisateur</th>
              <th className="px-4 py-3 font-bold">Profil</th>
              <th className="px-4 py-3 font-bold">État</th>
              <th className="px-4 py-3 font-bold">Dernière connexion</th>
              {canEdit && <th className="px-4 py-3 font-bold text-right">Actions</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-slate-50">
                <td className="px-4 py-3">
                  <span className="font-medium text-slate-900">{u.username}</span>
                  {u.id === currentUserId && (
                    <span className="ml-2 text-[10px] font-bold uppercase text-blue-700">
                      toi
                    </span>
                  )}
                  {u.full_name && (
                    <span className="block text-xs text-slate-500">{u.full_name}</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className={u.is_admin ? 'font-semibold text-amber-800' : 'text-slate-700'}>
                    {u.profile_name ?? (
                      // Un compte sans profil n'a AUCUN droit : il se connecte
                      // sur un écran vide. Le dire plutôt que d'afficher un
                      // tiret, qui se lirait comme « rien de particulier ».
                      <span className="text-red-700">Aucun profil — aucun droit</span>
                    )}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-block px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                    u.enabled ? 'bg-green-100 text-green-800' : 'bg-slate-200 text-slate-600'
                  }`}>
                    {u.enabled ? 'Actif' : 'Désactivé'}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {u.last_login_at
                    ? new Date(u.last_login_at).toLocaleString('fr-FR')
                    : <span className="text-slate-400">jamais</span>}
                </td>
                {canEdit && (
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      <button
                        onClick={() => onEdit(u)}
                        className="px-2.5 py-1 rounded-lg text-xs font-medium text-blue-800
                                   bg-blue-50 hover:bg-blue-100 transition-colors"
                      >
                        Modifier
                      </button>
                      <button
                        onClick={() => onResetPassword(u)}
                        className="px-2.5 py-1 rounded-lg text-xs font-medium text-slate-700
                                   hover:bg-slate-100 transition-colors"
                      >
                        Mot de passe
                      </button>
                      {u.id !== currentUserId && (
                        <button
                          onClick={() => onDelete(u)}
                          className="px-2.5 py-1 rounded-lg text-xs font-medium text-red-700
                                     hover:bg-red-50 transition-colors"
                        >
                          Supprimer
                        </button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
