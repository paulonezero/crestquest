import { FormEvent, useId, useState } from 'react'

interface UsernameFormProps {
  onSubmit: (username: string) => Promise<void>
  error?: string | null
  initialUsername?: string
}

export function UsernameForm({ onSubmit, error, initialUsername = '' }: UsernameFormProps) {
  const [username, setUsername] = useState(initialUsername)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const inputId = useId()
  const hintId = useId()
  const errorId = useId()
  const visibleError = validationError ?? error

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmedUsername = username.trim().replace(/\s+/g, ' ')
    if (!trimmedUsername) {
      setValidationError('Enter a username to continue.')
      return
    }
    if (trimmedUsername.length > 24) {
      setValidationError('Keep your username to 24 characters or fewer.')
      return
    }

    setValidationError(null)
    setIsSaving(true)
    try {
      await onSubmit(trimmedUsername)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="card welcome-card" aria-labelledby="welcome-title">
      <div className="welcome-mark" aria-hidden="true"><span>?</span></div>
      <div className="eyebrow">Begin your quest</div>
      <h1 id="welcome-title">What should the realm call you?</h1>
      <p className="lead">Pick a leaderboard name, then prove how well you know the badges of European football.</p>

      <form className="username-form" onSubmit={handleSubmit} noValidate>
        <label htmlFor={inputId}>Username</label>
        <input
          id={inputId}
          name="username"
          type="text"
          value={username}
          onChange={(event) => { setUsername(event.target.value); setValidationError(null) }}
          autoComplete="username"
          maxLength={25}
          aria-describedby={`${hintId}${visibleError ? ` ${errorId}` : ''}`}
          aria-invalid={visibleError ? true : undefined}
          disabled={isSaving}
          autoFocus
        />
        <p className="field-hint" id={hintId}>Up to 24 characters. This name appears on public leaderboards.</p>
        {visibleError && <p className="form-error" id={errorId} role="alert">{visibleError}</p>}
        <button className="button button--primary" type="submit" disabled={isSaving}>
          {isSaving ? 'Preparing your quest…' : 'Continue'}
          {!isSaving && <span aria-hidden="true">→</span>}
        </button>
      </form>
    </section>
  )
}
