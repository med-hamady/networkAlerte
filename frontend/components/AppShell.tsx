'use client'

import { usePathname } from 'next/navigation'
import Sidebar from '@/components/Sidebar'

/**
 * Decides whether to render the dashboard chrome (Sidebar + main column) or
 * the bare child (used for the /login full-screen form). Doing this in a
 * client wrapper avoids having to move every existing page into a Next.js
 * route group just to swap the layout for one page.
 */
export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  // Paths that render full-screen, without the dashboard chrome.
  const isChromeless = pathname === '/login'

  if (isChromeless) {
    return <>{children}</>
  }

  return (
    <div className="bg-white min-h-screen flex">
      <Sidebar />
      <main className="flex-1 overflow-auto min-h-screen bg-slate-50">
        <div className="max-w-6xl mx-auto px-6 py-6">
          {children}
        </div>
      </main>
    </div>
  )
}
