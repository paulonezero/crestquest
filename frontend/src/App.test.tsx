import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App, { shouldAcceptRound } from './App'
import type { ActiveRound, ExpiredRound, GameState } from './api'

const scopes = ['all', 'premier-league', 'bundesliga', 'la-liga', 'primeira-liga', 'ligue-1', 'serie-a', 'eredivisie', 'championship']
const durations = [30, 60, 90]
const readyService = { status: 'ready' as const, data_ready: true, leaderboard_ready: true, detail: null }

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

function question(token = 'question-old', roundNumber = 1) {
  return {
    question_token: token,
    crest_url: `/api/questions/${token}/crest`,
    choices: [
      { answer_token: `${token}-a`, name: 'Arsenal', league: 'Premier League' },
      { answer_token: `${token}-b`, name: 'Barcelona', league: 'La Liga' },
      { answer_token: `${token}-c`, name: 'Celtic', league: 'Scottish Premiership' },
      { answer_token: `${token}-d`, name: 'Dortmund', league: 'Bundesliga' },
    ],
    removed_answer_tokens: [] as string[],
    round_number: roundNumber,
  }
}

type ActiveRoundOverrides = Partial<Omit<ActiveRound, 'advance_token' | 'awaiting_advance'>> & {
  advance_token?: string | null
  awaiting_advance?: boolean
}

function activeRound(overrides: ActiveRoundOverrides = {}): ActiveRound {
  const awaitingAdvance = overrides.awaiting_advance ?? false
  return {
    status: 'active',
    round_token: 'round-1',
    revision: 1,
    scope: 'all',
    duration_seconds: 60,
    remaining_seconds: 60,
    deadline: Date.now() / 1000 + 60,
    score: 0,
    points_available: 100,
    first_attempt_streak: 0,
    best_streak: 0,
    clean_three_progress: 0,
    clean_three_bonuses: 0,
    correct_answers: 0,
    incorrect_selections: 0,
    flawless_bonus: 0,
    awaiting_advance: awaitingAdvance,
    advance_token: awaitingAdvance ? (overrides.advance_token ?? 'advance-1') : null,
    question: question(),
    reveal: null,
    ...overrides,
  } as ActiveRound
}

function expiredRound(overrides: Partial<ExpiredRound> = {}): ExpiredRound {
  return {
    status: 'expired',
    round_token: 'round-1',
    revision: 10,
    scope: 'all',
    duration_seconds: 60,
    final_score: 1250,
    clubs_named: 8,
    incorrect_selections: 2,
    best_streak: 4,
    clean_three_bonuses: 2,
    flawless_multiplier: 1,
    final_unanswered_club: {
      answer_token: 'final-answer',
      name: 'Ajax',
      crest_url: '/api/questions/final-question/crest',
    },
    leaderboard_submission_pending: false,
    made_top_10: false,
    ...overrides,
  }
}

function gameState(round: GameState['round'] = null, player: GameState['player'] = { username: 'Arden' }): GameState {
  return { player, supported_scopes: scopes, supported_durations: durations, service: readyService, round }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

describe('Crest Quest frontend', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    window.localStorage.clear()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('scrollTo', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('collects a username and presents all setup choices', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(null, null)))
      .mockResolvedValueOnce(jsonResponse(gameState()))

    render(<App />)

    await user.type(await screen.findByLabelText('Username'), 'Arden')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByRole('heading', { name: 'Welcome, Arden.' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(12)
    expect(screen.getByRole('radio', { name: 'Eredivisie' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Championship' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /90 seconds/i })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/player', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({ username: 'Arden' }),
    }))
  })

  it('changes a username without clearing the anonymous identity', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState()))
      .mockResolvedValueOnce(jsonResponse(gameState(null, { username: 'Blair' })))

    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Change username' }))
    const input = screen.getByLabelText('Username')
    expect(input).toHaveValue('Arden')
    await user.clear(input)
    await user.type(input, 'Blair')
    await user.click(screen.getByRole('button', { name: /continue/i }))

    expect(await screen.findByRole('heading', { name: 'Welcome, Blair.' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/player', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({ username: 'Blair' }),
    }))
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/player/logout')).toBe(false)
  })

  it('enforces revision, terminal-state, and deliberate-start round acceptance', () => {
    const current = activeRound({ revision: 4 })
    expect(shouldAcceptRound(current, activeRound({ revision: 3 }), 'action')).toBe(false)

    const expired = expiredRound({ revision: 5 })
    expect(shouldAcceptRound(expired, activeRound({ revision: 99 }), 'snapshot')).toBe(false)
    const nextRound = activeRound({ round_token: 'round-2', revision: 1 })
    expect(shouldAcceptRound(expired, nextRound, 'snapshot')).toBe(false)
    expect(shouldAcceptRound(expired, nextRound, 'start')).toBe(true)
  })

  it('starts a round with the selected scope and duration', async () => {
    const user = userEvent.setup()
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState()))
      .mockResolvedValueOnce(jsonResponse(activeRound({ scope: 'premier-league', duration_seconds: 30, remaining_seconds: 30, deadline: Date.now() / 1000 + 30 })))

    render(<App />)

    await user.click(await screen.findByRole('radio', { name: 'Premier League' }))
    await user.click(screen.getByRole('radio', { name: /30 seconds/i }))
    const appShell = document.querySelector<HTMLElement>('.app-shell')
    if (!appShell) throw new Error('App shell was not rendered')
    appShell.scrollLeft = 36
    appShell.scrollTop = 423
    await user.click(screen.getByRole('button', { name: /start quest/i }))

    expect(await screen.findByRole('heading', { name: 'Name that crest' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Mystery football club crest' })).toHaveAttribute('src', '/api/questions/question-old/crest')
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
    expect(appShell.scrollLeft).toBe(0)
    expect(appShell.scrollTop).toBe(0)
    expect(fetchMock).toHaveBeenLastCalledWith('/api/round/start', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ scope: 'premier-league', duration: 30 }),
    }))
  })

  it('shakes and removes a wrong answer without revealing the club', async () => {
    const user = userEvent.setup()
    const initial = activeRound()
    const wrongState = activeRound({
      incorrect_selections: 1,
      points_available: 75,
      question: {
        ...initial.question,
        choices: initial.question.choices.slice(1),
        removed_answer_tokens: ['question-old-a'],
      },
    })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(initial)))
      .mockResolvedValueOnce(jsonResponse({ correct: false, points_awarded: 0, base_points: 0, bonus_points: 0, reveal: null, state: wrongState }))

    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Arsenal' }))

    expect(await screen.findByText('Arsenal is not correct. Try again.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Arsenal' })).toHaveClass('answer-button--wrong')
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Arsenal' })).not.toBeInTheDocument(), { timeout: 1000 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Barcelona' })).toHaveFocus())
    const answerSlots = Array.from(document.querySelectorAll('.answer-grid > [data-answer-slot]'))
    expect(answerSlots.map((slot) => slot.getAttribute('data-answer-slot'))).toEqual(
      initial.question.choices.map((choice) => choice.answer_token),
    )
    expect(answerSlots[0]).toHaveClass('answer-slot--empty')
    expect(screen.queryByText(/ajax/i)).not.toBeInTheDocument()
  })

  it('supports number-key answer selection', async () => {
    const initial = activeRound()
    const wrongState = activeRound({ question: { ...initial.question, removed_answer_tokens: ['question-old-b'] } })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(initial)))
      .mockResolvedValueOnce(jsonResponse({ correct: false, points_awarded: 0, base_points: 0, bonus_points: 0, reveal: null, state: wrongState }))

    render(<App />)
    await screen.findByRole('button', { name: 'Barcelona' })
    fireEvent.keyDown(window, { key: '2' })

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/round/guess', expect.objectContaining({
      body: JSON.stringify({ question_token: 'question-old', answer_token: 'question-old-b' }),
    })))
  })

  it('shows correct feedback without delaying the next question', async () => {
    const user = userEvent.setup()
    const initial = activeRound()
    const nextQuestion = question('question-next', 2)
    nextQuestion.choices[0].name = 'Chelsea'
    const awaiting = activeRound({
      score: 125,
      correct_answers: 1,
      first_attempt_streak: 1,
      awaiting_advance: true,
      question: nextQuestion,
      reveal: { answer_token: 'question-old-a', name: 'Arsenal' },
    })
    const advanced = activeRound({ ...awaiting, revision: 3, awaiting_advance: false, advance_token: null, reveal: null })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(initial)))
      .mockResolvedValueOnce(jsonResponse({
        correct: true,
        points_awarded: 125,
        base_points: 100,
        bonus_points: 25,
        reveal: { answer_token: 'question-old-a', name: 'Arsenal' },
        state: awaiting,
      }))
      .mockResolvedValueOnce(jsonResponse(advanced))

    render(<App />)
    const coveredCrest = await screen.findByRole('img', {
      name: 'Mystery football club crest',
    })
    expect(coveredCrest).toHaveAttribute('src', '/api/questions/question-old/crest')
    expect(coveredCrest).toHaveAttribute('width', '256')
    expect(coveredCrest).toHaveAttribute('height', '256')
    await user.click(screen.getByRole('button', { name: 'Arsenal' }))

    expect(await screen.findByText('Correct: Arsenal', { exact: true })).toBeInTheDocument()
    expect(screen.getByText(/\+125 points/)).toBeInTheDocument()

    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/round/advance', expect.objectContaining({
      body: JSON.stringify({ advance_token: 'advance-1' }),
    })))
    const nextCrest = screen.getByRole('img', { name: 'Mystery football club crest' })
    expect(nextCrest).toHaveAttribute('src', '/api/questions/question-next/crest')
    expect(nextCrest).toHaveAttribute('width', '256')
    expect(nextCrest).toHaveAttribute('height', '256')
    const revealedCrest = screen.getByRole('img', { name: 'Arsenal revealed crest' })
    expect(revealedCrest).toHaveAttribute('src', '/api/questions/question-old/crest?reveal=3')
    expect(screen.getByLabelText('Previous correct answer: Arsenal, 125 points')).toBeInTheDocument()
    expect(screen.getByText('+125', { exact: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Chelsea' })).toBeEnabled()
    expect(screen.getByText('Correct: Arsenal', { exact: true })).toBeInTheDocument()
  })

  it('recovers awaiting-advance state with its required advance token', async () => {
    const recovered = activeRound({
      awaiting_advance: true,
      advance_token: 'recovered-advance',
      reveal: { answer_token: 'question-old-a', name: 'Arsenal' },
    })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(recovered)))
      .mockResolvedValueOnce(jsonResponse(activeRound({ revision: 2 })))

    render(<App />)

    expect(await screen.findByRole('img', { name: 'Mystery football club crest' })).toHaveAttribute('src', '/api/questions/question-old/crest')
    expect(screen.queryByRole('img', { name: 'Arsenal crest' })).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/round/advance', expect.objectContaining({
      body: JSON.stringify({ advance_token: 'recovered-advance' }),
    })), { timeout: 1400 })
  })

  it('expires at zero and renders the complete result', async () => {
    const atZero = activeRound({ remaining_seconds: 0, deadline: Date.now() / 1000 })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(atZero)))
      .mockResolvedValueOnce(jsonResponse(expiredRound()))

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Quest complete.' })).toBeInTheDocument()
    expect(screen.getByText('1,250')).toBeInTheDocument()
    expect(screen.getByText('Ajax')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Ajax crest' })).toHaveAttribute(
      'src',
      '/api/questions/final-question/crest?reveal=10',
    )
    expect(screen.getByText('Clean-three bonuses')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/round/expire', expect.objectContaining({ method: 'POST' }))
  })

  it('retries expiry after an early 409 reconciles to active, with backoff', async () => {
    let stateCalls = 0
    let expireCalls = 0
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/state') {
        stateCalls += 1
        const round = stateCalls === 1
          ? activeRound({ remaining_seconds: 0, deadline: Date.now() / 1000 })
          : activeRound({ revision: 2, remaining_seconds: 0, deadline: Date.now() / 1000 })
        return Promise.resolve(jsonResponse(gameState(round)))
      }
      if (url === '/api/round/expire') {
        expireCalls += 1
        return Promise.resolve(expireCalls === 1
          ? jsonResponse({ detail: 'Round is still active.' }, 409)
          : jsonResponse(expiredRound({ revision: 3 })))
      }
      throw new Error(`Unexpected request: ${url}`)
    })

    render(<App />)

    await waitFor(() => expect(stateCalls).toBe(2))
    await new Promise((resolve) => window.setTimeout(resolve, 250))
    expect(expireCalls).toBe(1)
    expect(await screen.findByRole('heading', { name: 'Quest complete.' }, { timeout: 2500 })).toBeInTheDocument()
    expect(expireCalls).toBe(2)
  })

  it('does not let a delayed guess response regress an expired round', async () => {
    const pendingGuess = deferred<Response>()
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/state') return Promise.resolve(jsonResponse(gameState(activeRound({ remaining_seconds: 1, deadline: Date.now() / 1000 + 1 }))))
      if (url === '/api/round/guess') return pendingGuess.promise
      if (url === '/api/round/expire') return Promise.resolve(jsonResponse(expiredRound({ revision: 3 })))
      throw new Error(`Unexpected request: ${url}`)
    })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Arsenal' }))

    expect(await screen.findByRole('heading', { name: 'Quest complete.' }, { timeout: 2500 })).toBeInTheDocument()
    pendingGuess.resolve(jsonResponse({
      correct: false,
      points_awarded: 0,
      base_points: 0,
      bonus_points: 0,
      reveal: null,
      state: activeRound({ revision: 2 }),
    }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Quest complete.' })).toBeInTheDocument())
  })

  it('does not let a delayed advance response regress an expired round', async () => {
    const pendingExpiry = deferred<Response>()
    const pendingAdvance = deferred<Response>()
    const awaiting = activeRound({
      revision: 2,
      remaining_seconds: 0,
      deadline: Date.now() / 1000,
      score: 100,
      correct_answers: 1,
      awaiting_advance: true,
      advance_token: 'advance-race',
      reveal: { answer_token: 'question-old-a', name: 'Arsenal' },
    })
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/state') return Promise.resolve(jsonResponse(gameState(activeRound())))
      if (url === '/api/round/guess') return Promise.resolve(jsonResponse({
        correct: true,
        points_awarded: 100,
        base_points: 100,
        bonus_points: 0,
        reveal: { answer_token: 'question-old-a', name: 'Arsenal' },
        state: awaiting,
      }))
      if (url === '/api/round/expire') return pendingExpiry.promise
      if (url === '/api/round/advance') return pendingAdvance.promise
      throw new Error(`Unexpected request: ${url}`)
    })

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Arsenal' }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => url === '/api/round/expire')).toBe(true)
      expect(fetchMock.mock.calls.some(([url]) => url === '/api/round/advance')).toBe(true)
    })

    pendingExpiry.resolve(jsonResponse(expiredRound({ revision: 4 })))
    expect(await screen.findByRole('heading', { name: 'Quest complete.' })).toBeInTheDocument()
    pendingAdvance.resolve(jsonResponse(activeRound({ revision: 3 })))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Quest complete.' })).toBeInTheDocument())
  })

  it('automatically opens the matching leaderboard after a top-10 result', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(expiredRound({ made_top_10: true, scope: 'serie-a', duration_seconds: 90 }))))
      .mockResolvedValueOnce(jsonResponse({ scope: 'serie-a', duration: 90, entries: [{ rank: 1, username: 'Arden', score: 1250, clubs_named: 8, incorrect_selections: 2, best_streak: 4, clean_three_bonuses: 2, flawless_multiplier: 1, is_current_player: true, submitted_at: '2026-08-26T10:00:00Z' }] }))

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Leaderboard' })).toBeInTheDocument()
    expect(await screen.findByRole('rowheader', { name: /Arden/ })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/leaderboard?scope=serie-a&duration=90', expect.objectContaining({ method: 'GET' }))
  })

  it('uses the server current-player marker instead of matching usernames', async () => {
    const user = userEvent.setup()
    const entry = {
      score: 900,
      clubs_named: 6,
      incorrect_selections: 1,
      best_streak: 3,
      clean_three_bonuses: 1,
      flawless_multiplier: 1,
      submitted_at: '2026-08-26T10:00:00Z',
    }
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState()))
      .mockResolvedValueOnce(jsonResponse({
        scope: 'all',
        duration: 60,
        entries: [
          { ...entry, rank: 1, username: 'Arden', is_current_player: false },
          { ...entry, rank: 2, username: 'Blair', is_current_player: true },
        ],
      }))

    render(<App />)
    await user.click(await screen.findByRole('button', { name: /view leaderboard/i }))

    const ardenRow = (await screen.findByRole('rowheader', { name: 'Arden' })).closest('tr')
    const blairRow = screen.getByRole('rowheader', { name: /Blair.*you/ }).closest('tr')
    expect(ardenRow).not.toHaveClass('is-current-player')
    expect(blairRow).toHaveClass('is-current-player')
  })

  it('describes pending submissions as session-scoped and only shows an earned 2x bonus', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(gameState(expiredRound({ leaderboard_submission_pending: true }))))
    const first = render(<App />)

    expect(await screen.findByText('This score can be retried while this session remains available.')).toBeInTheDocument()
    expect(screen.queryByText(/flawless/i)).not.toBeInTheDocument()

    first.unmount()
    fetchMock.mockResolvedValueOnce(jsonResponse(gameState(expiredRound({ flawless_multiplier: 2 }))))
    render(<App />)
    expect(await screen.findByText('Flawless bonus earned')).toBeInTheDocument()
    expect(screen.getByText('×2')).toBeInTheDocument()
  })

  it('persists the sound and haptics preference across mounts', async () => {
    const user = userEvent.setup()
    fetchMock.mockResolvedValueOnce(jsonResponse(gameState(activeRound())))
    const first = render(<App />)

    const toggle = await screen.findByRole('switch', { name: 'Sound & haptics off' })
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(window.localStorage.getItem('crest-quest-effects')).toBe('on')

    first.unmount()
    fetchMock.mockResolvedValueOnce(jsonResponse(gameState(activeRound())))
    render(<App />)
    expect(await screen.findByRole('switch', { name: 'Sound & haptics on' })).toHaveAttribute('aria-checked', 'true')
  })

  it('honours reduced motion when removing a wrong answer', async () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true })
    vi.stubGlobal('matchMedia', matchMedia)
    const initial = activeRound()
    const wrongState = activeRound({
      revision: 2,
      question: {
        ...initial.question,
        choices: initial.question.choices.slice(1),
        removed_answer_tokens: ['question-old-a'],
      },
    })
    fetchMock
      .mockResolvedValueOnce(jsonResponse(gameState(initial)))
      .mockResolvedValueOnce(jsonResponse({ correct: false, points_awarded: 0, base_points: 0, bonus_points: 0, reveal: null, state: wrongState }))

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Arsenal' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Arsenal' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Barcelona' })).toHaveFocus())
    expect(matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
  })

  it('keeps gameplay available while leaderboards are degraded', async () => {
    const degraded = gameState()
    degraded.service = {
      status: 'degraded',
      data_ready: true,
      leaderboard_ready: false,
      detail: 'Leaderboard unavailable.',
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(degraded))

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Welcome, Arden.' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/gameplay can continue/i)
  })

  it('retries setup-required service state', async () => {
    const user = userEvent.setup()
    const unavailable: GameState = {
      ...gameState(null, null),
      service: { status: 'setup-required', data_ready: false, leaderboard_ready: true, detail: 'Catalogue missing.' },
    }
    fetchMock
      .mockResolvedValueOnce(jsonResponse(unavailable))
      .mockResolvedValueOnce(jsonResponse(gameState()))

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'The quest needs its club data.' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry service setup' }))
    expect(await screen.findByRole('heading', { name: 'Welcome, Arden.' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/setup/retry', expect.objectContaining({ method: 'POST' }))
  })

  it.each([375, 768, 1440])(
    'renders the complete active-round controls at a %ipx viewport',
    async (width) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
      window.dispatchEvent(new Event('resize'))
      fetchMock.mockResolvedValueOnce(jsonResponse(gameState(activeRound())))

      render(<App />)

      expect(await screen.findByRole('heading', { name: 'Name that crest' })).toBeInTheDocument()
      expect(screen.getByRole('progressbar', { name: 'Round time remaining' })).toBeInTheDocument()
      expect(screen.getAllByRole('button', { name: /Arsenal|Barcelona|Celtic|Dortmund/ })).toHaveLength(4)
    },
  )

  it('provides accessible game landmarks, controls, labels, and live feedback', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(gameState(activeRound())))
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Name that crest' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Round time remaining' })).toHaveAttribute('aria-valuenow', '60')
    expect(screen.getByRole('switch', { name: 'Sound & haptics off' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('img', { name: 'Mystery football club crest' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Arsenal|Barcelona|Celtic|Dortmund/ })).toHaveLength(4)
    expect(screen.getByRole('button', { name: 'Arsenal' })).toHaveAccessibleDescription('Premier League')
    expect(screen.getByText('Choose the club that matches the crest.')).toHaveAttribute('aria-live', 'polite')
  })
})
