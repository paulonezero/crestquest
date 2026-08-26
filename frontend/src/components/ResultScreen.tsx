import { useState } from 'react'
import type { ExpiredRound } from '../api'
import { durationName, scopeName } from '../game'

interface ResultScreenProps {
  round: ExpiredRound
  onPlayAgain: () => void
  onLeaderboard: () => void
  onRetrySubmission: () => Promise<void>
}

export function ResultScreen({ round, onPlayAgain, onLeaderboard, onRetrySubmission }: ResultScreenProps) {
  const [isRetrying, setIsRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)

  async function retry() {
    setRetryError(null)
    setIsRetrying(true)
    try {
      await onRetrySubmission()
    } catch (error) {
      setRetryError(error instanceof Error ? error.message : 'Submission retry failed.')
    } finally {
      setIsRetrying(false)
    }
  }

  return (
    <section className="card result-card" aria-labelledby="result-title">
      <div className="eyebrow">Full time · {scopeName(round.scope)} · {durationName(round.duration_seconds)}</div>
      <h1 id="result-title">Quest complete.</h1>
      <p className="result-score"><span>Final score</span><strong>{round.final_score.toLocaleString()}</strong></p>

      {round.made_top_10 && (
        <p className="top-ten-banner" role="status">You made the top 10!</p>
      )}

      <dl className="result-stats">
        <div><dt>Clubs named</dt><dd>{round.clubs_named}</dd></div>
        <div><dt>Incorrect picks</dt><dd>{round.incorrect_selections}</dd></div>
        <div><dt>Best streak</dt><dd>{round.best_streak}</dd></div>
        <div><dt>Clean-three bonuses</dt><dd>{round.clean_three_bonuses}</dd></div>
        {round.flawless_multiplier === 2 && (
          <div className="result-stats__wide result-stats__bonus"><dt>Flawless bonus earned</dt><dd>×2</dd></div>
        )}
      </dl>

      <div className="final-reveal">
        {round.final_unanswered_club.crest_url && (
          <img
            src={`${round.final_unanswered_club.crest_url}?reveal=${round.revision}`}
            width={256}
            height={256}
            alt={`${round.final_unanswered_club.name} crest`}
          />
        )}
        <span>The crest at the whistle was</span>
        <strong>{round.final_unanswered_club.name}</strong>
      </div>

      {round.leaderboard_submission_pending && (
        <div className="submission-warning" role="alert">
          <div>
            <strong>Leaderboard submission pending</strong>
            <span>This score can be retried while this session remains available.</span>
          </div>
          <button className="button button--small" type="button" disabled={isRetrying} onClick={() => void retry()}>
            {isRetrying ? 'Retrying…' : 'Retry submission'}
          </button>
        </div>
      )}
      {retryError && <p className="form-error" role="alert">{retryError}</p>}

      <div className="result-actions">
        <button className="button button--primary" type="button" onClick={onPlayAgain}>Play again</button>
        <button className="button button--secondary" type="button" onClick={onLeaderboard}>View leaderboard</button>
      </div>
    </section>
  )
}
