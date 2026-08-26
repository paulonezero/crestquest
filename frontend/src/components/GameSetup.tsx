import { FormEvent, useEffect, useState } from 'react'
import { durationName, scopeName } from '../game'

interface GameSetupProps {
  username: string
  scopes: string[]
  durations: number[]
  onStart: (scope: string, duration: number) => Promise<void>
  onLeaderboard: (scope: string, duration: number) => void
}

export function GameSetup({ username, scopes, durations, onStart, onLeaderboard }: GameSetupProps) {
  const [scope, setScope] = useState(scopes[0] ?? 'all')
  const [duration, setDuration] = useState(durations[1] ?? durations[0] ?? 60)
  const [isStarting, setIsStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!scopes.includes(scope) && scopes[0]) setScope(scopes[0])
  }, [scope, scopes])

  useEffect(() => {
    if (!durations.includes(duration) && durations[0]) setDuration(durations[0])
  }, [duration, durations])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setIsStarting(true)
    try {
      await onStart(scope, duration)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The round could not be started.')
    } finally {
      setIsStarting(false)
    }
  }

  return (
    <section className="card setup-card" aria-labelledby="setup-title">
      <div className="setup-heading">
        <div>
          <div className="eyebrow">Choose your challenge</div>
          <h1 id="setup-title">Welcome, {username}.</h1>
          <p className="lead">Name as many crests as you can before the final whistle.</p>
        </div>
        <div className="player-seal" aria-hidden="true">{username.charAt(0).toUpperCase()}</div>
      </div>

      <form className="quest-form" onSubmit={handleSubmit}>
        <fieldset>
          <legend>League</legend>
          <div className="scope-grid">
            {scopes.map((item) => (
              <label className={`choice-tile${scope === item ? ' choice-tile--selected' : ''}`} key={item}>
                <input
                  type="radio"
                  name="scope"
                  value={item}
                  checked={scope === item}
                  onChange={() => setScope(item)}
                />
                <span className="choice-tile__mark" aria-hidden="true" />
                <span>{scopeName(item)}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset>
          <legend>Round length</legend>
          <div className="duration-grid">
            {durations.map((item) => (
              <label className={`duration-tile${duration === item ? ' duration-tile--selected' : ''}`} key={item}>
                <input
                  type="radio"
                  name="duration"
                  value={item}
                  checked={duration === item}
                  onChange={() => setDuration(item)}
                />
                <strong>{item}</strong>
                <span>seconds</span>
              </label>
            ))}
          </div>
        </fieldset>

        {error && <p className="form-error" role="alert">{error}</p>}
        <div className="setup-actions">
          <button className="button button--primary" type="submit" disabled={isStarting}>
            {isStarting ? 'Starting round…' : 'Start quest'}
            {!isStarting && <span aria-hidden="true">→</span>}
          </button>
          <button className="button button--secondary" type="button" onClick={() => onLeaderboard(scope, duration)}>
            View leaderboard
            <span className="sr-only"> for {scopeName(scope)}, {durationName(duration)}</span>
          </button>
        </div>
      </form>
    </section>
  )
}
