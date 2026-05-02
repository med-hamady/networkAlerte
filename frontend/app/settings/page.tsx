'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { getThresholds, patchThresholds, resetThreshold } from '@/lib/api'
import type { Threshold } from '@/lib/types'

const CATEGORY_ORDER = [
  'radio_signal',
  'radio_cinr',
  'radio_ccq',
  'capacity',
  'errors',
  'battery',
  'antiflap',
  'throughput',
]

export default function SettingsPage() {
  const [thresholds, setThresholds] = useState<Threshold[]>([])
  const [pending, setPending]       = useState<Record<string, number>>({})
  const [saving, setSaving]         = useState(false)
  const [resetKey, setResetKey]     = useState<string | null>(null)
  const [savedAt, setSavedAt]       = useState<string | null>(null)
  const [error, setError]           = useState<string | null>(null)
  const [loading, setLoading]       = useState(true)
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await getThresholds()
      setThresholds(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur de chargement')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const hasPending = Object.keys(pending).length > 0

  const handleChange = (key: string, value: number) => {
    setPending(p => ({ ...p, [key]: value }))
    setSavedAt(null)
  }

  const handleSave = async () => {
    if (!hasPending) return
    setSaving(true)
    setError(null)
    try {
      const updated = await patchThresholds(pending)
      setThresholds(updated)
      setPending({})
      setSavedAt(new Date().toLocaleTimeString('fr-FR'))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur de sauvegarde')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async (key: string) => {
    setResetKey(key)
    try {
      await resetThreshold(key)
      const updated = await getThresholds()
      setThresholds(updated)
      setPending(p => { const n = { ...p }; delete n[key]; return n })
    } catch {
      // If 404 (no override), just clear pending value
      setPending(p => { const n = { ...p }; delete n[key]; return n })
      const updated = await getThresholds()
      setThresholds(updated)
    } finally {
      setResetKey(null)
    }
  }

  const handleDiscard = () => setPending({})

  // Group thresholds by category in defined order
  const grouped = CATEGORY_ORDER.reduce<Record<string, Threshold[]>>((acc, cat) => {
    const items = thresholds.filter(t => t.category === cat)
    if (items.length) acc[cat] = items
    return acc
  }, {})

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-blue-300">
        Chargement des seuils…
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-3xl">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Seuils d'alerte</h1>
          <p className="text-blue-400 text-sm mt-1">
            Les valeurs modifiées ici prennent effet au prochain cycle de polling sans redémarrage.
          </p>
        </div>

        {/* Save bar */}
        <div className="flex items-center gap-3">
          {savedAt && !hasPending && (
            <span className="text-xs text-green-600 font-medium">✓ Sauvegardé à {savedAt}</span>
          )}
          {hasPending && (
            <button
              onClick={handleDiscard}
              className="text-sm text-blue-500 hover:text-blue-700 px-3 py-1.5 rounded-lg border border-blue-200 bg-white transition-colors"
            >
              Annuler
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={!hasPending || saving}
            className="text-sm text-white bg-blue-700 hover:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed font-medium px-5 py-1.5 rounded-lg transition-colors"
          >
            {saving ? 'Sauvegarde…' : `Sauvegarder${hasPending ? ` (${Object.keys(pending).length})` : ''}`}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Info banner */}
      <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 text-sm text-blue-700 flex items-start gap-2">
        <InfoIcon className="w-4 h-4 mt-0.5 shrink-0 text-blue-500" />
        <span>
          Les valeurs par défaut proviennent du fichier <code className="bg-blue-100 px-1 rounded text-xs">.env</code> du serveur.
          Modifier une valeur ici crée une surcharge en base de données qui prend le dessus sans redémarrage.
          Cliquer sur <strong>Défaut</strong> supprime la surcharge.
        </span>
      </div>

      {/* Groups */}
      {Object.entries(grouped).map(([_cat, items]) => (
        <section key={_cat} className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
          <div className="px-5 py-3 bg-blue-50 border-b border-blue-100">
            <h2 className="text-sm font-semibold text-blue-700">{items[0].category_label}</h2>
          </div>
          <div className="divide-y divide-blue-50">
            {items.map(t => {
              const currentVal = pending[t.key] !== undefined ? pending[t.key] : t.value
              const isModified = pending[t.key] !== undefined
              const isOverridden = t.is_overridden && !isModified

              return (
                <div key={t.key} className="px-5 py-4 flex items-center gap-4">
                  {/* Label */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-700">{t.label}</span>
                      {isOverridden && (
                        <span className="text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-medium">
                          personnalisé
                        </span>
                      )}
                      {isModified && (
                        <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-medium">
                          modifié
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-blue-400 mt-0.5">
                      Défaut env : {t.default} {t.unit}
                    </p>
                  </div>

                  {/* Input */}
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      value={currentVal}
                      min={t.min}
                      max={t.max}
                      step={t.step}
                      onChange={e => handleChange(t.key, t.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value))}
                      className="w-24 border border-blue-200 rounded-lg px-3 py-1.5 text-sm text-center font-mono focus:outline-none focus:ring-2 focus:ring-blue-400"
                    />
                    <span className="text-xs text-blue-400 w-8">{t.unit}</span>
                  </div>

                  {/* Reset button */}
                  <button
                    onClick={() => handleReset(t.key)}
                    disabled={resetKey === t.key || (!t.is_overridden && !isModified)}
                    title="Remettre à la valeur par défaut"
                    className="text-xs text-blue-400 hover:text-blue-700 disabled:opacity-30 disabled:cursor-not-allowed px-2 py-1 rounded transition-colors whitespace-nowrap"
                  >
                    {resetKey === t.key ? '…' : 'Défaut'}
                  </button>
                </div>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

function InfoIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}
