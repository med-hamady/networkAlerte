'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, updateDevice } from '@/lib/api'
import type { AlertPolicy, Device, PolicyOverride } from '@/lib/types'

const CHANNELS = ['slack', 'webhook', 'email'] as const
const CHANNEL_STYLES: Record<string, string> = {
  slack:   'bg-purple-50 text-purple-700 border-purple-200',
  email:   'bg-amber-50  text-amber-700  border-amber-200',
  webhook: 'bg-sky-50    text-sky-700    border-sky-200',
}

interface Props {
  device: Device
  onChange?: (device: Device) => void
}

export default function DevicePolicyOverridesEditor({ device, onChange }: Props) {
  const { data: policies } = useSWR<AlertPolicy[]>(endpoints.alertPolicies, fetcher)

  const overrides = device.policy_overrides ?? {}
  const overrideEntries = Object.entries(overrides)

  const [adding, setAdding] = useState(false)
  const [editingType, setEditingType] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Alert types that don't yet have an override — candidates for "add"
  const availableAlertTypes = (policies ?? [])
    .map(p => p.alert_type)
    .filter(at => !(at in overrides))

  async function persist(newOverrides: Record<string, PolicyOverride> | null) {
    setError(null)
    setSaving(true)
    try {
      const updated = await updateDevice(device.id, { policy_overrides: newOverrides })
      onChange?.(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveOverride(alertType: string, override: PolicyOverride) {
    const next = { ...overrides, [alertType]: override }
    await persist(next)
    setEditingType(null)
    setAdding(false)
  }

  async function handleRemove(alertType: string) {
    const next = { ...overrides }
    delete next[alertType]
    await persist(Object.keys(next).length === 0 ? null : next)
  }

  async function handleClearAll() {
    if (!confirm('Supprimer tous les overrides de cet équipement ?')) return
    await persist(null)
  }

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <p className="text-blue-400 text-xs uppercase tracking-widest font-semibold">
          Overrides de policy
          {overrideEntries.length > 0 && (
            <span className="ml-2 bg-blue-100 text-blue-600 px-1.5 py-0.5 rounded-full text-xs font-bold">
              {overrideEntries.length}
            </span>
          )}
        </p>
        {overrideEntries.length > 0 && (
          <button
            onClick={handleClearAll}
            disabled={saving}
            className="text-xs text-red-500 hover:text-red-700 disabled:opacity-40 underline"
          >
            Tout réinitialiser
          </button>
        )}
      </div>

      <div className="bg-white border border-blue-100 rounded-xl p-4 shadow-sm space-y-3">
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-600 px-3 py-2 rounded-lg text-xs">
            {error}
          </div>
        )}

        {overrideEntries.length === 0 && !adding && (
          <p className="text-blue-300 text-xs">
            Aucun override — ce device suit la policy globale par défaut.
          </p>
        )}

        {overrideEntries.map(([alertType, override]) => (
          <div key={alertType}>
            {editingType === alertType ? (
              <OverrideForm
                alertType={alertType}
                initial={override}
                onCancel={() => setEditingType(null)}
                onSave={(o) => handleSaveOverride(alertType, o)}
                saving={saving}
              />
            ) : (
              <OverrideSummary
                alertType={alertType}
                override={override}
                onEdit={() => setEditingType(alertType)}
                onRemove={() => handleRemove(alertType)}
                disabled={saving}
              />
            )}
          </div>
        ))}

        {adding && (
          <OverrideForm
            availableAlertTypes={availableAlertTypes}
            onCancel={() => setAdding(false)}
            onSave={(o, at) => at && handleSaveOverride(at, o)}
            saving={saving}
          />
        )}

        {!adding && availableAlertTypes.length > 0 && (
          <button
            onClick={() => setAdding(true)}
            disabled={saving}
            className="w-full px-3 py-2 border border-dashed border-blue-200 text-blue-500 hover:bg-blue-50 hover:border-blue-300 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
          >
            + Ajouter un override
          </button>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Summary row
// ---------------------------------------------------------------------------

function OverrideSummary({
  alertType, override, onEdit, onRemove, disabled,
}: {
  alertType: string
  override: PolicyOverride
  onEdit: () => void
  onRemove: () => void
  disabled: boolean
}) {
  const fields: string[] = []
  if (override.notify_immediately !== undefined)     fields.push(`notify=${override.notify_immediately}`)
  if (override.groupable !== undefined)              fields.push(`groupable=${override.groupable}`)
  if (override.recovery_notification !== undefined)  fields.push(`recovery=${override.recovery_notification}`)
  if (override.channels)                             fields.push(`channels=[${override.channels.join(',')}]`)

  return (
    <div className="flex items-start justify-between gap-3 bg-blue-50/40 border border-blue-100 rounded-lg p-2.5">
      <div className="flex-1 min-w-0 space-y-1">
        <code className="font-mono text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
          {alertType}
        </code>
        <p className="text-xs text-slate-600 font-mono break-all">
          {fields.length === 0 ? '(empty override)' : fields.join(' · ')}
        </p>
      </div>
      <div className="flex gap-1.5 shrink-0">
        <button
          onClick={onEdit} disabled={disabled}
          className="text-xs text-blue-500 hover:text-blue-700 disabled:opacity-40 underline"
        >
          Éditer
        </button>
        <button
          onClick={onRemove} disabled={disabled}
          className="text-xs text-red-500 hover:text-red-700 disabled:opacity-40 underline"
        >
          Supprimer
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Form (add or edit)
// ---------------------------------------------------------------------------

function OverrideForm({
  alertType,
  availableAlertTypes,
  initial,
  onCancel,
  onSave,
  saving,
}: {
  alertType?: string
  availableAlertTypes?: string[]
  initial?: PolicyOverride
  onCancel: () => void
  onSave: (override: PolicyOverride, alertType?: string) => void
  saving: boolean
}) {
  const [selectedType, setSelectedType] = useState(availableAlertTypes?.[0] ?? '')
  const [notify, setNotify] = useState<boolean | undefined>(initial?.notify_immediately)
  const [groupable, setGroupable] = useState<boolean | undefined>(initial?.groupable)
  const [recovery, setRecovery] = useState<boolean | undefined>(initial?.recovery_notification)
  const [channels, setChannels] = useState<string[] | undefined>(initial?.channels)

  function toggleChannel(c: string) {
    const current = channels ?? []
    if (current.includes(c)) {
      const next = current.filter(x => x !== c)
      setChannels(next.length === 0 && !channels ? undefined : next)
    } else {
      setChannels([...current, c])
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const override: PolicyOverride = {}
    if (notify    !== undefined) override.notify_immediately    = notify
    if (groupable !== undefined) override.groupable             = groupable
    if (recovery  !== undefined) override.recovery_notification = recovery
    if (channels  !== undefined) override.channels              = channels
    onSave(override, alertType ?? selectedType)
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white border border-blue-200 rounded-lg p-3 space-y-3 shadow-sm">

      {/* Alert type selector (add mode only) */}
      {!alertType && availableAlertTypes && (
        <div>
          <label className="block text-xs font-semibold text-blue-500 uppercase tracking-wider mb-1">
            Alert type
          </label>
          <select
            required value={selectedType}
            onChange={(e) => setSelectedType(e.target.value)}
            className="w-full px-2 py-1.5 border border-blue-200 rounded text-xs focus:outline-none focus:ring-2 focus:ring-blue-300"
          >
            <option value="" disabled>— Choisir —</option>
            {availableAlertTypes.map(at => <option key={at} value={at}>{at}</option>)}
          </select>
        </div>
      )}
      {alertType && (
        <p className="text-xs text-slate-600">
          Override pour <code className="font-mono bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded">{alertType}</code>
        </p>
      )}

      {/* Tri-state toggles: undefined = use base policy, true/false = override */}
      <TriToggle label="notify_immediately"    value={notify}    onChange={setNotify} />
      <TriToggle label="groupable"             value={groupable} onChange={setGroupable} />
      <TriToggle label="recovery_notification" value={recovery}  onChange={setRecovery} />

      {/* Channels override */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs font-semibold text-blue-500 uppercase tracking-wider">
            Canaux
          </label>
          <button
            type="button"
            onClick={() => setChannels(channels === undefined ? [] : undefined)}
            className="text-[11px] text-blue-400 hover:text-blue-600 underline"
          >
            {channels === undefined ? 'Override' : 'Hériter'}
          </button>
        </div>
        {channels === undefined ? (
          <p className="text-[11px] text-blue-300 italic">Hérité de la policy globale</p>
        ) : (
          <div className="flex gap-1.5 flex-wrap">
            {CHANNELS.map(c => {
              const active = channels.includes(c)
              return (
                <button
                  key={c} type="button" onClick={() => toggleChannel(c)}
                  className={`px-2 py-0.5 rounded text-[11px] font-mono border transition-all ${
                    active ? CHANNEL_STYLES[c] : 'bg-white border-slate-200 text-slate-400'
                  }`}
                >
                  {active ? '✓ ' : ''}{c}
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className="flex gap-1.5 pt-1">
        <button
          type="submit" disabled={saving || (!alertType && !selectedType)}
          className="px-3 py-1.5 bg-blue-600 text-white rounded text-xs font-medium hover:bg-blue-700 transition-colors shadow-sm disabled:opacity-40"
        >
          Enregistrer
        </button>
        <button
          type="button" onClick={onCancel} disabled={saving}
          className="px-3 py-1.5 bg-white border border-blue-200 text-blue-600 rounded text-xs font-medium hover:bg-blue-50 transition-colors"
        >
          Annuler
        </button>
      </div>
    </form>
  )
}

// ---------------------------------------------------------------------------
// Tri-state toggle (true / false / undefined = inherit)
// ---------------------------------------------------------------------------

function TriToggle({
  label, value, onChange,
}: {
  label: string
  value: boolean | undefined
  onChange: (v: boolean | undefined) => void
}) {
  const opts: { v: boolean | undefined; label: string; cls: string }[] = [
    { v: undefined, label: 'Hériter', cls: 'bg-white border-slate-200 text-slate-500' },
    { v: true,      label: 'true',    cls: 'bg-green-50 border-green-200 text-green-700' },
    { v: false,     label: 'false',   cls: 'bg-red-50 border-red-200 text-red-600' },
  ]
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-slate-600 font-mono">{label}</span>
      <div className="flex gap-1">
        {opts.map(o => (
          <button
            key={String(o.v)} type="button" onClick={() => onChange(o.v)}
            className={`px-2 py-0.5 rounded text-[10px] font-medium border transition-all ${
              value === o.v ? o.cls : 'bg-transparent border-transparent text-slate-300 hover:text-slate-500'
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}
