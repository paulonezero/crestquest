import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  advanceRound,
  ApiError,
  submitGuess,
  type ActiveRound as ActiveRoundState,
  type Choice,
  type GameRound,
  type Question,
  type Reveal,
} from '../api'
import { scopeName, timerText } from '../game'

interface ActiveRoundProps {
  round: ActiveRoundState
  onRoundChange: (round: GameRound) => boolean
  onExpire: () => Promise<void>
  onConflict: () => Promise<void>
}

interface CorrectFeedback {
  question: Question
  reveal: Reveal
  points: number
  basePoints: number
  bonusPoints: number
}

type AudioContextConstructor = new () => AudioContext

function getEffectsPreference(): boolean {
  try {
    return window.localStorage.getItem('crest-quest-effects') === 'on'
  } catch {
    return false
  }
}

function playEffect(kind: 'correct' | 'wrong') {
  const webkitWindow = window as typeof window & { webkitAudioContext?: AudioContextConstructor }
  const Context = window.AudioContext ?? webkitWindow.webkitAudioContext
  if (!Context) return

  const context = new Context()
  const oscillator = context.createOscillator()
  const gain = context.createGain()
  oscillator.type = kind === 'correct' ? 'sine' : 'triangle'
  oscillator.frequency.setValueAtTime(kind === 'correct' ? 520 : 180, context.currentTime)
  if (kind === 'correct') oscillator.frequency.exponentialRampToValueAtTime(780, context.currentTime + 0.12)
  gain.gain.setValueAtTime(0.0001, context.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.12, context.currentTime + 0.015)
  gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.18)
  oscillator.connect(gain)
  gain.connect(context.destination)
  oscillator.start()
  oscillator.stop(context.currentTime + 0.2)
  window.setTimeout(() => void context.close(), 240)
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function ActiveRound({ round, onRoundChange, onExpire, onConflict }: ActiveRoundProps) {
  const [secondsLeft, setSecondsLeft] = useState(round.remaining_seconds)
  const [isAnswering, setIsAnswering] = useState(false)
  const [wrongAnswer, setWrongAnswer] = useState<{ choice: Choice } | null>(null)
  const [feedback, setFeedback] = useState<CorrectFeedback | null>(null)
  const [status, setStatus] = useState('Choose the club that matches the crest.')
  const [effectsOn, setEffectsOn] = useState(getEffectsPreference)
  const [advancing, setAdvancing] = useState(false)
  const [expiryRetryTick, setExpiryRetryTick] = useState(0)
  const feedbackTimer = useRef<number | null>(null)
  const removalTimer = useRef<number | null>(null)
  const expiryRetryTimer = useRef<number | null>(null)
  const expiryStarted = useRef(false)
  const mounted = useRef(true)
  const answerRefs = useRef(new Map<string, HTMLButtonElement>())
  const pendingFocusToken = useRef<string | null>(null)
  const autoAdvanceToken = useRef<string | null>(null)
  const reducedMotion = prefersReducedMotion()
  const choiceSlots = useMemo(
    () => round.question.choices,
    [round.question.question_token],
  )

  const clockSync = useMemo(() => ({
    deadline: round.deadline,
    offset: round.deadline - round.remaining_seconds - Date.now() / 1000,
  }), [round.deadline, round.remaining_seconds])

  useEffect(() => {
    const update = () => {
      const serverNow = Date.now() / 1000 + clockSync.offset
      setSecondsLeft(Math.max(0, Math.ceil(clockSync.deadline - serverNow)))
    }
    update()
    const interval = window.setInterval(update, 100)
    return () => window.clearInterval(interval)
  }, [clockSync])

  useEffect(() => {
    if (secondsLeft === 0) return
    expiryStarted.current = false
    if (expiryRetryTimer.current !== null) {
      window.clearTimeout(expiryRetryTimer.current)
      expiryRetryTimer.current = null
    }
  }, [secondsLeft])

  useEffect(() => {
    if (secondsLeft !== 0 || expiryStarted.current) return
    expiryStarted.current = true
    setStatus('Time is up. Finalising your score…')
    void onExpire().finally(() => {
      if (!mounted.current) return
      expiryRetryTimer.current = window.setTimeout(() => {
        expiryStarted.current = false
        setExpiryRetryTick((tick) => tick + 1)
      }, 1000)
    })
  }, [expiryRetryTick, onExpire, secondsLeft])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current)
      if (removalTimer.current !== null) window.clearTimeout(removalTimer.current)
      if (expiryRetryTimer.current !== null) window.clearTimeout(expiryRetryTimer.current)
    }
  }, [])

  function triggerEffect(kind: 'correct' | 'wrong') {
    if (!effectsOn) return
    playEffect(kind)
    if ('vibrate' in navigator) navigator.vibrate(kind === 'correct' ? [35, 35, 55] : 45)
  }

  const continueAfter = useCallback(async (advanceToken: string) => {
    if (advancing) return
    setAdvancing(true)
    try {
      const nextRound = await advanceRound(advanceToken)
      if (onRoundChange(nextRound)) {
        setStatus('Choose the club that matches the crest.')
        if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current)
        feedbackTimer.current = window.setTimeout(() => setFeedback(null), 3000)
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await onConflict()
      } else {
        setStatus(error instanceof Error ? `${error.message} Retry the next crest.` : 'Could not load the next crest. Retry to continue.')
      }
    } finally {
      setAdvancing(false)
    }
  }, [advancing, onConflict, onRoundChange])

  useEffect(() => {
    if (!round.awaiting_advance || advancing || autoAdvanceToken.current === round.advance_token) return
    autoAdvanceToken.current = round.advance_token
    setStatus(round.reveal ? `Correct: ${round.reveal.name}. Loading the next crest…` : 'Correct. Loading the next crest…')
    void continueAfter(round.advance_token)
  }, [advancing, continueAfter, round])

  const choose = useCallback(async (choice: Choice) => {
    if (isAnswering || round.awaiting_advance || secondsLeft <= 0 || round.question.removed_answer_tokens.includes(choice.answer_token)) return
    setIsAnswering(true)
    if (feedbackTimer.current !== null) {
      window.clearTimeout(feedbackTimer.current)
      feedbackTimer.current = null
    }
    setFeedback(null)
    setStatus(`Checking ${choice.name}…`)
    const answeredQuestion = round.question

    try {
      const response = await submitGuess(answeredQuestion.question_token, choice.answer_token)
      if (!onRoundChange(response.state)) return

      if (response.correct && response.reveal && response.state.awaiting_advance) {
        triggerEffect('correct')
        const advanceToken = response.state.advance_token
        if (feedbackTimer.current !== null) window.clearTimeout(feedbackTimer.current)
        setFeedback({
          question: answeredQuestion,
          reveal: response.reveal,
          points: response.points_awarded,
          basePoints: response.base_points,
          bonusPoints: response.bonus_points,
        })
        setStatus(`Correct: ${response.reveal.name}. Loading the next crest…`)
        autoAdvanceToken.current = advanceToken
        void continueAfter(advanceToken)
      } else {
        triggerEffect('wrong')
        const wrongIndex = choiceSlots.findIndex(
          (candidate) => candidate.answer_token === choice.answer_token,
        )
        const removedTokens = response.state.question.removed_answer_tokens
        const nextChoice = choiceSlots.slice(wrongIndex + 1).find(
          (candidate) => !removedTokens.includes(candidate.answer_token),
        ) ?? choiceSlots.slice(0, wrongIndex).reverse().find(
          (candidate) => !removedTokens.includes(candidate.answer_token),
        )
        pendingFocusToken.current = nextChoice?.answer_token ?? null
        setWrongAnswer({ choice })
        setStatus(`${choice.name} is not correct. Try again.`)
        if (removalTimer.current !== null) window.clearTimeout(removalTimer.current)
        removalTimer.current = window.setTimeout(() => setWrongAnswer(null), reducedMotion ? 0 : 430)
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await onConflict()
      } else {
        setStatus(error instanceof Error ? error.message : 'That answer could not be checked.')
      }
    } finally {
      setIsAnswering(false)
    }
  }, [choiceSlots, continueAfter, effectsOn, isAnswering, onConflict, onRoundChange, reducedMotion, round.awaiting_advance, round.question, secondsLeft])

  useEffect(() => {
    if (wrongAnswer || !pendingFocusToken.current) return
    answerRefs.current.get(pendingFocusToken.current)?.focus()
    pendingFocusToken.current = null
  }, [round.question, wrongAnswer])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target) || event.altKey || event.ctrlKey || event.metaKey) return
      const index = Number(event.key) - 1
      const choice = choiceSlots[index]
      if (
        index >= 0
        && index < 4
        && choice
        && !round.question.removed_answer_tokens.includes(choice.answer_token)
      ) {
        event.preventDefault()
        void choose(choice)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [choiceSlots, choose, round.question.removed_answer_tokens])

  function toggleEffects() {
    const next = !effectsOn
    setEffectsOn(next)
    try {
      window.localStorage.setItem('crest-quest-effects', next ? 'on' : 'off')
    } catch {
      // The preference remains active for this session if storage is unavailable.
    }
  }

  const feedbackCrestUrl = feedback?.reveal.crest_url ?? feedback?.question.crest_url
  const feedbackCrestSrc = feedbackCrestUrl
    ? `${feedbackCrestUrl}${feedbackCrestUrl.includes('?') ? '&' : '?'}reveal=${round.revision}`
    : null
  const progress = Math.max(0, Math.min(100, (secondsLeft / round.duration_seconds) * 100))

  return (
    <section className="game-screen" aria-labelledby="round-title">
      <div className="round-topline">
        <div>
          <div className="eyebrow">{scopeName(round.scope)}</div>
          <h1 id="round-title">Name that crest</h1>
        </div>
        <button
          className={`effects-toggle${effectsOn ? ' is-on' : ''}`}
          type="button"
          role="switch"
          aria-checked={effectsOn}
          onClick={toggleEffects}
        >
          <span className="effects-toggle__icon" aria-hidden="true">{effectsOn ? '♪' : '×'}</span>
          Sound &amp; haptics {effectsOn ? 'on' : 'off'}
        </button>
      </div>

      <div className={`timer-panel${secondsLeft <= 10 ? ' timer-panel--urgent' : ''}`}>
        <div className="timer-panel__labels">
          <span>Time remaining</span>
          <strong aria-label={`${secondsLeft} seconds remaining`}>{timerText(secondsLeft)}</strong>
        </div>
        <div className="timer-track" role="progressbar" aria-label="Round time remaining" aria-valuemin={0} aria-valuemax={round.duration_seconds} aria-valuenow={secondsLeft}>
          <span style={{ width: `${progress}%` }} />
        </div>
      </div>

      <div className="question-card">
        <div className="crest-panel">
          <div className="crest-stage">
            <span className="crest-stage__glow" aria-hidden="true" />
            <img
              key={round.question.question_token}
              src={round.question.crest_url}
              width={256}
              height={256}
              alt="Mystery football club crest"
            />
          </div>
        </div>

        <div className="round-controls">
          <div className="round-stats" aria-label="Round statistics">
            <div><span>Question</span><strong>{round.question.round_number}</strong></div>
            <div><span>Score</span><strong>{round.score.toLocaleString()}</strong></div>
            <div><span>Clubs named</span><strong>{round.correct_answers}</strong></div>
            <div><span>Streak</span><strong>{round.first_attempt_streak}</strong></div>
          </div>

          <div className="answer-area">
            <div className="answer-heading">
              <div>
                <h2>Which club is this?</h2>
                <p className={`answer-status${feedback ? ' answer-status--correct' : ''}`} role="status" aria-live="polite" aria-atomic="true">
                  {feedback ? (
                    <>
                      <strong>Correct: {feedback.reveal.name}</strong>
                      <span>+{feedback.points} points · {feedback.basePoints} base{feedback.bonusPoints > 0 ? ` + ${feedback.bonusPoints} clean-three bonus` : ''}</span>
                    </>
                  ) : status}
                </p>
              </div>
              <div className="answer-heading__meta">
                <span>{round.points_available} points available</span>
                {round.awaiting_advance && !advancing && (
                  <button type="button" onClick={() => void continueAfter(round.advance_token)}>Retry next crest</button>
                )}
              </div>
            </div>
            <div className="answer-grid" aria-busy={isAnswering || advancing}>
              {choiceSlots.map((choice, index) => {
                  const isWrong = wrongAnswer?.choice.answer_token === choice.answer_token
                  const isRemoved = round.question.removed_answer_tokens.includes(choice.answer_token)
                  if (isRemoved && !isWrong) {
                    return (
                      <div
                        className="answer-slot--empty"
                        key={choice.answer_token}
                        data-answer-slot={choice.answer_token}
                        aria-hidden="true"
                      />
                    )
                  }
                  return (
                    <button
                      className={`answer-button${isWrong ? ' answer-button--wrong' : ''}`}
                      type="button"
                      key={choice.answer_token}
                      data-answer-slot={choice.answer_token}
                      ref={(element) => {
                        if (element) answerRefs.current.set(choice.answer_token, element)
                        else answerRefs.current.delete(choice.answer_token)
                      }}
                      disabled={isAnswering || advancing || round.awaiting_advance || secondsLeft <= 0 || isWrong}
                      aria-label={choice.name}
                      aria-describedby={`answer-league-${choice.answer_token}`}
                      onClick={() => void choose(choice)}
                    >
                      <kbd aria-hidden="true">{index + 1}</kbd>
                      <span className="answer-button__label">
                        <span>{choice.name}</span>
                        <span
                          className="answer-button__league"
                          id={`answer-league-${choice.answer_token}`}
                        >
                          {choice.league}
                        </span>
                      </span>
                      {isWrong && <span className="answer-button__wrong" aria-hidden="true">×</span>}
                    </button>
                  )
              })}
            </div>
          </div>

          <div className={`round-footer${feedback ? ' round-footer--feedback' : ''}`}>
            {feedback && feedbackCrestSrc && (
              <aside className="recent-answer" aria-label={`Previous correct answer: ${feedback.reveal.name}, ${feedback.points} points`}>
                <img src={feedbackCrestSrc} width={40} height={40} alt={`${feedback.reveal.name} revealed crest`} />
                <div className="recent-answer__club">
                  <span><span aria-hidden="true">✓</span> Correct answer</span>
                  <strong>{feedback.reveal.name}</strong>
                </div>
                <strong className="recent-answer__score">+{feedback.points}</strong>
              </aside>
            )}
            <div className="round-footer-stats" aria-label="Bonus statistics">
              <span>Best streak <strong>{round.best_streak}</strong></span>
              <span>Clean three <strong>{round.clean_three_progress}/3</strong></span>
              <span>Bonuses <strong>{round.clean_three_bonuses}</strong></span>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
