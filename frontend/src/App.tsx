import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  ApiError,
  expireRound,
  getGameState,
  logoutPlayer,
  retryLeaderboard,
  retrySetup,
  startRound,
  updatePlayer,
  type GameRound,
  type GameState,
} from './api'
import { ActiveRound } from './components/ActiveRound'
import { Brand } from './components/Brand'
import { GameSetup } from './components/GameSetup'
import { Leaderboard } from './components/Leaderboard'
import { ResultScreen } from './components/ResultScreen'
import { ServiceError } from './components/ServiceError'
import { ServiceStatus } from './components/ServiceStatus'
import { UsernameForm } from './components/UsernameForm'

type ReturnView = 'setup' | 'result'
type View =
  | { kind: 'loading' }
  | { kind: 'username' }
  | { kind: 'setup' }
  | { kind: 'active' }
  | { kind: 'result' }
  | { kind: 'service' }
  | { kind: 'network-error'; detail: string }
  | { kind: 'leaderboard'; scope: string; duration: number; returnTo: ReturnView }

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected game service error occurred.'
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function resetPageScroll(container?: HTMLElement | null) {
  window.scrollTo(0, 0)
  document.documentElement.scrollTop = 0
  document.body.scrollTop = 0
  if (container) {
    container.scrollLeft = 0
    container.scrollTop = 0
  }
}

type RoundSource = 'action' | 'snapshot' | 'start'

export function shouldAcceptRound(current: GameRound | null, next: GameRound, source: RoundSource): boolean {
  if (!current) return true
  if (current.round_token === next.round_token) {
    if (current.status === 'expired' && next.status === 'active') return false
    return next.revision >= current.revision
  }
  if (source === 'start') return true
  if (current.status === 'expired' || source === 'action') return false
  return true
}

export default function App() {
  const [view, setView] = useState<View>({ kind: 'loading' })
  const [state, setState] = useState<GameState | null>(null)
  const [usernameError, setUsernameError] = useState<string | null>(null)
  const [usernameInitial, setUsernameInitial] = useState('')
  const roundRef = useRef<GameRound | null>(null)
  const stateRef = useRef<GameState | null>(null)
  const appShellRef = useRef<HTMLDivElement>(null)

  const routeAcceptedState = useCallback((next: GameState, autoOpenLeaderboard = true) => {
    if (next.service.status === 'setup-required') {
      setView({ kind: 'service' })
    } else if (!next.player) {
      setView({ kind: 'username' })
    } else if (next.round?.status === 'active') {
      setView({ kind: 'active' })
    } else if (next.round?.status === 'expired') {
      if (autoOpenLeaderboard && next.round.made_top_10 && next.service.leaderboard_ready) {
        setView({
          kind: 'leaderboard',
          scope: next.round.scope,
          duration: next.round.duration_seconds,
          returnTo: 'result',
        })
      } else {
        setView({ kind: 'result' })
      }
    } else {
      setView({ kind: 'setup' })
    }
  }, [])

  const routeState = useCallback((next: GameState, autoOpenLeaderboard = true) => {
    let acceptedRound = next.round
    if (!next.round && roundRef.current?.status === 'expired') {
      acceptedRound = roundRef.current
    } else if (next.round && !shouldAcceptRound(roundRef.current, next.round, 'snapshot')) {
      acceptedRound = roundRef.current
    } else {
      roundRef.current = next.round
    }
    const acceptedState = { ...next, round: acceptedRound }
    stateRef.current = acceptedState
    setState(acceptedState)
    routeAcceptedState(acceptedState, autoOpenLeaderboard)
  }, [routeAcceptedState])

  const loadState = useCallback(async (showLoading = true, signal?: AbortSignal) => {
    if (showLoading) setView({ kind: 'loading' })
    try {
      const next = await getGameState(signal)
      routeState(next)
    } catch (error) {
      if (!isAbortError(error)) setView({ kind: 'network-error', detail: messageFrom(error) })
    }
  }, [routeState])

  useEffect(() => {
    const controller = new AbortController()
    void loadState(true, controller.signal)
    return () => controller.abort()
  }, [loadState])

  async function saveUsername(username: string) {
    setUsernameError(null)
    try {
      routeState(await updatePlayer(username))
    } catch (error) {
      setUsernameError(messageFrom(error))
    }
  }

  async function beginRound(scope: string, duration: number) {
    const round = await startRound(scope, duration)
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
    resetPageScroll(appShellRef.current)
    updateRound(round, 'start')
  }

  const reconcile = useCallback(async () => {
    await loadState(false)
  }, [loadState])

  const updateRound = useCallback((round: GameRound, source: RoundSource = 'action'): boolean => {
    if (!shouldAcceptRound(roundRef.current, round, source)) return false
    const current = stateRef.current
    if (!current) return false

    roundRef.current = round
    const next = { ...current, round }
    stateRef.current = next
    setState(next)
    routeAcceptedState(next)
    return true
  }, [routeAcceptedState])

  const finishRound = useCallback(async () => {
    try {
      updateRound(await expireRound())
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await reconcile()
      } else {
        await loadState(false)
      }
    }
  }, [loadState, reconcile, updateRound])

  async function retryService() {
    routeState(await retrySetup())
  }

  async function retrySubmission() {
    const round = await retryLeaderboard()
    updateRound(round)
  }

  function changeUsername() {
    setUsernameInitial(stateRef.current?.player?.username ?? '')
    setUsernameError(null)
    setView({ kind: 'username' })
  }

  async function endSession() {
    try {
      await logoutPlayer()
      const current = stateRef.current
      const next = current ? { ...current, player: null, round: null } : null
      roundRef.current = null
      stateRef.current = next
      setState(next)
      setUsernameInitial('')
      setUsernameError(null)
      setView({ kind: 'username' })
    } catch (error) {
      setView({ kind: 'network-error', detail: messageFrom(error) })
    }
  }

  const scopes = state?.supported_scopes ?? []
  const durations = state?.supported_durations ?? []
  const username = state?.player?.username
  const round = state?.round
  const isGame = view.kind === 'active'

  useLayoutEffect(() => {
    if (isGame) resetPageScroll(appShellRef.current)
    document.body.classList.toggle('is-playing', isGame)
    return () => document.body.classList.remove('is-playing')
  }, [isGame])

  return (
    <div ref={appShellRef} className={`app-shell${isGame ? ' app-shell--game' : ''}`}>
      <div className="ambient ambient--one" aria-hidden="true" />
      <div className="ambient ambient--two" aria-hidden="true" />

      <header className="site-header">
        <Brand />
        {username ? (
          <div className="account-actions">
            <span className="account-name">Playing as <strong>{username}</strong></span>
            <button type="button" onClick={changeUsername}>Change username</button>
            <button type="button" onClick={() => void endSession()}>Log out</button>
          </div>
        ) : (
          <span className="header-motto">Know the badge. Name the club.</span>
        )}
      </header>

      <main className={`main-content${isGame ? ' main-content--game' : ''}`}>
        {state?.service.status === 'degraded' && view.kind !== 'service' && (
          <div className="service-banner" role="status">
            Leaderboards are temporarily unavailable. Gameplay can continue and failed score submissions can be retried.
          </div>
        )}
        {view.kind === 'loading' && (
          <div className="loading-state" role="status" aria-live="polite">
            <span className="loading-state__crest" aria-hidden="true" />
            <span>Opening the quest ledger…</span>
          </div>
        )}

        {view.kind === 'username' && (
          <UsernameForm key={usernameInitial || 'new-player'} initialUsername={usernameInitial} error={usernameError} onSubmit={saveUsername} />
        )}

        {view.kind === 'setup' && username && (
          <GameSetup
            username={username}
            scopes={scopes}
            durations={durations}
            onStart={beginRound}
            onLeaderboard={(scope, duration) => setView({ kind: 'leaderboard', scope, duration, returnTo: 'setup' })}
          />
        )}

        {view.kind === 'active' && round?.status === 'active' && (
          <ActiveRound round={round} onRoundChange={updateRound} onExpire={finishRound} onConflict={reconcile} />
        )}

        {view.kind === 'result' && round?.status === 'expired' && (
          <ResultScreen
            round={round}
            onPlayAgain={() => setView({ kind: 'setup' })}
            onLeaderboard={() => setView({ kind: 'leaderboard', scope: round.scope, duration: round.duration_seconds, returnTo: 'result' })}
            onRetrySubmission={retrySubmission}
          />
        )}

        {view.kind === 'leaderboard' && (
          <Leaderboard
            initialScope={view.scope}
            initialDuration={view.duration}
            scopes={scopes}
            durations={durations}
            onBack={() => setView({ kind: view.returnTo })}
          />
        )}

        {view.kind === 'service' && state && (
          <ServiceStatus service={state.service} onRetry={retryService} />
        )}

        {view.kind === 'network-error' && (
          <ServiceError detail={view.detail} onRetry={() => void loadState()} />
        )}
      </main>

      <footer className="site-footer">
        <span>Fast eyes. Sharp memory.</span><span aria-hidden="true">✦</span><span>Crest Quest</span>
      </footer>
    </div>
  )
}
