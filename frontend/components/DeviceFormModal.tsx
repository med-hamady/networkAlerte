'use client'

import { useEffect, useState } from 'react'
import type {
  Device,
  DeviceFormData,
  LrFormData,
  RocketFormData,
  UispPowerFormData,
  UispSwitchFormData,
} from '@/lib/types'
import { createDevice, deleteDevice, updateDevice } from '@/lib/api'

interface Props {
  open: boolean
  device: Device | null   // null = create mode, non-null = edit mode
  onClose: () => void
  onSaved: () => void
}

// Types manually creatable by the operator. LR is excluded on purpose: those
// rows are owned by the auto-discovery pipeline (the Rocket reports its peers
// via HTTP API, and discovery_service inserts/updates LR rows).
const CREATABLE_DEVICE_TYPES: Array<{ value: Exclude<DeviceFormData['device_type'], 'lr'>; label: string }> = [
  { value: 'rocket',       label: 'Rocket (LTU ou airMAX)' },
  { value: 'uisp_switch',  label: 'UISP Switch' },
  { value: 'uisp_power',   label: 'UISP Power' },
]

const TYPE_LABEL: Record<DeviceFormData['device_type'], string> = {
  rocket:       'Rocket',
  lr:           'LR (auto-découvert)',
  uisp_switch:  'UISP Switch',
  uisp_power:   'UISP Power',
}

const ROCKET_RADIO_TECHS: Array<{ value: 'ltu' | 'airmax'; label: string }> = [
  { value: 'ltu',    label: 'LTU' },
  { value: 'airmax', label: 'airMAX' },
]

const LR_MODEL_VARIANTS: Array<{ value: LrFormData['model_variant']; label: string }> = [
  { value: 'ltu_lr',       label: 'LTU LR' },
  { value: 'ltu_instant',  label: 'LTU Instant' },
  { value: 'ltu_lite',     label: 'LTU Lite' },
  { value: 'litebeam_5ac', label: 'Litebeam 5AC' },
  { value: 'litebeam_m5',  label: 'Litebeam M5' },
]

function emptyForm(type: DeviceFormData['device_type']): DeviceFormData {
  const base = {
    name: '',
    ip_address: '',
    location: '',
    snmp_community: '',
    notes: '',
  }
  switch (type) {
    case 'rocket':
      return { ...base, device_type: 'rocket', radio_tech: 'ltu', ssh_username: '', ssh_password: '', ssh_port: 443 }
    case 'lr':
      return { ...base, device_type: 'lr', model_variant: 'ltu_lr', rocket_id: null, ssh_username: '', ssh_password: '', ssh_port: 22 }
    case 'uisp_power':
      return { ...base, device_type: 'uisp_power', api_username: '', api_password: '', api_port: 443 }
    case 'uisp_switch':
      return { ...base, device_type: 'uisp_switch', max_ports: 16, rocket_port_index: null, port_min_speed_mbps: 1000 }
  }
}

function deviceToForm(device: Device): DeviceFormData {
  const base = {
    name: device.name,
    ip_address: device.ip_address,
    location: device.location ?? '',
    snmp_community: device.snmp_community ?? '',
    notes: device.notes ?? '',
  }
  switch (device.device_type) {
    case 'rocket':
      return {
        ...base,
        device_type: 'rocket',
        radio_tech: device.radio_tech,
        ssh_username: device.ssh_username ?? '',
        ssh_password: '',
        ssh_port: device.ssh_port,
      }
    case 'lr':
      return {
        ...base,
        device_type: 'lr',
        model_variant: device.model_variant,
        rocket_id: device.rocket_id,
        ssh_username: device.ssh_username ?? '',
        ssh_password: '',
        ssh_port: device.ssh_port,
      }
    case 'uisp_power':
      return {
        ...base,
        device_type: 'uisp_power',
        api_username: device.api_username ?? '',
        api_password: '',
        api_port: device.api_port,
      }
    case 'uisp_switch':
      return {
        ...base,
        device_type: 'uisp_switch',
        max_ports: device.max_ports,
        rocket_port_index: device.rocket_port_index,
        port_min_speed_mbps: device.port_min_speed_mbps,
      }
  }
}

export default function DeviceFormModal({ open, device, onClose, onSaved }: Props) {
  const [form, setForm] = useState<DeviceFormData>(() => emptyForm('rocket'))
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEdit = device !== null

  useEffect(() => {
    if (!open) return
    setError(null)
    setConfirmDelete(false)
    setForm(device ? deviceToForm(device) : emptyForm('rocket'))
  }, [open, device])

  if (!open) return null

  // Setter — accepts any field name because each sub-component is responsible
  // for passing fields that match the form variant currently rendered. The
  // discriminator (device_type) prevents the wrong fields from being collected.
  const update = (field: string, value: unknown) => {
    setForm(f => ({ ...f, [field]: value }) as DeviceFormData)
  }

  // When the user switches the device_type in create mode, reset the form to
  // the right shape so type-specific fields are valid defaults.
  const switchType = (type: DeviceFormData['device_type']) => {
    setForm(prev => ({
      ...emptyForm(type),
      name: prev.name,
      ip_address: prev.ip_address,
      location: prev.location,
      snmp_community: prev.snmp_community,
      notes: prev.notes,
    }))
  }

  const handleSave = async () => {
    setError(null)
    if (!form.name.trim())       { setError("Le nom est requis"); return }
    if (!form.ip_address.trim()) { setError("L'adresse IP est requise"); return }
    setSaving(true)
    try {
      if (isEdit && device) {
        // Strip empty write-only secrets so the backend keeps the existing value.
        const payload: Record<string, unknown> = { ...form }
        if ('ssh_password' in payload && !payload.ssh_password) delete payload.ssh_password
        if ('api_password' in payload && !payload.api_password) delete payload.api_password
        await updateDevice(device.id, payload)
      } else {
        await createDevice(form)
      }
      onSaved()
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erreur inattendue")
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!device) return
    setDeleting(true)
    setError(null)
    try {
      await deleteDevice(device.id)
      onSaved()
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erreur inattendue")
    } finally {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100">
          <h2 className="text-lg font-bold text-blue-900">
            {isEdit ? `Modifier — ${device.name}` : 'Enregistrer un équipement'}
          </h2>
          <button onClick={onClose} className="text-blue-300 hover:text-blue-600">
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-4">

          <Field label="Nom *">
            <input
              type="text"
              value={form.name}
              onChange={e => update('name', e.target.value)}
              placeholder="ex: Rocket SUD"
              className={input}
            />
          </Field>

          <Field label="Adresse IP *">
            <input
              type="text"
              value={form.ip_address}
              onChange={e => update('ip_address', e.target.value)}
              placeholder="ex: 10.135.82.1"
              className={`${input} font-mono`}
            />
          </Field>

          <Field label="Type d'équipement *">
            {isEdit ? (
              <div className={`${input} bg-blue-50 text-slate-600`}>
                {TYPE_LABEL[form.device_type]}
              </div>
            ) : (
              <select
                value={form.device_type as Exclude<DeviceFormData['device_type'], 'lr'>}
                onChange={e => switchType(e.target.value as Exclude<DeviceFormData['device_type'], 'lr'>)}
                className={input}
              >
                {CREATABLE_DEVICE_TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            )}
            {isEdit && form.device_type === 'lr' && (
              <p className="text-xs text-blue-300 mt-1">
                Les LR sont créés et mis à jour automatiquement par la découverte
                via l&apos;API du Rocket parent — pas modifiables manuellement.
              </p>
            )}
            {isEdit && form.device_type !== 'lr' && (
              <p className="text-xs text-blue-300 mt-1">
                Le type est figé après création — supprimer puis recréer si nécessaire.
              </p>
            )}
          </Field>

          <Field label="Emplacement">
            <input
              type="text"
              value={form.location}
              onChange={e => update('location', e.target.value)}
              placeholder="ex: Site AT2"
              className={input}
            />
          </Field>

          <Field label="Community SNMP" hint="Laisser vide pour utiliser la valeur globale du .env">
            <input
              type="text"
              value={form.snmp_community}
              onChange={e => update('snmp_community', e.target.value)}
              placeholder="public"
              className={input}
            />
          </Field>

          {/* Type-specific blocks */}
          {form.device_type === 'rocket'      && <RocketFields form={form} update={update} isEdit={isEdit} />}
          {form.device_type === 'lr'          && <LrFields form={form} update={update} isEdit={isEdit} />}
          {form.device_type === 'uisp_power'  && <PowerFields form={form} update={update} isEdit={isEdit} hasPwd={device?.device_type === 'uisp_power' && device.has_api_password} />}
          {form.device_type === 'uisp_switch' && <SwitchFields form={form} update={update} />}

          <Field label="Notes">
            <textarea
              value={form.notes}
              onChange={e => update('notes', e.target.value)}
              rows={2}
              placeholder="Informations complémentaires…"
              className={`${input} resize-none`}
            />
          </Field>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-blue-100 flex items-center justify-between gap-3">
          {isEdit ? (
            confirmDelete ? (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-600 font-medium">Confirmer ?</span>
                <button onClick={handleDelete} disabled={deleting} className="text-xs bg-red-600 text-white px-3 py-1.5 rounded-lg hover:bg-red-700 disabled:opacity-50">
                  {deleting ? '…' : 'Supprimer'}
                </button>
                <button onClick={() => setConfirmDelete(false)} className="text-xs text-blue-500 hover:text-blue-700">Annuler</button>
              </div>
            ) : (
              <button onClick={() => setConfirmDelete(true)} className="text-sm text-red-500 hover:text-red-700 font-medium">Supprimer</button>
            )
          ) : <div />}

          <div className="flex items-center gap-3">
            <button onClick={onClose} className="text-sm text-blue-500 hover:text-blue-700 px-4 py-2 rounded-lg">Annuler</button>
            <button onClick={handleSave} disabled={saving} className="text-sm bg-blue-700 text-white px-5 py-2 rounded-lg hover:bg-blue-800 disabled:opacity-50 font-medium">
              {saving ? 'Enregistrement…' : isEdit ? 'Enregistrer' : 'Ajouter'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Type-specific field blocks
// ─────────────────────────────────────────────────────────────────────────────

function RocketFields({
  form, update, isEdit,
}: {
  form: RocketFormData
  update: (field: string, value: unknown) => void
  isEdit: boolean
}) {
  return (
    <div className="bg-blue-50 rounded-xl p-4 space-y-3">
      <p className="text-xs font-semibold text-blue-600 uppercase tracking-wide">Configuration Rocket</p>
      <Field label="Technologie radio">
        <select value={form.radio_tech} onChange={e => update('radio_tech', e.target.value as 'ltu' | 'airmax')} className={input}>
          {ROCKET_RADIO_TECHS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <Field label="Utilisateur API">
            <input type="text" value={form.ssh_username} onChange={e => update('ssh_username', e.target.value)} placeholder="ubnt" className={input} />
          </Field>
        </div>
        <Field label="Port HTTPS">
          <input type="number" value={form.ssh_port} onChange={e => update('ssh_port', Number(e.target.value))} min={1} max={65535} className={input} />
        </Field>
      </div>
      <Field label="Mot de passe API" hint={isEdit ? "Laisser vide pour conserver le mot de passe existant" : ""}>
        <input type="password" value={form.ssh_password} onChange={e => update('ssh_password', e.target.value)} placeholder={isEdit ? "••••••••" : "Mot de passe API"} autoComplete="new-password" className={input} />
      </Field>
    </div>
  )
}

function LrFields({
  form, update, isEdit,
}: {
  form: LrFormData
  update: (field: string, value: unknown) => void
  isEdit: boolean
}) {
  const variantLabel = LR_MODEL_VARIANTS.find(v => v.value === form.model_variant)?.label ?? form.model_variant
  return (
    <div className="bg-emerald-50 rounded-xl p-4 space-y-3">
      <p className="text-xs font-semibold text-emerald-700 uppercase tracking-wide">Configuration LR</p>
      <Field label="Modèle" hint="Détecté automatiquement par la découverte — non modifiable.">
        <div className={`${input} bg-white text-slate-600`}>{variantLabel}</div>
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <Field label="Utilisateur SSH">
            <input type="text" value={form.ssh_username} onChange={e => update('ssh_username', e.target.value)} placeholder="ubnt" className={input} />
          </Field>
        </div>
        <Field label="Port SSH">
          <input type="number" value={form.ssh_port} onChange={e => update('ssh_port', Number(e.target.value))} min={1} max={65535} className={input} />
        </Field>
      </div>
      <Field label="Mot de passe SSH" hint={isEdit ? "Laisser vide pour conserver le mot de passe existant" : ""}>
        <input type="password" value={form.ssh_password} onChange={e => update('ssh_password', e.target.value)} placeholder={isEdit ? "••••••••" : "Mot de passe SSH"} autoComplete="new-password" className={input} />
      </Field>
    </div>
  )
}

function PowerFields({
  form, update, isEdit, hasPwd,
}: {
  form: UispPowerFormData
  update: (field: string, value: unknown) => void
  isEdit: boolean
  hasPwd: boolean | undefined
}) {
  return (
    <div className="bg-amber-50 rounded-xl p-4 space-y-3">
      <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide">Identifiants UISP Power</p>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <Field label="Utilisateur API">
            <input type="text" value={form.api_username} onChange={e => update('api_username', e.target.value)} placeholder="ubnt" className={input} />
          </Field>
        </div>
        <Field label="Port HTTPS">
          <input type="number" value={form.api_port} onChange={e => update('api_port', Number(e.target.value))} min={1} max={65535} className={input} />
        </Field>
      </div>
      <Field label="Mot de passe API" hint={isEdit ? "Laisser vide pour conserver le mot de passe existant" : ""}>
        <input type="password" value={form.api_password} onChange={e => update('api_password', e.target.value)} placeholder={isEdit && hasPwd ? "••••••••" : "Mot de passe UISP Power"} autoComplete="new-password" className={input} />
      </Field>
    </div>
  )
}

function SwitchFields({
  form, update,
}: {
  form: UispSwitchFormData
  update: (field: string, value: unknown) => void
}) {
  return (
    <div className="bg-slate-50 rounded-xl p-4 space-y-3">
      <p className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Configuration UISP Switch</p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Nombre de ports à scanner">
          <input type="number" value={form.max_ports} onChange={e => update('max_ports', Number(e.target.value))} min={1} max={64} className={input} />
        </Field>
        <Field label="Index du port Rocket" hint="0 = pas de monitoring de port spécifique">
          <input type="number" value={form.rocket_port_index ?? 0} onChange={e => update('rocket_port_index', Number(e.target.value) || null)} min={0} max={64} className={input} />
        </Field>
      </div>
      <Field label="Vitesse minimale attendue (Mbps)">
        <input type="number" value={form.port_min_speed_mbps} onChange={e => update('port_min_speed_mbps', Number(e.target.value))} min={10} max={10000} className={input} />
      </Field>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-semibold text-blue-600 uppercase tracking-wide">{label}</label>
      {children}
      {hint && <p className="text-xs text-blue-300">{hint}</p>}
    </div>
  )
}

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}

const input = "w-full border border-blue-200 rounded-lg px-3 py-2 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent bg-white"
