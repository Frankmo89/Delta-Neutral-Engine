import { useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, Pause, Play, ShieldCheck, TrendingUp, Wifi, X } from 'lucide-react'

const POSITIONS_API_URL = 'http://localhost:8000/api/positions'
const PORTFOLIO_API_URL = 'http://localhost:8000/api/portfolio'
const SCANNER_WS_URL = 'ws://localhost:8000/ws/scanner'

function Tooltip({ text, children, position = 'top' }) {
  const tooltipPositionClass =
    position === 'bottom'
      ? 'top-full left-1/2 mt-2 -translate-x-1/2'
      : 'bottom-full left-1/2 mb-2 -translate-x-1/2'

  return (
    <span className="group relative z-[9999] inline-flex cursor-help items-center gap-1 overflow-visible align-middle">
      {children}
      <span
        className={`pointer-events-none absolute z-[9999] w-72 rounded-md border border-zinc-700 bg-gray-800 px-3 py-2 text-xs normal-case leading-relaxed text-zinc-200 opacity-0 shadow-xl transition-opacity duration-150 group-hover:opacity-100 ${tooltipPositionClass}`}
      >
        {text}
      </span>
    </span>
  )
}

function toPct(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) {
    return '0.0000%'
  }
  return `${num.toFixed(4)}%`
}

function toAprFromFundingPct(fundingRatePct) {
  const num = Number(fundingRatePct)
  if (!Number.isFinite(num)) {
    return 0
  }
  return num * 3 * 365
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
}

function getFundingSemantic(fundingRatePct) {
  const value = Number(fundingRatePct)

  if (!Number.isFinite(value)) {
    return {
      label: 'Neutral',
      textClass: 'text-zinc-300',
      chipClass: 'border-zinc-600/40 bg-zinc-700/20 text-zinc-300',
      barClass: 'bg-zinc-500/70',
    }
  }

  if (value >= 0.2) {
    return {
      label: 'Alta',
      textClass: 'text-emerald-300',
      chipClass: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
      barClass: 'bg-emerald-400',
    }
  }

  if (value >= 0.05) {
    return {
      label: 'Buena',
      textClass: 'text-lime-300',
      chipClass: 'border-lime-500/40 bg-lime-500/15 text-lime-300',
      barClass: 'bg-lime-400',
    }
  }

  if (value <= -0.05) {
    return {
      label: 'Riesgo',
      textClass: 'text-rose-300',
      chipClass: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
      barClass: 'bg-rose-400',
    }
  }

  return {
    label: 'Neutral',
    textClass: 'text-zinc-300',
    chipClass: 'border-zinc-600/40 bg-zinc-700/20 text-zinc-300',
    barClass: 'bg-zinc-500/70',
  }
}

function fundingMagnitudePercent(fundingRatePct) {
  const value = Math.abs(Number(fundingRatePct))
  if (!Number.isFinite(value)) {
    return 0
  }
  return clamp((value / 1) * 100, 0, 100)
}

function parseFundingTimestampMs(rawTimestamp) {
  if (rawTimestamp === null || rawTimestamp === undefined || rawTimestamp === '') {
    return null
  }

  if (typeof rawTimestamp === 'number') {
    return Number.isFinite(rawTimestamp) ? rawTimestamp : null
  }

  const numeric = Number(rawTimestamp)
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric
  }

  const parsed = Date.parse(String(rawTimestamp))
  if (Number.isNaN(parsed)) {
    return null
  }
  return parsed
}

function formatCountdown(nextFundingTime, nowMs) {
  const targetMs = parseFundingTimestampMs(nextFundingTime)
  if (!targetMs) {
    return '--:--:--'
  }

  const deltaMs = targetMs - nowMs
  if (deltaMs <= 0) {
    return '00:00:00'
  }

  const totalSeconds = Math.floor(deltaMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function formatBreakeven(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) {
    return 'N/A'
  }
  return `${num.toFixed(2)} p`
}

function formatPnl(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) {
    return '0.00 USDT'
  }
  return `${num.toFixed(2)} USDT`
}

function formatUsdt(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) {
    return '0.00'
  }
  return num.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function getPnlSemantic(unrealizedPnl) {
  const pnl = Number(unrealizedPnl)
  if (!Number.isFinite(pnl)) {
    return 'text-zinc-300'
  }
  if (pnl > 0) {
    return 'text-emerald-300'
  }
  if (pnl < 0) {
    return 'text-rose-300'
  }
  return 'text-zinc-300'
}

function App() {
  const [scanResults, setScanResults] = useState([])
  const [activePositions, setActivePositions] = useState([])
  const [portfolio, setPortfolio] = useState({ total_balance: 0, lifetime_pnl: 0 })
  const [wsConnected, setWsConnected] = useState(false)
  const [isScannerPaused, setIsScannerPaused] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const [pendingCloseSymbol, setPendingCloseSymbol] = useState('')
  const [pendingForceCleanup, setPendingForceCleanup] = useState('')
  const [closeError, setCloseError] = useState('')

  const loadPositions = async (isMounted = true) => {
    try {
      const response = await fetch(POSITIONS_API_URL)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const payload = await response.json()
      if (isMounted) {
        setActivePositions(Array.isArray(payload) ? payload : [])
      }
    } catch (error) {
      if (isMounted) {
        setActivePositions([])
      }
      console.error('Error loading positions:', error)
    }
  }

  const handleForceCleanup = async (symbol) => {
    setPendingForceCleanup(symbol)
    setCloseError('')
    try {
      const response = await fetch(`${POSITIONS_API_URL}/${encodeURIComponent(symbol)}/force`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      await loadPositions(true)
    } catch (error) {
      console.error(`Error forcing cleanup for ${symbol}:`, error)
    } finally {
      setPendingForceCleanup('')
    }
  }

  const handleClosePosition = async (symbol) => {
    setPendingCloseSymbol(symbol)
    setCloseError('')

    try {
      const response = await fetch(`${POSITIONS_API_URL}/${encodeURIComponent(symbol)}/close`, {
        method: 'POST',
      })

      if (!response.ok) {
        let detail = `HTTP ${response.status}`
        try {
          const payload = await response.json()
          if (payload?.detail) {
            detail = payload.detail
          }
        } catch {
          // Ignorar errores de parsing y usar código HTTP.
        }
        throw new Error(detail)
      }

      await Promise.all([loadPositions(true), loadPortfolio(true)])
    } catch (error) {
      console.error(`Error closing position ${symbol}:`, error)
      setCloseError(`No se pudo cerrar ${symbol}: ${error instanceof Error ? error.message : 'Error desconocido'}`)
    } finally {
      setPendingCloseSymbol('')
    }
  }

  const loadPortfolio = async (isMounted = true) => {
    try {
      const response = await fetch(PORTFOLIO_API_URL)
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const payload = await response.json()
      if (isMounted) {
        setPortfolio({
          total_balance: Number(payload.total_balance || 0),
          lifetime_pnl: Number(payload.lifetime_pnl || 0),
        })
      }
    } catch (error) {
      if (isMounted) {
        setPortfolio({ total_balance: 0, lifetime_pnl: 0 })
      }
      console.error('Error loading portfolio:', error)
    }
  }

  useEffect(() => {
    let isMounted = true
    let socket

    loadPositions(isMounted)
    loadPortfolio(isMounted)

    const portfolioIntervalId = window.setInterval(() => {
      loadPortfolio(isMounted)
    }, 15000)

    try {
      socket = new WebSocket(SCANNER_WS_URL)
    } catch (error) {
      console.error('Error creating WebSocket:', error)
      return () => {
        isMounted = false
      }
    }

    socket.onopen = () => {
      if (isMounted) {
        setWsConnected(true)
      }
    }

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data)
        if (message.type === 'scanner_snapshot') {
          setScanResults(Array.isArray(message.results) ? message.results : [])
        }
      } catch (error) {
        console.error('Error parsing WS message:', error)
      }
    }

    socket.onclose = () => {
      if (isMounted) {
        setWsConnected(false)
      }
    }

    socket.onerror = (error) => {
      console.error('WebSocket error:', error)
    }

    return () => {
      isMounted = false
      window.clearInterval(portfolioIntervalId)
      setWsConnected(false)
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.close()
      }
    }
  }, [])

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)

    return () => {
      window.clearInterval(intervalId)
    }
  }, [])

  const sortedScanResults = useMemo(
    () => [...scanResults].sort((a, b) => Number(b.apr_est) - Number(a.apr_est)),
    [scanResults],
  )
  const lifetimePnlClass =
    Number(portfolio.lifetime_pnl) > 0
      ? 'text-emerald-300'
      : Number(portfolio.lifetime_pnl) < 0
      ? 'text-rose-300'
      : 'text-zinc-200'

  return (
    <div className="min-h-screen bg-[#09090b] text-zinc-100">
      <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-[#09090b]/95 backdrop-blur">
        <div className="mx-auto w-full max-w-7xl px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl border border-zinc-700 bg-zinc-900 p-2">
                <Activity className="h-5 w-5 text-cyan-300" />
              </div>
              <div>
                <h1 className="text-lg font-semibold tracking-wide text-zinc-100 sm:text-xl">
                  Delta-Neutral Engine
                </h1>
                <p className="text-xs uppercase tracking-[0.22em] text-zinc-400">Funding Arbitrage</p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300">
                <ShieldCheck className="h-4 w-4" />
                Testnet Active
              </span>

              <div className="inline-flex items-center gap-2 rounded-full border border-zinc-700 bg-zinc-900 px-3 py-1 text-xs text-zinc-300">
                <span className="relative inline-flex h-2.5 w-2.5 items-center justify-center">
                  <span
                    className={`absolute inline-flex h-2.5 w-2.5 rounded-full ${
                      wsConnected ? 'bg-emerald-400' : 'bg-zinc-500'
                    }`}
                  />
                  {wsConnected && <span className="absolute inline-flex h-5 w-5 rounded-full border border-emerald-400/50 animate-pulse-ring" />}
                </span>
                <Wifi className={`h-4 w-4 ${wsConnected ? 'text-emerald-300' : 'text-zinc-500'}`} />
                {wsConnected ? 'WS Connected' : 'WS Offline'}
              </div>

              <button
                type="button"
                onClick={() => setIsScannerPaused((prev) => !prev)}
                className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-200 transition hover:border-amber-400/40 hover:bg-amber-500/20"
              >
                {isScannerPaused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                {isScannerPaused ? 'Reanudar Escáner' : 'Pausar Escáner'}
              </button>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/90 px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                TOTAL EQUITY (USDT)
              </p>
              <p className="mt-2 font-mono text-2xl font-semibold text-cyan-200 sm:text-3xl">
                {formatUsdt(portfolio.total_balance)}
              </p>
            </div>
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/90 px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                LIFETIME PNL (USDT)
              </p>
              <p className={`mt-2 font-mono text-2xl font-semibold sm:text-3xl ${lifetimePnlClass}`}>
                {Number(portfolio.lifetime_pnl) >= 0 ? '+' : ''}
                {formatUsdt(portfolio.lifetime_pnl)}
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-7xl flex-col gap-8 px-4 py-8 sm:px-6 lg:px-8">
        <section className="space-y-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-amber-300" />
            <Tooltip text="Operaciones abiertas actualmente. El bot compró y vendió este activo al mismo tiempo para anular el riesgo de mercado.">
              <h2 className="text-base font-semibold uppercase tracking-[0.16em] text-zinc-100 sm:text-lg">
                ACTIVE POSITIONS
              </h2>
            </Tooltip>
          </div>

          {closeError && (
            <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
              {closeError}
            </div>
          )}

          {activePositions.length === 0 ? (
            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/70 p-6 text-sm text-zinc-400">
              No hay posiciones Delta-Neutral activas.
            </div>
          ) : (
            <div className="overflow-visible rounded-2xl border border-zinc-800 bg-zinc-900/80">
              <div className="overflow-x-auto overflow-y-visible">
                <table className="min-w-full divide-y divide-zinc-800 text-sm">
                  <thead className="bg-zinc-900/90">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Pair</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Qty</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Funding</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Break-even</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">PnL</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Estado</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Acción</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {activePositions.map((position) => {
                      const semantic = getFundingSemantic(position.funding_rate_pct)
                      const magnitude = fundingMagnitudePercent(position.funding_rate_pct)
                      const pnlClass = getPnlSemantic(position.unrealized_pnl)
                      const requiresIntervention = Boolean(position.requires_manual_intervention)
                      const interventionReason = position.intervention_reason || 'Intervención manual requerida'

                      return (
                        <tr
                          key={position.symbol}
                          className={requiresIntervention
                            ? 'relative z-0 bg-amber-500/10 animate-pulse'
                            : 'relative z-0 hover:bg-zinc-800/40'}
                        >
                          <td className="overflow-visible px-4 py-3 font-medium text-zinc-200">{position.symbol}</td>
                          <td className="overflow-visible px-4 py-3 text-right text-zinc-300">{Number(position.qty || 0).toFixed(4)}</td>
                          <td className="overflow-visible px-4 py-3 text-right">
                            <div className="inline-flex min-w-40 flex-col items-end gap-1">
                              <span className={`font-semibold ${semantic.textClass}`}>{toPct(position.funding_rate_pct)}</span>
                              <div className="h-1.5 w-full rounded-full bg-zinc-800">
                                <div
                                  className={`h-1.5 rounded-full ${semantic.barClass}`}
                                  style={{ width: `${magnitude}%` }}
                                />
                              </div>
                            </div>
                          </td>
                          <td className="overflow-visible px-4 py-3 text-right text-zinc-300">{formatBreakeven(position.breakeven_periods)}</td>
                          <td className={`overflow-visible px-4 py-3 text-right font-semibold ${pnlClass}`}>{formatPnl(position.unrealized_pnl)}</td>
                          <td className="overflow-visible px-4 py-3 text-left">
                            {requiresIntervention ? (
                              <span className="inline-flex items-center gap-2 rounded-md border border-rose-500/60 bg-rose-500/20 px-2 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-rose-200">
                                <AlertTriangle className="h-3.5 w-3.5" />
                                Intervención
                              </span>
                            ) : (
                              <span className="inline-flex items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-xs font-semibold uppercase tracking-[0.12em] text-emerald-300">
                                Estable
                              </span>
                            )}
                            {requiresIntervention && (
                              <p className="mt-1 max-w-xs text-[11px] text-amber-200/90">{interventionReason}</p>
                            )}
                          </td>
                          <td className="overflow-visible px-4 py-3 text-right">
                            <div className="flex justify-end gap-2">
                              <button
                                type="button"
                                onClick={() => handleClosePosition(position.symbol)}
                                disabled={pendingCloseSymbol === position.symbol}
                                className="inline-flex items-center justify-center gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs font-medium uppercase tracking-[0.12em] text-rose-200 transition hover:bg-rose-500/20"
                              >
                                <X className="h-4 w-4" />
                                {pendingCloseSymbol === position.symbol ? 'Cerrando...' : 'Cerrar'}
                              </button>
                              {requiresIntervention && (
                                <button
                                  type="button"
                                  onClick={() => handleForceCleanup(position.symbol)}
                                  disabled={pendingForceCleanup === position.symbol}
                                  className="inline-flex items-center justify-center rounded-lg border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-xs font-medium uppercase tracking-[0.12em] text-zinc-200 transition hover:border-zinc-500 hover:bg-zinc-700/60 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                  {pendingForceCleanup === position.symbol ? 'Limpiando...' : 'Forzar Limpieza DB'}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>

        <section className="space-y-4 pb-10">
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-cyan-300" />
            <h2 className="text-base font-semibold text-zinc-100 sm:text-lg">Live Arbitrage Scanner</h2>
          </div>

          <div className="overflow-visible rounded-2xl border border-zinc-800 bg-zinc-900/80">
            <div className="overflow-x-auto overflow-y-visible">
              <table className="min-w-full divide-y divide-zinc-800 text-sm">
                <thead className="bg-zinc-900/90">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">
                      <Tooltip
                        text="El par de criptomonedas que muestra una ineficiencia en el mercado."
                        position="bottom"
                      >
                        <span>PAIR</span>
                      </Tooltip>
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">
                      <Tooltip
                        text="La tasa de interés que pagan los traders apalancados cada 8 horas. Si es positiva, los que apuestan a la baja (Short) cobran este porcentaje."
                        position="bottom"
                      >
                        <span>FUNDING RATE</span>
                      </Tooltip>
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">
                      Funding Signal
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">
                      Next Funding (T-)
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-[0.16em] text-zinc-400">Net APR</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/80">
                  {sortedScanResults.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-zinc-500">
                        {isScannerPaused ? 'Escáner en pausa (solo visual).' : 'Esperando stream del scanner...'}
                      </td>
                    </tr>
                  ) : (
                    sortedScanResults.map((row) => {
                      const semantic = getFundingSemantic(row.funding_rate_pct)
                      const magnitude = fundingMagnitudePercent(row.funding_rate_pct)
                      const countdown = formatCountdown(row.next_funding_time, nowMs)

                      return (
                        <tr key={row.symbol} className="hover:bg-zinc-800/40">
                          <td className="overflow-visible px-4 py-3 font-medium text-zinc-200">{row.symbol}</td>
                          <td className={`overflow-visible px-4 py-3 text-right font-semibold ${semantic.textClass}`}>
                            {toPct(row.funding_rate_pct)}
                          </td>
                          <td className="overflow-visible px-4 py-3">
                            <div className="flex items-center gap-3">
                              <span className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] ${semantic.chipClass}`}>
                                {semantic.label}
                              </span>
                              <div className="h-1.5 flex-1 rounded-full bg-zinc-800">
                                <div
                                  className={`h-1.5 rounded-full transition-all duration-500 ${semantic.barClass}`}
                                  style={{ width: `${magnitude}%` }}
                                />
                              </div>
                            </div>
                          </td>
                          <td className="overflow-visible px-4 py-3 text-right font-mono text-cyan-200">{countdown}</td>
                          <td className="overflow-visible px-4 py-3 text-right font-semibold text-emerald-300">{toPct(row.apr_est)}</td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
