import type { Metadata } from 'next'
import './globals.css'
import AppShell from '@/components/AppShell'

export const metadata: Metadata = {
  title: 'A2 ICT — Network Management',
  description: 'Gestion et supervision du réseau UISP/Ubiquiti — A2 ICT',
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
