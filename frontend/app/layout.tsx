import type { Metadata } from 'next'
import './globals.css'
import Sidebar from '@/components/Sidebar'

export const metadata: Metadata = {
  title: 'A2 Holding — Network Supervisor',
  description: 'Supervision réseau UISP/Ubiquiti — A2 Holding',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="bg-white min-h-screen flex">
        <Sidebar />
        <main className="flex-1 overflow-auto min-h-screen bg-slate-50">
          <div className="max-w-6xl mx-auto px-6 py-6">
            {children}
          </div>
        </main>
      </body>
    </html>
  )
}
