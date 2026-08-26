export type ServiceStatus = 'ready' | 'setup-required' | 'degraded'
export type RoundStatus = 'active' | 'expired'

export interface Player {
  username: string
}

export interface ServiceState {
  status: ServiceStatus
  data_ready: boolean
  leaderboard_ready: boolean
  detail: string | null
}

export interface Choice {
  answer_token: string
  name: string
  league: string
}

export interface Reveal {
  answer_token: string
  name: string
  crest_url?: string
}

export interface Question {
  question_token: string
  crest_url: string
  choices: Choice[]
  removed_answer_tokens: string[]
  round_number: number
}

interface ActiveRoundBase {
  status: 'active'
  round_token: string
  revision: number
  scope: string
  duration_seconds: number
  remaining_seconds: number
  deadline: number
  score: number
  points_available: number
  first_attempt_streak: number
  best_streak: number
  clean_three_progress: number
  clean_three_bonuses: number
  correct_answers: number
  incorrect_selections: number
  flawless_bonus: number
  question: Question
  reveal: Reveal | null
}

export interface PlayableActiveRound extends ActiveRoundBase {
  awaiting_advance: false
  advance_token: null
}

export interface AwaitingAdvanceRound extends ActiveRoundBase {
  awaiting_advance: true
  advance_token: string
}

export type ActiveRound = PlayableActiveRound | AwaitingAdvanceRound

export interface ExpiredRound {
  status: 'expired'
  round_token: string
  revision: number
  scope: string
  duration_seconds: number
  final_score: number
  clubs_named: number
  incorrect_selections: number
  best_streak: number
  clean_three_bonuses: number
  flawless_multiplier: 1 | 2
  final_unanswered_club: Reveal
  leaderboard_submission_pending: boolean
  made_top_10: boolean
}

export type GameRound = ActiveRound | ExpiredRound

export interface GameState {
  player: Player | null
  supported_scopes: string[]
  supported_durations: number[]
  service: ServiceState
  round: GameRound | null
}

export interface GuessResponse {
  correct: boolean
  points_awarded: number
  base_points: number
  bonus_points: number
  reveal: Reveal | null
  state: ActiveRound
}

export interface LeaderboardEntry {
  rank: number
  username: string
  score: number
  clubs_named: number
  incorrect_selections: number
  best_streak: number
  clean_three_bonuses: number
  flawless_multiplier: number
  is_current_player: boolean
  submitted_at: string
}

export interface LeaderboardResponse {
  scope: string
  duration: number
  entries: LeaderboardEntry[]
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...init?.headers,
      },
    })
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error
    throw new ApiError('The game service could not be reached.', 0)
  }

  if (!response.ok) {
    let message = `The game service returned an error (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: string; message?: string }
      message = body.detail ?? body.message ?? message
    } catch {
      // Keep the status-based fallback for non-JSON responses.
    }
    throw new ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getGameState(signal?: AbortSignal): Promise<GameState> {
  return request<GameState>('/api/state', { method: 'GET', signal })
}

export function updatePlayer(username: string): Promise<GameState> {
  return request<GameState>('/api/player', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  })
}

export function logoutPlayer(): Promise<void> {
  return request<void>('/api/player/logout', { method: 'POST' })
}

export function startRound(scope: string, duration: number): Promise<ActiveRound> {
  return request<ActiveRound>('/api/round/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, duration }),
  })
}

export function submitGuess(questionToken: string, answerToken: string): Promise<GuessResponse> {
  return request<GuessResponse>('/api/round/guess', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question_token: questionToken, answer_token: answerToken }),
  })
}

export function advanceRound(advanceToken: string): Promise<GameRound> {
  return request<GameRound>('/api/round/advance', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ advance_token: advanceToken }),
  })
}

export function expireRound(): Promise<ExpiredRound> {
  return request<ExpiredRound>('/api/round/expire', { method: 'POST' })
}

export function getLeaderboard(scope: string, duration: number, signal?: AbortSignal): Promise<LeaderboardResponse> {
  const query = new URLSearchParams({ scope, duration: String(duration) })
  return request<LeaderboardResponse>(`/api/leaderboard?${query.toString()}`, { method: 'GET', signal })
}

export function retryLeaderboard(): Promise<ExpiredRound> {
  return request<ExpiredRound>('/api/leaderboard/retry', { method: 'POST' })
}

export function retrySetup(): Promise<GameState> {
  return request<GameState>('/api/setup/retry', { method: 'POST' })
}
