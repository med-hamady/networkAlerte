const styles: Record<string, string> = {
  open:         'bg-red-50    text-red-600    border border-red-200',
  acknowledged: 'bg-orange-50 text-orange-600 border border-orange-200',
  resolved:     'bg-green-50  text-green-700  border border-green-200',
}

const labels: Record<string, string> = {
  open:         'OUVERT',
  acknowledged: 'ACQUITTÉ',
  resolved:     'RÉSOLU',
}

export default function IncidentStatusBadge({ status }: { status: string }) {
  const key = status.toLowerCase()
  return (
    <span className={`inline-flex px-2.5 py-0.5 rounded-lg text-xs font-semibold ${styles[key] ?? 'bg-blue-50 text-blue-400 border border-blue-200'}`}>
      {labels[key] ?? status.toUpperCase()}
    </span>
  )
}
