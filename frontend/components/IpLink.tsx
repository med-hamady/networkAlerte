'use client'

import type { MouseEvent } from 'react'

interface Props {
  ip: string | null
  /** Extra classes merged with the default link styling. */
  className?: string
}

/**
 * Affiche une adresse IP comme lien cliquable ouvrant l'UI de l'équipement
 * (http://<ip>) dans un nouvel onglet. `stopPropagation` évite de déclencher
 * un onClick parent (carte/ligne cliquable) quand l'IP est imbriquée dedans.
 */
export default function IpLink({ ip, className = '' }: Props) {
  if (!ip) return null
  const stop = (e: MouseEvent) => e.stopPropagation()
  return (
    <a
      href={`http://${ip}`}
      target="_blank"
      rel="noopener noreferrer"
      onClick={stop}
      title={`Ouvrir http://${ip} dans un nouvel onglet`}
      className={`hover:underline hover:text-blue-600 transition-colors ${className}`}
    >
      {ip}
    </a>
  )
}
