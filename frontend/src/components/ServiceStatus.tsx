import { useState } from 'react'
import type { ServiceState } from '../api'

interface ServiceStatusProps {
  service: ServiceState
  onRetry: () => Promise<void>
}

export function ServiceStatus({ service, onRetry }: ServiceStatusProps) {
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const setupRequired = service.status === 'setup-required'

  async function retry() {
    setError(null)
    setRetrying(true)
    try {
      await onRetry()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Service setup could not be retried.')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <section className="card error-card" aria-labelledby="service-status-title">
      <div className="error-card__icon" aria-hidden="true">{setupRequired ? '⌁' : '!'}</div>
      <div className="eyebrow">{setupRequired ? 'Setup required' : 'Limited service'}</div>
      <h1 id="service-status-title">{setupRequired ? 'The quest needs its club data.' : 'Crest Quest is running in degraded mode.'}</h1>
      <p className="lead">{setupRequired ? 'Finish loading the club catalogue before beginning a round.' : 'One or more game services are unavailable. Retry to restore the full experience.'}</p>
      <ul className="service-checks" aria-label="Service readiness">
        <li className={service.data_ready ? 'is-ready' : 'is-down'}><span aria-hidden="true">{service.data_ready ? '✓' : '×'}</span> Club data</li>
        <li className={service.leaderboard_ready ? 'is-ready' : 'is-down'}><span aria-hidden="true">{service.leaderboard_ready ? '✓' : '×'}</span> Leaderboards</li>
      </ul>
      {service.detail && <p className="error-detail">{service.detail}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <button className="button button--primary" type="button" disabled={retrying} onClick={() => void retry()}>{retrying ? 'Retrying setup…' : 'Retry service setup'}</button>
    </section>
  )
}
