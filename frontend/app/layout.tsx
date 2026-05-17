import type { Metadata } from 'next'
import './globals.css'
import AppShell from '@/components/AppShell'

export const metadata: Metadata = {
  title: 'A2 Holding — Network Supervisor',
  description: 'Supervision réseau UISP/Ubiquiti — A2 Holding',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="bg-white min-h-screen">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  )
}
