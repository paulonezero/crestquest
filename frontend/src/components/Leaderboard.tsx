import { useEffect, useState } from 'react'
import { getLeaderboard, type LeaderboardResponse } from '../api'
import { durationName, scopeName } from '../game'

interface LeaderboardProps {
  initialScope: string
  initialDuration: number
  scopes: string[]
  durations: number[]
  onBack: () => void
}

export function Leaderboard({ initialScope, initialDuration, scopes, durations, onBack }: LeaderboardProps) {
  const [scope, setScope] = useState(initialScope)
  const [duration, setDuration] = useState(initialDuration)
  const [board, setBoard] = useState<LeaderboardResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    void getLeaderboard(scope, duration, controller.signal)
      .then((response) => setBoard(response))
      .catch((caught) => {
        if (!(caught instanceof Error && caught.name === 'AbortError')) {
          setError(caught instanceof Error ? caught.message : 'The leaderboard could not be loaded.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [duration, scope])

  return (
    <section className="card leaderboard-card" aria-labelledby="leaderboard-title">
      <button className="back-button" type="button" onClick={onBack}><span aria-hidden="true">←</span> Back</button>
      <div className="leaderboard-heading">
        <div>
          <div className="eyebrow">Hall of crests</div>
          <h1 id="leaderboard-title">Leaderboard</h1>
        </div>
        <span className="board-count">{scopes.length * durations.length} boards</span>
      </div>

      <div className="board-filters" aria-label="Leaderboard filters">
        <label>
          <span>League</span>
          <select value={scope} onChange={(event) => setScope(event.target.value)}>
            {scopes.map((item) => <option value={item} key={item}>{scopeName(item)}</option>)}
          </select>
        </label>
        <label>
          <span>Duration</span>
          <select value={duration} onChange={(event) => setDuration(Number(event.target.value))}>
            {durations.map((item) => <option value={item} key={item}>{durationName(item)}</option>)}
          </select>
        </label>
      </div>

      <div className="leaderboard-status" aria-live="polite">
        {loading && 'Loading standings…'}
        {error && <span role="alert">{error}</span>}
      </div>

      {!loading && !error && board && board.entries.length === 0 && (
        <div className="empty-board"><strong>No scores yet.</strong><span>Be the first name on this board.</span></div>
      )}

      {!loading && !error && board && board.entries.length > 0 && (
        <div className="table-scroll" role="region" aria-label="Leaderboard standings" tabIndex={0}>
          <table>
            <caption className="sr-only">Top scores for {scopeName(scope)}, {durationName(duration)}</caption>
            <thead><tr><th scope="col">Rank</th><th scope="col">Player</th><th scope="col">Score</th><th scope="col">Clubs</th><th scope="col">Best streak</th><th scope="col">Errors</th><th scope="col">Bonuses</th><th scope="col">Flawless</th></tr></thead>
            <tbody>
              {board.entries.map((entry) => (
                <tr className={entry.is_current_player ? 'is-current-player' : undefined} key={`${entry.rank}-${entry.username}-${entry.submitted_at}`}>
                  <td data-label="Rank"><span className="rank">{entry.rank}</span></td>
                  <th scope="row" data-label="Player">{entry.username}{entry.is_current_player && <span className="sr-only"> (you)</span>}</th>
                  <td className="score-cell" data-label="Score">{entry.score.toLocaleString()}</td>
                  <td data-label="Clubs">{entry.clubs_named}</td>
                  <td data-label="Best streak">{entry.best_streak}</td>
                  <td data-label="Errors">{entry.incorrect_selections}</td>
                  <td data-label="Bonuses">{entry.clean_three_bonuses}</td>
                  <td data-label="Flawless">×{entry.flawless_multiplier}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
