interface ServiceErrorProps {
  detail?: string
  onRetry: () => void
}

export function ServiceError({ detail, onRetry }: ServiceErrorProps) {
  return (
    <section className="card error-card" aria-labelledby="service-error-title">
      <div className="error-card__icon" aria-hidden="true">!</div>
      <div className="eyebrow">A temporary setback</div>
      <h1 id="service-error-title">The quest service is unavailable.</h1>
      <p className="lead">
        We could not load your progress. Check that the service is running, then
        try again.
      </p>
      {detail && <p className="error-detail" role="alert">{detail}</p>}
      <button className="button button--primary" type="button" onClick={onRetry}>
        Try again
      </button>
    </section>
  )
}
