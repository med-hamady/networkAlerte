const styles: Record<string, string> = {
  critical: 'bg-red-50    text-red-600   border border-red-200',
  warning:  'bg-orange-50 text-orange-600 border border-orange-200',
  info:     'bg-blue-50   text-blue-600  border border-blue-200',
  dynamic:  'bg-purple-50 text-purple-600 border border-purple-200',
}

const labels: Record<string, string> = {
  critical: 'CRITIQUE',
  warning:  'AVERTISSEMENT',
  info:     'INFO',
  dynamic:  'DYNAMIQUE',
}

export default function SeverityBadge({ severity }: { severity: string }) {
  const key = severity.toLowerCase()
  return (
    <span className={`inline-flex px-2.5 py-0.5 rounded-lg text-xs font-semibold ${styles[key] ?? 'bg-blue-50 text-blue-400 border border-blue-200'}`}>
      {labels[key] ?? severity.toUpperCase()}
    </span>
  )
}
