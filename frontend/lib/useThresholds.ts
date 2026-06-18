import useSWR from 'swr'
import { endpoints, fetcher } from './api'
import type { Threshold } from './types'

export interface ThresholdMap {
  signal_warning_dbm: number
  signal_critical_dbm: number
  cinr_warning_db: number
  cinr_critical_db: number
  ccq_warning_pct: number
  ccq_critical_pct: number
  lr_latency_critical_ms: number
  battery_warning_pct: number
  battery_critical_pct: number
  // Pass-through for any other threshold key
  [key: string]: number
}

const DEFAULTS: ThresholdMap = {
  signal_warning_dbm: -75,
  signal_critical_dbm: -80,
  cinr_warning_db: 20,
  cinr_critical_db: 10,
  ccq_warning_pct: 75,
  ccq_critical_pct: 50,
  lr_latency_critical_ms: 100,
  battery_warning_pct: 25,
  battery_critical_pct: 10,
  airmax_backhaul_capacity_min_mbps: 150,
}

/**
 * Returns the effective threshold values keyed by setting name.
 * Falls back to safe defaults while loading or on error so UI never breaks.
 * Cached/shared via SWR — calling from multiple components triggers a single fetch.
 */
export function useThresholds(): ThresholdMap {
  const { data } = useSWR<Threshold[]>(endpoints.thresholds, fetcher, {
    revalidateOnFocus: false,
    dedupingInterval: 30_000,
  })

  if (!data) return DEFAULTS

  const map: ThresholdMap = { ...DEFAULTS }
  for (const t of data) {
    map[t.key] = t.value
  }
  return map
}
