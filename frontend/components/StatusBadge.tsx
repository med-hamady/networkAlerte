// "unknown" est rendu en ROUGE, comme "down" : un device sans statut mesurable
// (typiquement un LR qui a perdu son IP au churn DHCP) n'est PAS joignable —
// l'afficher en bleu neutre le faisait passer pour un détail cosmétique alors
// que c'est une absence de service côté abonné. Libellé distinct ("INCONNU")
// pour ne pas prétendre qu'on l'a vu tomber au ping.
const styles: Record<string, string> = {
  up:      'bg-green-50  text-green-700 border border-green-200',
  down:    'bg-red-50    text-red-600   border border-red-200',
  unknown: 'bg-red-50    text-red-500   border border-red-200',
}

const labels: Record<string, string> = {
  up:      'UP',
  down:    'DOWN',
  unknown: 'INCONNU',
}

export default function StatusBadge({ status }: { status: string }) {
  const key = status.toLowerCase()
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold ${styles[key] ?? styles.unknown}`}>
      <span className="relative flex items-center justify-center w-1.5 h-1.5">
        {key === 'up' && (
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
        )}
        <span className={`relative inline-flex w-1.5 h-1.5 rounded-full ${
          key === 'up' ? 'bg-green-500' : key === 'down' ? 'bg-red-500' : 'bg-red-400'
        }`} />
      </span>
      {labels[key] ?? status.toUpperCase()}
    </span>
  )
}
