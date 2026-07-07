'use client'

// PAGE TEMPORAIRE — aperçu visuel de SiteTopology avec des données factices.
// À supprimer après validation (ainsi que l'exception dans middleware.ts / AppShell).

import { useState } from 'react'
import type { Device } from '@/lib/types'
import SiteTopology from '@/components/SiteTopology'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import DeviceCard from '@/components/DeviceCard'

let _id = 0
function dev(partial: Partial<Device> & Pick<Device, 'device_type' | 'name'>): Device {
  _id += 1
  return {
    id: _id,
    ip_address: `10.135.2.${100 + _id}`,
    status: 'up',
    location: 'A2 ARF1',
    site: 'A2 ARF1',
    snmp_community: null,
    notes: null,
    last_seen: new Date(Date.now() - 28_000).toISOString(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    mac_address: null,
    hostname: null,
    firmware_version: null,
    auto_discovered: true,
    first_discovered_at: null,
    last_discovered_at: null,
    policy_overrides: null,
    ...partial,
  } as Device
}

const withSwitch: Device[] = [
  dev({ device_type: 'uisp_switch', name: 'ARF1-UISP-S-Pro 409' }),
  dev({ device_type: 'rocket', name: 'A2-ARF1-EST',   radio_tech: 'ltu' } as any),
  dev({ device_type: 'rocket', name: 'A2-ARF1-NORD',  radio_tech: 'ltu' } as any),
  dev({ device_type: 'rocket', name: 'A2-ARF1-OUEST', radio_tech: 'airmax', status: 'down' } as any),
  dev({ device_type: 'uisp_power', name: 'ARF1-UISP-P F6A' } as any),
  dev({ device_type: 'airfiber', name: 'F60 ARF1-PK1' } as any),
  dev({ device_type: 'ptp_litebeam', name: 'PTP ARF1↔TS1' } as any),
]

const manyChildren: Device[] = [
  dev({ device_type: 'uisp_switch', name: 'HQ-UISP-S-Pro' }),
  ...Array.from({ length: 10 }, (_, i) =>
    dev({ device_type: 'rocket', name: `A2-HQ-R${i + 1}`, radio_tech: i % 2 ? 'airmax' : 'ltu' } as any),
  ),
]

const noSwitch: Device[] = [
  dev({ device_type: 'rocket', name: 'A2-X-EST', radio_tech: 'ltu' } as any),
  dev({ device_type: 'uisp_power', name: 'X-UISP-P' } as any),
]

// Cartes pour vérifier toutes les photos (dont Rocket airMAX + LTU Lite).
const cards: Device[] = [
  dev({ device_type: 'rocket', name: 'Rocket LTU',    radio_tech: 'ltu' } as any),
  dev({ device_type: 'rocket', name: 'Rocket airMAX', radio_tech: 'airmax' } as any),
  dev({ device_type: 'lr', name: 'LR LTU',   model_variant: 'ltu_lr' } as any),
  dev({ device_type: 'lr', name: 'LR LTU Lite', model_variant: 'ltu_lite' } as any),
  dev({ device_type: 'lr', name: 'LR LiteBeam', model_variant: 'litebeam_5ac' } as any),
  dev({ device_type: 'uisp_switch', name: 'UISP Switch' }),
  dev({ device_type: 'uisp_power', name: 'UISP Power' } as any),
  dev({ device_type: 'airfiber', name: 'airFiber 60' } as any),
]

export default function TopoPreview() {
  const [selected, setSelected] = useState<Device | null>(null)
  return (
    <div className="min-h-screen bg-blue-50/40 p-8 space-y-12">
      <Section title="1 · Site avec switch + 6 équipements (dont 1 down)">
        <SiteTopology devices={withSwitch} onSelect={setSelected} />
      </Section>
      <Section title="2 · Beaucoup d'équipements (colonne haute)">
        <SiteTopology devices={manyChildren} onSelect={setSelected} />
      </Section>
      <Section title="3 · Sans switch (repli grille)">
        <SiteTopology devices={noSwitch} onSelect={setSelected} />
      </Section>
      <Section title="4 · Cartes équipements — toutes les photos">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          {cards.map(d => <DeviceCard key={d.id} device={d} onClick={setSelected} />)}
        </div>
      </Section>
      <DeviceDetailModal device={selected} devices={[]} onClose={() => setSelected(null)} onNavigate={setSelected} />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-bold text-blue-900">{title}</h2>
      <div className="bg-white border border-blue-100 rounded-xl p-6 shadow-sm">{children}</div>
    </div>
  )
}
