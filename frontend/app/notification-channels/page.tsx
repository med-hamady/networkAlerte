'use client'

import { useState } from 'react'
import useSWR from 'swr'
import {
  createNotificationChannel,
  deleteNotificationChannel,
  endpoints,
  fetcher,
  updateNotificationChannel,
} from '@/lib/api'
import type { NotificationChannel, NotificationChannelInput } from '@/lib/types'

const CHANNEL_TYPES = ['email'] as const
type ChannelType = (typeof CHANNEL_TYPES)[number]

function emptyConfig(channelType: string): Record<string, unknown> {
  if (channelType === 'email') return { recipients: [] as string[] }
  return {}
}

function describeConfig(c: NotificationChannel): string {
  const cfg = c.config ?? {}
  if (c.channel_type === 'email') {
    const list = Array.isArray(cfg.recipients) ? cfg.recipients : []
    return list.length ? list.join(', ') : '—'
  }
  return JSON.stringify(cfg)
}

export default function NotificationChannelsPage() {
  const { data: channels, isLoading, mutate } = useSWR<NotificationChannel[]>(
    endpoints.notificationChannels, fetcher, { refreshInterval: 30_000 },
  )

  const [editing, setEditing]     = useState<NotificationChannel | null>(null)
  const [creating, setCreating]   = useState(false)
  const [error, setError]         = useState<string | null>(null)

  async function handleSave(input: NotificationChannelInput, id?: number) {
    setError(null)
    try {
      if (id) await updateNotificationChannel(id, input)
      else    await createNotificationChannel(input)
      await mutate()
      setEditing(null)
      setCreating(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
    }
  }

  async function handleDelete(id: number) {
    if (!confirm('Supprimer ce canal ?')) return
    setError(null)
    try {
      await deleteNotificationChannel(id)
      await mutate()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
    }
  }

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Canaux de notification</h1>
          <p className="text-blue-400 text-sm mt-1">
            Configurez les destinataires email. Si aucun canal n&apos;est activé en
            base, le système retombe sur les variables d&apos;environnement.
          </p>
        </div>
        <button
          onClick={() => { setCreating(true); setEditing(null) }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm"
        >
          + Nouveau canal
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl text-sm">
          Erreur : {error}
        </div>
      )}

      {(creating || editing) && (
        <ChannelForm
          initial={editing ?? undefined}
          onCancel={() => { setEditing(null); setCreating(false); setError(null) }}
          onSave={(data) => handleSave(data, editing?.id)}
        />
      )}

      <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
        {isLoading ? (
          <div className="px-6 py-12 text-center text-blue-300">Chargement…</div>
        ) : !channels?.length ? (
          <div className="px-6 py-12 text-center">
            <p className="text-blue-400">Aucun canal en base — fallback variables d&apos;environnement actif.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['#', 'Nom', 'Type', 'Configuration', 'Actif', 'Actions'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {channels.map(c => (
                  <tr key={c.id} className="hover:bg-blue-50/50 transition-colors">
                    <td className="px-4 py-3 text-blue-300 font-mono text-xs">{c.id}</td>
                    <td className="px-4 py-3 font-medium text-slate-800">{c.name}</td>
                    <td className="px-4 py-3">
                      <code className="font-mono text-xs bg-blue-50 text-blue-700 border border-blue-200 px-1.5 py-0.5 rounded">
                        {c.channel_type}
                      </code>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-600 max-w-md truncate" title={describeConfig(c)}>
                      {describeConfig(c)}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 text-xs font-semibold ${
                        c.enabled ? 'text-green-600' : 'text-blue-300'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${c.enabled ? 'bg-green-500' : 'bg-blue-200'}`} />
                        {c.enabled ? 'Activé' : 'Désactivé'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1.5">
                        <button
                          onClick={() => { setEditing(c); setCreating(false) }}
                          className="px-2.5 py-1 text-xs bg-blue-50 text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors"
                        >
                          Modifier
                        </button>
                        <button
                          onClick={() => handleDelete(c.id)}
                          className="px-2.5 py-1 text-xs bg-red-50 text-red-600 border border-red-200 rounded-lg hover:bg-red-100 transition-colors"
                        >
                          Supprimer
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Form
// ---------------------------------------------------------------------------

function ChannelForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: NotificationChannel
  onSave: (data: NotificationChannelInput) => void
  onCancel: () => void
}) {
  const [name, setName]                 = useState(initial?.name ?? '')
  const [channelType, setChannelType]   = useState<ChannelType>('email')
  const [enabled, setEnabled]           = useState(initial?.enabled ?? true)
  const [recipients, setRecipients]     = useState(
    initial?.channel_type === 'email' && Array.isArray(initial.config?.recipients)
      ? (initial.config.recipients as string[]).join(', ')
      : '',
  )

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const config: Record<string, unknown> = {
      recipients: recipients.split(',').map(s => s.trim()).filter(Boolean),
    }
    onSave({ name: name.trim(), channel_type: channelType, config, enabled })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white border border-blue-200 rounded-xl shadow-sm p-5 space-y-4"
    >
      <h2 className="text-sm font-semibold text-blue-900">
        {initial ? `Modifier le canal #${initial.id}` : 'Nouveau canal'}
      </h2>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-semibold text-blue-500 uppercase tracking-wider mb-1">Nom</label>
          <input
            type="text" required value={name} onChange={(e) => setName(e.target.value)}
            placeholder="ops-slack"
            className="w-full px-3 py-2 border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-blue-500 uppercase tracking-wider mb-1">Type</label>
          <select
            value={channelType}
            onChange={(e) => setChannelType(e.target.value as ChannelType)}
            disabled={!!initial}
            className="w-full px-3 py-2 border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:bg-blue-50"
          >
            {CHANNEL_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      {channelType === 'email' && (
        <div>
          <label className="block text-xs font-semibold text-blue-500 uppercase tracking-wider mb-1">
            Destinataires (séparés par virgule)
          </label>
          <input
            type="text" required value={recipients} onChange={(e) => setRecipients(e.target.value)}
            placeholder="ops@company.com, oncall@company.com"
            className="w-full px-3 py-2 border border-blue-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
          <p className="text-xs text-blue-400 mt-1">
            Le serveur SMTP utilise les variables d&apos;env (SMTP_HOST, SMTP_USERNAME, etc.).
          </p>
        </div>
      )}

      <div>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700 cursor-pointer">
          <input
            type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)}
            className="w-4 h-4 accent-blue-600"
          />
          Activé
        </label>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="submit"
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm"
        >
          {initial ? 'Enregistrer' : 'Créer'}
        </button>
        <button
          type="button" onClick={onCancel}
          className="px-4 py-2 bg-white border border-blue-200 text-blue-600 rounded-lg text-sm font-medium hover:bg-blue-50 transition-colors"
        >
          Annuler
        </button>
      </div>
    </form>
  )
}
