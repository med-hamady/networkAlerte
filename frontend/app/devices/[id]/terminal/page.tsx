'use client'

import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { requestShellTicket } from '@/lib/api'

// xterm imports are dynamic — the lib touches `window` at import time, so
// keeping it inside useEffect avoids SSR errors.

const STATUS_LABELS = {
  init:        'Initialisation…',
  ticketing:   'Demande du ticket…',
  connecting:  'Connexion au backend…',
  open:        'Session ouverte',
  closed:      'Session fermée',
  error:       'Erreur',
} as const

type Status = keyof typeof STATUS_LABELS

function deriveBackendWsBase(): string {
  // Prod: same-origin. The shell WebSocket goes through nginx (same scheme,
  // host and port as the page — 443), which proxies
  // /api/v1/devices/{id}/shell to the backend. No hardcoded port.
  //
  // Dev: the Next dev server (:3000) does NOT proxy /api/v1, and there is no
  // nginx, so the dev stack sets NEXT_PUBLIC_BACKEND_WS_URL=ws://localhost:8000
  // to reach the backend directly (port 8000 is exposed in docker-compose.yml).
  const fromEnv = process.env.NEXT_PUBLIC_BACKEND_WS_URL
  if (fromEnv) return fromEnv.replace(/\/+$/, '')
  if (typeof window === 'undefined') return ''
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}`
}

export default function DeviceTerminalPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const deviceId = Number(params.id)

  const containerRef = useRef<HTMLDivElement | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<Status>('init')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!Number.isFinite(deviceId) || !containerRef.current) return

    let disposed = false
    let term: import('@xterm/xterm').Terminal | null = null
    let fitAddon: import('@xterm/addon-fit').FitAddon | null = null
    let resizeObserver: ResizeObserver | null = null
    let onResizeWindow: (() => void) | null = null

    async function boot() {
      const xtermMod = await import('@xterm/xterm')
      const fitMod = await import('@xterm/addon-fit')
      // CSS is loaded once — duplicate import calls are deduped by webpack.
      // @ts-expect-error — the package ships a CSS file, no TS types for it.
      await import('@xterm/xterm/css/xterm.css')

      if (disposed || !containerRef.current) return

      term = new xtermMod.Terminal({
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        fontSize: 13,
        cursorBlink: true,
        theme: {
          background: '#0b1020',
          foreground: '#e4e8f7',
          cursor:     '#7dd3fc',
        },
        scrollback: 5000,
      })
      fitAddon = new fitMod.FitAddon()
      term.loadAddon(fitAddon)
      term.open(containerRef.current)
      try { fitAddon.fit() } catch { /* container may not be measured yet */ }

      term.writeln('\x1b[36m[supervisor]\x1b[0m Demande du ticket d\'accès…')
      setStatus('ticketing')

      let ticket: string
      try {
        const t = await requestShellTicket(deviceId)
        ticket = t.ticket
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : 'Erreur ticket'
        term.writeln(`\x1b[31m[supervisor]\x1b[0m ${msg}`)
        setStatus('error')
        setErrorMsg(msg)
        return
      }

      const wsUrl = `${deriveBackendWsBase()}/api/v1/devices/${deviceId}/shell?ticket=${encodeURIComponent(ticket)}`
      term.writeln(`\x1b[36m[supervisor]\x1b[0m Connexion à ${wsUrl}`)
      setStatus('connecting')

      const ws = new WebSocket(wsUrl)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      ws.onopen = () => {
        setStatus('open')
        // Send the current PTY size so the remote shell wraps lines correctly.
        if (term) {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
      }

      ws.onmessage = (ev) => {
        if (!term) return
        if (typeof ev.data === 'string') {
          term.write(ev.data)
        } else if (ev.data instanceof ArrayBuffer) {
          term.write(new Uint8Array(ev.data))
        }
      }

      ws.onclose = (ev) => {
        setStatus('closed')
        if (term) {
          term.writeln(`\r\n\x1b[33m[supervisor]\x1b[0m Session fermée (code ${ev.code}${ev.reason ? `, ${ev.reason}` : ''})`)
        }
      }

      ws.onerror = () => {
        setStatus('error')
        setErrorMsg('Erreur WebSocket — voir la console')
      }

      // User keystrokes → backend
      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(data)
      })

      // Resize bridge — push new cols/rows to backend so the PTY adapts.
      const sendResize = () => {
        if (!term || !fitAddon) return
        try { fitAddon.fit() } catch { return }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
        }
      }
      resizeObserver = new ResizeObserver(sendResize)
      resizeObserver.observe(containerRef.current)
      onResizeWindow = sendResize
      window.addEventListener('resize', onResizeWindow)
    }

    boot()

    return () => {
      disposed = true
      if (onResizeWindow) window.removeEventListener('resize', onResizeWindow)
      if (resizeObserver) resizeObserver.disconnect()
      if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) {
        wsRef.current.close()
      }
      if (term) term.dispose()
    }
  }, [deviceId])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/devices" className="text-sm text-blue-600 hover:text-blue-800">
            ← Retour aux équipements
          </Link>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight mt-1">
            Terminal modem #{deviceId}
          </h1>
          <p className="text-sm text-blue-400 mt-1">
            Statut : <span className={status === 'open' ? 'text-green-600 font-medium' : status === 'error' ? 'text-red-600 font-medium' : 'text-blue-500 font-medium'}>
              {STATUS_LABELS[status]}
            </span>
            {errorMsg && <span className="text-red-500"> — {errorMsg}</span>}
          </p>
        </div>
        <button
          onClick={() => router.refresh()}
          className="text-sm text-white bg-blue-700 hover:bg-blue-800 font-medium px-4 py-1.5 rounded-lg shadow-sm"
        >
          Reconnecter
        </button>
      </div>

      <div
        ref={containerRef}
        className="rounded-xl border border-slate-800 bg-[#0b1020] p-2 shadow-lg"
        style={{ height: 'calc(100vh - 220px)', minHeight: 360 }}
      />

      <p className="text-xs text-blue-300">
        Session SSH chaînée : supervisor → LR (jump) → modem. Le ticket est à usage unique
        et expire après 30 secondes.
      </p>
    </div>
  )
}
