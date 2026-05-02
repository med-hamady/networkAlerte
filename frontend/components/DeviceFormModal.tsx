'use client'

import { useEffect, useState } from 'react'
import type { Device, DeviceFormData } from '@/lib/types'
import { createDevice, deleteDevice, updateDevice } from '@/lib/api'

interface Props {
  open: boolean
  device: Device | null   // null = create mode, non-null = edit mode
  onClose: () => void
  onSaved: () => void
}

const DEVICE_TYPES = [
  { value: 'ltu_rocket',    label: 'LTU Rocket (AP radio)' },
  { value: 'ltu_lr',        label: 'LTU LR (CPE radio)' },
  { value: 'airmax_rocket', label: 'Rocket airMAX (airOS)' },
  { value: 'uisp_switch',   label: 'UISP Switch' },
  { value: 'uisp_power',    label: 'UISP Power' },
]

const EMPTY: DeviceFormData = {
  name: '',
  ip_address: '',
  device_type: 'ltu_rocket',
  model: '',
  location: '',
  snmp_community: '',
  ssh_username: '',
  ssh_password: '',
  ssh_port: 22,
  notes: '',
}

export default function DeviceFormModal({ open, device, onClose, onSaved }: Props) {
  const [form, setForm] = useState<DeviceFormData>(EMPTY)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEdit = device !== null

  useEffect(() => {
    if (!open) return
    setError(null)
    setConfirmDelete(false)
    if (device) {
      setForm({
        name:            device.name,
        ip_address:      device.ip_address,
        device_type:     device.device_type,
        model:           device.model ?? '',
        location:        device.location ?? '',
        snmp_community:  device.snmp_community ?? '',
        ssh_username:    device.ssh_username ?? '',
        ssh_password:    '',   // never pre-filled — write-only
        ssh_port:        device.ssh_port ?? 22,
        notes:           device.notes ?? '',
      })
    } else {
      setForm(EMPTY)
    }
  }, [open, device])

  if (!open) return null

  const set = (field: keyof DeviceFormData) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
      const value = field === 'ssh_port' ? Number(e.target.value) : e.target.value
      setForm(f => ({ ...f, [field]: value }))
    }

  const handleSave = async () => {
    setError(null)
    if (!form.name.trim())       { setError("Le nom est requis"); return }
    if (!form.ip_address.trim()) { setError("L'adresse IP est requise"); return }
    setSaving(true)
    try {
      if (isEdit && device) {
        // For updates: only send ssh_password if the user typed a new one
        const payload = { ...form }
        if (!payload.ssh_password) delete (payload as Partial<DeviceFormData>).ssh_password
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

  const needsSsh = form.device_type === 'ltu_lr'
  const needsSnmp = form.device_type !== 'uisp_power'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      {/* Panel */}
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100">
          <h2 className="text-lg font-bold text-blue-900">
            {isEdit ? `Modifier — ${device.name}` : 'Enregistrer un équipement'}
          </h2>
          <button onClick={onClose} className="text-blue-300 hover:text-blue-600 transition-colors">
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-4">

          {/* Name */}
          <Field label="Nom *">
            <input
              type="text"
              value={form.name}
              onChange={set('name')}
              placeholder="ex: LTU Rocket Toit"
              className={input}
            />
          </Field>

          {/* IP */}
          <Field label="Adresse IP *">
            <input
              type="text"
              value={form.ip_address}
              onChange={set('ip_address')}
              placeholder="ex: 192.168.1.10"
              className={`${input} font-mono`}
            />
          </Field>

          {/* Type */}
          <Field label="Type d'équipement *">
            <select value={form.device_type} onChange={set('device_type')} className={input}>
              {DEVICE_TYPES.map(t => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </Field>

          {/* Model + Location */}
          <div className="grid grid-cols-2 gap-4">
            <Field label="Modèle">
              <input
                type="text"
                value={form.model}
                onChange={set('model')}
                placeholder="ex: LTU‑Rocket"
                className={input}
              />
            </Field>
            <Field label="Emplacement">
              <input
                type="text"
                value={form.location}
                onChange={set('location')}
                placeholder="ex: Toit immeuble A"
                className={input}
              />
            </Field>
          </div>

          {/* SNMP */}
          {needsSnmp && (
            <Field label="Community SNMP" hint="Laisser vide pour utiliser la valeur globale">
              <input
                type="text"
                value={form.snmp_community}
                onChange={set('snmp_community')}
                placeholder="public"
                className={input}
              />
            </Field>
          )}

          {/* SSH credentials — for all device types (enables check-ssh / check-ping diagnostics) */}
          <div className="bg-blue-50 rounded-xl p-4 space-y-3">
            <p className="text-xs font-semibold text-blue-600 uppercase tracking-wide">
              Credentials SSH
              {!needsSsh && <span className="ml-2 font-normal text-blue-400 normal-case">(diagnostics uniquement)</span>}
            </p>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <Field label="Utilisateur">
                  <input
                    type="text"
                    value={form.ssh_username}
                    onChange={set('ssh_username')}
                    placeholder="ubnt"
                    className={input}
                  />
                </Field>
              </div>
              <Field label="Port">
                <input
                  type="number"
                  value={form.ssh_port}
                  onChange={set('ssh_port')}
                  min={1}
                  max={65535}
                  className={input}
                />
              </Field>
            </div>
            <Field
              label="Mot de passe SSH"
              hint={isEdit ? "Laisser vide pour conserver le mot de passe existant" : ""}
            >
              <input
                type="password"
                value={form.ssh_password}
                onChange={set('ssh_password')}
                placeholder={isEdit ? "••••••••" : "Mot de passe SSH"}
                autoComplete="new-password"
                className={input}
              />
            </Field>
          </div>

          {/* Notes */}
          <Field label="Notes">
            <textarea
              value={form.notes}
              onChange={set('notes')}
              rows={2}
              placeholder="Informations complémentaires…"
              className={`${input} resize-none`}
            />
          </Field>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-blue-100 flex items-center justify-between gap-3">
          {/* Delete zone */}
          {isEdit && (
            <div>
              {confirmDelete ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-red-600 font-medium">Confirmer la suppression ?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="text-xs bg-red-600 text-white px-3 py-1.5 rounded-lg hover:bg-red-700 disabled:opacity-50"
                  >
                    {deleting ? '…' : 'Supprimer'}
                  </button>
                  <button
                    onClick={() => setConfirmDelete(false)}
                    className="text-xs text-blue-500 hover:text-blue-700"
                  >
                    Annuler
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="text-sm text-red-500 hover:text-red-700 font-medium transition-colors"
                >
                  Supprimer
                </button>
              )}
            </div>
          )}
          {!isEdit && <div />}

          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="text-sm text-blue-500 hover:text-blue-700 px-4 py-2 rounded-lg transition-colors"
            >
              Annuler
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-sm bg-blue-700 text-white px-5 py-2 rounded-lg hover:bg-blue-800 disabled:opacity-50 font-medium transition-colors"
            >
              {saving ? 'Enregistrement…' : isEdit ? 'Enregistrer' : 'Ajouter'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-semibold text-blue-600 uppercase tracking-wide">
        {label}
      </label>
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
