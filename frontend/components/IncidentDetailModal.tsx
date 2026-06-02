'use client'

import type { Incident } from '@/lib/types'
import {
  LR_MODEL_VARIANT_LABELS,
  alertTypeLabel,
  formatDate,
  lrFamilyLabel,
  metricLabel,
} from '@/lib/types'
import SeverityBadge from './SeverityBadge'
import IncidentStatusBadge from './IncidentStatusBadge'

const CHANNEL_STYLES: Record<string, string> = {
  email: 'bg-amber-50 text-amber-700 border-amber-200',
}

export default function IncidentDetailModal({
  incident,
  onClose,
}: {
  incident: Incident
  onClose: () => void
}) {
  return (
    <div
      className="fixed inset-0 z-50 bg-blue-900/40 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 border-b border-blue-100">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <SeverityBadge severity={incident.severity} />
              <IncidentStatusBadge status={incident.status} />
              {incident.notify_immediately && (
                <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-600 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                  Notification immédiate
                </span>
              )}
            </div>
            <h2 className="text-lg font-bold text-blue-900">
              {alertTypeLabel(incident.alert_type)}
              <span className="text-blue-400 font-normal ml-2 text-sm">#{incident.id}</span>
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-blue-400 hover:text-blue-600 transition-colors"
            aria-label="Fermer"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-5 text-sm">

          {/* Device */}
          <Section title="Équipement">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Field label="Nom"        value={incident.device_name ?? `#${incident.device_id}`} />
              <Field label="Type"       value={incident.device_type ?? '—'} />
              {incident.lr_model_variant && (
                <Field
                  label="Modèle LR"
                  value={`${LR_MODEL_VARIANT_LABELS[incident.lr_model_variant] ?? incident.lr_model_variant} (${lrFamilyLabel(incident.lr_model_variant)})`}
                />
              )}
              <Field label="IP"         value={incident.device_ip ?? '—'} mono />
              {incident.device_mac && (
                <Field label="Adresse MAC" value={incident.device_mac} mono />
              )}
              <Field label="Device ID"  value={`#${incident.device_id}`} />
            </div>
          </Section>

          {/* Metric + cause */}
          {incident.metric_name && (
            <Section title="Diagnostic">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Field
                  label="Métrique"
                  value={
                    incident.metric_value !== null
                      ? `${metricLabel(incident.metric_name)} = ${incident.metric_value}`
                      : metricLabel(incident.metric_name)
                  }
                />
                {incident.threshold_value !== null && (
                  <Field
                    label="Seuil"
                    value={String(incident.threshold_value)}
                    mono
                  />
                )}
              </div>
            </Section>
          )}

          {/* Channels */}
          <Section title="Canaux de notification">
            <div className="flex gap-1 flex-wrap">
              {incident.notification_channel_policy.length === 0 ? (
                <span className="text-blue-300 text-xs">Aucun canal configuré pour ce type d'alerte.</span>
              ) : (
                incident.notification_channel_policy.map(c => (
                  <span
                    key={c}
                    className={`inline-flex px-2 py-0.5 rounded text-[11px] font-mono border ${
                      CHANNEL_STYLES[c] ?? 'bg-slate-50 text-slate-600 border-slate-200'
                    }`}
                  >
                    {c}
                  </span>
                ))
              )}
            </div>
          </Section>

          {/* Timeline */}
          <Section title="Chronologie">
            <div className="grid grid-cols-1 gap-1 text-xs">
              <Field label="Détecté le"        value={formatDate(incident.detected_at)} />
              <Field label="Dernière alerte"   value={formatDate(incident.last_triggered_at)} />
              <Field label="Résolu le"         value={formatDate(incident.resolved_at)} />
            </div>
          </Section>

          {/* Pre-formatted operator message */}
          {incident.message && (
            <Section title="Message opérateur">
              <pre className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs text-slate-700 whitespace-pre-wrap font-mono leading-relaxed">
                {incident.message}
              </pre>
            </Section>
          )}

          {/* Description */}
          {incident.description && (
            <Section title="Description technique">
              <p className="text-xs text-slate-600 whitespace-pre-wrap">{incident.description}</p>
            </Section>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-blue-100 bg-blue-50/40 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-white border border-blue-200 text-blue-600 rounded-lg text-sm font-medium hover:bg-blue-50 transition-colors"
          >
            Fermer
          </button>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-blue-500 uppercase tracking-wider mb-2">{title}</h3>
      {children}
    </div>
  )
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <span className="text-blue-400">{label} : </span>
      <span className={`text-slate-700 ${mono ? 'font-mono' : ''}`}>{value}</span>
    </div>
  )
}
