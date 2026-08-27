import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { expect, test, type Locator, type Page } from '@playwright/test'

const BASE_URL = 'http://127.0.0.1:4174'
const SCREENSHOT_DIR = fileURLToPath(new URL('../../var/screenshots/responsive/', import.meta.url))

const VIEWPORTS = [
  { width: 1024, height: 768 },
  { width: 1180, height: 820 },
  { width: 1366, height: 1024 },
  { width: 768, height: 1024 },
  { width: 820, height: 1180 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 667, height: 375 },
  { width: 844, height: 390 },
  { width: 932, height: 430 },
] as const

type Viewport = (typeof VIEWPORTS)[number]
type JsonRecord = Record<string, unknown>

const SCREENSHOT_VIEWPORTS = new Map([
  ['1024x768', 'ipad-landscape'],
  ['390x844', 'iphone-portrait'],
  ['844x390', 'iphone-landscape'],
])

const scopes = ['all', 'premier-league', 'bundesliga', 'la-liga', 'primeira-liga', 'ligue-1', 'serie-a', 'eredivisie', 'championship']
const durations = [30, 60, 90]
const service = { status: 'ready', data_ready: true, leaderboard_ready: true, detail: null }

const firstChoices = [
  { answer_token: 'question-one-a', name: 'Arsenal', league: 'Premier League' },
  { answer_token: 'question-one-b', name: 'Barcelona', league: 'La Liga' },
  { answer_token: 'question-one-c', name: 'Celtic', league: 'Scottish Premiership' },
  { answer_token: 'question-one-d', name: 'Dortmund', league: 'Bundesliga' },
]

const secondChoices = [
  { answer_token: 'question-two-a', name: 'Ajax', league: 'Eredivisie' },
  { answer_token: 'question-two-b', name: 'Benfica', league: 'Primeira Liga' },
  { answer_token: 'question-two-c', name: 'Chelsea', league: 'Premier League' },
  { answer_token: 'question-two-d', name: 'Inter', league: 'Serie A' },
]

function question(token: 'question-one' | 'question-two', roundNumber: number, choices: typeof firstChoices) {
  return {
    question_token: token,
    crest_url: `/api/questions/${token}/crest`,
    choices,
    removed_answer_tokens: [] as string[],
    round_number: roundNumber,
  }
}

function activeRound({
  revision = 1,
  remainingSeconds = 60,
  currentQuestion = question('question-one', 1, firstChoices),
}: {
  revision?: number
  remainingSeconds?: number
  currentQuestion?: ReturnType<typeof question>
} = {}) {
  return {
    status: 'active',
    round_token: 'browser-round-1',
    revision,
    scope: 'all',
    duration_seconds: 60,
    remaining_seconds: remainingSeconds,
    deadline: Date.now() / 1000 + remainingSeconds,
    score: 0,
    points_available: 100,
    first_attempt_streak: 0,
    best_streak: 0,
    clean_three_progress: 0,
    clean_three_bonuses: 0,
    correct_answers: 0,
    incorrect_selections: 0,
    flawless_bonus: 0,
    awaiting_advance: false,
    advance_token: null,
    question: currentQuestion,
    reveal: null,
  }
}

function expiredRound() {
  return {
    status: 'expired',
    round_token: 'browser-round-1',
    revision: 10,
    scope: 'all',
    duration_seconds: 60,
    final_score: 125,
    clubs_named: 1,
    incorrect_selections: 1,
    best_streak: 1,
    clean_three_bonuses: 0,
    flawless_multiplier: 1,
    final_unanswered_club: {
      answer_token: 'question-two-a',
      name: 'Ajax',
      crest_url: '/api/questions/question-two/crest',
    },
    leaderboard_submission_pending: false,
    made_top_10: false,
  }
}

function gameState(round: JsonRecord | null) {
  return {
    player: { username: 'Playwright' },
    supported_scopes: scopes,
    supported_durations: durations,
    service,
    round,
  }
}

function leaderboard() {
  return {
    scope: 'all',
    duration: 60,
    entries: [
      {
        rank: 1,
        username: 'Playwright',
        score: 125,
        clubs_named: 1,
        incorrect_selections: 1,
        best_streak: 1,
        clean_three_bonuses: 0,
        flawless_multiplier: 1,
        is_current_player: true,
        submitted_at: '2026-08-26T12:00:00Z',
      },
    ],
  }
}

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

async function installApiMock(page: Page, options: { holdGuess?: boolean } = {}) {
  let round: JsonRecord | null = null
  let allowAdvance = false
  let expireAfterAdvance = false
  const guessGate = options.holdGuess ? deferred() : null

  const counters = {
    stateRequests: 0,
    startRequests: 0,
    guessRequests: 0,
    advanceRequests: 0,
    expireRequests: 0,
  }

  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) => route.abort())
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (/^\/api\/questions\/[^/]+\/crest$/.test(path)) {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: [
          '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">',
          '<path fill="#143e34" d="M128 10 224 45v78c0 62-39 104-96 123-57-19-96-61-96-123V45z"/>',
          '<path fill="none" stroke="#f5e6c2" stroke-width="12" d="M128 32 201 59v62c0 46-27 79-73 98-46-19-73-52-73-98V59z"/>',
          '<circle cx="128" cy="117" r="42" fill="#c58f31"/>',
          '<path fill="#fffdf8" d="m128 78 12 25 28 4-20 20 5 28-25-13-25 13 5-28-20-20 28-4z"/>',
          '</svg>',
        ].join(''),
      })
      return
    }

    if (path === '/api/state' && request.method() === 'GET') {
      counters.stateRequests += 1
      await route.fulfill({ json: gameState(round) })
      return
    }

    if (path === '/api/round/start' && request.method() === 'POST') {
      counters.startRequests += 1
      round = activeRound()
      await route.fulfill({ json: round })
      return
    }

    if (path === '/api/round/guess' && request.method() === 'POST') {
      counters.guessRequests += 1
      if (guessGate) await guessGate.promise

      const body = request.postDataJSON() as { answer_token: string }
      const current = round as ReturnType<typeof activeRound>
      if (body.answer_token !== 'question-one-b') {
        const removed = [...current.question.removed_answer_tokens, body.answer_token]
        round = {
          ...current,
          revision: current.revision + 1,
          points_available: 75,
          incorrect_selections: 1,
          question: { ...current.question, removed_answer_tokens: removed },
        }
        await route.fulfill({
          json: {
            correct: false,
            points_awarded: 0,
            base_points: 0,
            bonus_points: 0,
            reveal: null,
            state: round,
          },
        })
        return
      }

      const reveal = {
        answer_token: 'question-one-b',
        name: 'Barcelona',
        crest_url: '/api/questions/question-one/crest',
      }
      round = {
        ...current,
        revision: current.revision + 1,
        score: 125,
        correct_answers: 1,
        first_attempt_streak: 0,
        best_streak: 1,
        points_available: 100,
        awaiting_advance: true,
        advance_token: 'advance-browser-round-1',
        question: question('question-two', 2, secondChoices),
        reveal,
      }
      await route.fulfill({
        json: {
          correct: true,
          points_awarded: 125,
          base_points: 100,
          bonus_points: 25,
          reveal,
          state: round,
        },
      })
      return
    }

    if (path === '/api/round/advance' && request.method() === 'POST') {
      counters.advanceRequests += 1
      if (!allowAdvance) {
        await route.fulfill({ status: 503, json: { detail: 'Advance is held by the browser test.' } })
        return
      }
      round = {
        ...activeRound({
          revision: 4,
          remainingSeconds: expireAfterAdvance ? 1 : 60,
          currentQuestion: question('question-two', 2, secondChoices),
        }),
        score: 125,
        correct_answers: 1,
        best_streak: 1,
        incorrect_selections: 1,
      }
      await route.fulfill({ json: round })
      return
    }

    if (path === '/api/round/expire' && request.method() === 'POST') {
      counters.expireRequests += 1
      round = expiredRound()
      await route.fulfill({ json: round })
      return
    }

    if (path === '/api/leaderboard' && request.method() === 'GET') {
      await route.fulfill({ json: leaderboard() })
      return
    }

    await route.fulfill({
      status: 404,
      json: { detail: `No browser-test mock is defined for ${request.method()} ${path}.` },
    })
  })

  return {
    counters,
    releaseGuess: () => guessGate?.resolve(),
    allowAdvance(options: { expireSoon?: boolean } = {}) {
      allowAdvance = true
      expireAfterAdvance = options.expireSoon ?? false
    },
  }
}

async function openSetup(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.getByRole('heading', { name: 'Welcome, Playwright.' })).toBeVisible()
}

function viewportKey(viewport: Viewport | { width: number; height: number }) {
  return `${viewport.width}x${viewport.height}`
}

async function screenshot(page: Page, device: string, state: string, fullPage = false) {
  await page.screenshot({
    path: `${SCREENSHOT_DIR}/${device}-${state}.png`,
    fullPage,
    animations: 'disabled',
  })
}

async function assertInsideViewport(locator: Locator, viewport: { width: number; height: number }, label: string) {
  await expect(locator, `${viewportKey(viewport)}: ${label} should be visible`).toBeVisible()
  const box = await locator.boundingBox()
  if (!box) throw new Error(`${viewportKey(viewport)}: ${label} has no bounding box`)

  const tolerance = 1
  if (
    box.x < -tolerance
    || box.y < -tolerance
    || box.x + box.width > viewport.width + tolerance
    || box.y + box.height > viewport.height + tolerance
  ) {
    throw new Error(`${viewportKey(viewport)}: ${label} is outside the viewport: ${JSON.stringify({ box, viewport })}`)
  }
  return box
}

async function assertNoPageOverflow(page: Page, viewport: { width: number; height: number }, state: string) {
  const measurements = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    documentScrollLeft: document.documentElement.scrollLeft,
    documentScrollTop: document.documentElement.scrollTop,
    bodyScrollLeft: document.body.scrollLeft,
    bodyScrollTop: document.body.scrollTop,
    documentClientWidth: document.documentElement.clientWidth,
    documentClientHeight: document.documentElement.clientHeight,
    documentScrollWidth: document.documentElement.scrollWidth,
    documentScrollHeight: document.documentElement.scrollHeight,
    bodyScrollWidth: document.body.scrollWidth,
    bodyScrollHeight: document.body.scrollHeight,
    appScrollLeft: document.querySelector('.app-shell')?.scrollLeft ?? 0,
    appScrollTop: document.querySelector('.app-shell')?.scrollTop ?? 0,
    visualViewport: window.visualViewport ? {
      width: window.visualViewport.width,
      height: window.visualViewport.height,
      offsetLeft: window.visualViewport.offsetLeft,
      offsetTop: window.visualViewport.offsetTop,
      pageLeft: window.visualViewport.pageLeft,
      pageTop: window.visualViewport.pageTop,
      scale: window.visualViewport.scale,
    } : null,
  }))

  const hasOverflow = measurements.documentScrollWidth > measurements.documentClientWidth
    || measurements.bodyScrollWidth > measurements.innerWidth
    || measurements.documentScrollHeight > measurements.documentClientHeight
    || measurements.bodyScrollHeight > measurements.innerHeight
    || measurements.scrollX !== 0
    || measurements.scrollY !== 0
    || measurements.documentScrollLeft !== 0
    || measurements.documentScrollTop !== 0
    || measurements.bodyScrollLeft !== 0
    || measurements.bodyScrollTop !== 0
    || measurements.appScrollLeft !== 0
    || measurements.appScrollTop !== 0
    || (measurements.visualViewport !== null && (
      measurements.visualViewport.offsetLeft !== 0
      || measurements.visualViewport.offsetTop !== 0
      || measurements.visualViewport.pageLeft !== 0
      || measurements.visualViewport.pageTop !== 0
    ))

  if (hasOverflow) {
    throw new Error(`${viewportKey(viewport)} ${state}: page overflow requires scrolling: ${JSON.stringify(measurements)}`)
  }
}

async function assertNoHorizontalOverflow(page: Page, viewport: { width: number; height: number }, state: string) {
  const measurements = await page.evaluate(() => ({
    innerWidth: window.innerWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
  }))
  if (measurements.documentScrollWidth > measurements.innerWidth || measurements.bodyScrollWidth > measurements.innerWidth) {
    throw new Error(`${viewportKey(viewport)} ${state}: horizontal overflow: ${JSON.stringify(measurements)}`)
  }
}

async function assertAnswerTargets(page: Page, viewport: Viewport, names: string[]) {
  for (const name of names) {
    const box = await assertInsideViewport(page.getByRole('button', { name, exact: true }), viewport, `${name} answer`)
    if (box.width < 44 || box.height < 44) {
      throw new Error(`${viewportKey(viewport)}: ${name} answer target is smaller than 44x44: ${JSON.stringify(box)}`)
    }
  }
}

async function assertActiveLayout(page: Page, viewport: Viewport, answerNames: string[]) {
  await assertNoPageOverflow(page, viewport, 'active round')
  await assertInsideViewport(page.getByRole('heading', { name: 'Name that crest' }), viewport, 'round heading')
  await assertInsideViewport(page.getByRole('progressbar', { name: 'Round time remaining' }), viewport, 'timer')
  await assertInsideViewport(page.getByRole('img', { name: 'Mystery football club crest' }), viewport, 'crest')
  await assertInsideViewport(page.locator('[aria-label="Round statistics"]'), viewport, 'round statistics')
  await assertInsideViewport(page.getByText('Clubs named', { exact: true }), viewport, 'clubs-named statistic')
  await assertAnswerTargets(page, viewport, answerNames)

  const imageLoaded = await page.getByRole('img', { name: 'Mystery football club crest' }).evaluate(
    (image: HTMLImageElement) => image.complete && image.naturalWidth > 0,
  )
  expect(imageLoaded, `${viewportKey(viewport)}: crest mock should decode`).toBe(true)
}

async function assertCorrectFeedbackLayout(page: Page, viewport: Viewport) {
  await assertNoPageOverflow(page, viewport, 'correct feedback')
  await assertInsideViewport(page.getByRole('progressbar', { name: 'Round time remaining' }), viewport, 'timer during correct feedback')
  await assertInsideViewport(page.getByRole('img', { name: 'Mystery football club crest' }), viewport, 'next covered crest')
  await assertInsideViewport(page.locator('[aria-label="Round statistics"]'), viewport, 'statistics during correct feedback')
  await assertInsideViewport(page.getByRole('heading', { name: 'Which club is this?' }), viewport, 'next question heading')
  await assertInsideViewport(page.getByText('Correct: Barcelona', { exact: true }), viewport, 'correct club feedback')
  await assertInsideViewport(page.getByText(/\+125 points/), viewport, 'points breakdown')
  await assertInsideViewport(page.getByRole('img', { name: 'Barcelona revealed crest' }), viewport, 'previous revealed crest')
  await assertInsideViewport(page.locator('[aria-label="Previous correct answer: Barcelona, 125 points"]'), viewport, 'previous answer card')
  await assertInsideViewport(page.getByText('+125', { exact: true }), viewport, 'previous answer score')
  const retryBox = await assertInsideViewport(page.getByRole('button', { name: 'Retry next crest' }), viewport, 'advance retry target')
  if (retryBox.width < 44 || retryBox.height < 44) {
    throw new Error(`${viewportKey(viewport)}: Advance retry target is smaller than 44x44: ${JSON.stringify(retryBox)}`)
  }
} 

test.beforeAll(async () => {
  await mkdir(SCREENSHOT_DIR, { recursive: true })
})

for (const viewport of VIEWPORTS) {
  test(`touch gameplay remains complete and scroll-free at ${viewportKey(viewport)}`, async ({ browser }) => {
    const context = await browser.newContext({
      baseURL: BASE_URL,
      viewport,
      hasTouch: true,
      isMobile: viewport.width < 768,
      reducedMotion: 'no-preference',
    })
    const page = await context.newPage()

    try {
      const api = await installApiMock(page)
      await openSetup(page)
      await assertNoHorizontalOverflow(page, viewport, 'setup')

      const screenshotDevice = SCREENSHOT_VIEWPORTS.get(viewportKey(viewport))
      if (screenshotDevice) await screenshot(page, screenshotDevice, 'setup', true)

      await page.getByRole('button', { name: /Start quest/i }).tap()
      await expect(page.getByRole('heading', { name: 'Name that crest' })).toBeVisible()
      await assertActiveLayout(page, viewport, firstChoices.map((choice) => choice.name))
      if (screenshotDevice) await screenshot(page, screenshotDevice, 'active')

      await page.getByRole('button', { name: 'Arsenal', exact: true }).tap()
      await expect(page.getByText('Arsenal is not correct. Try again.', { exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Arsenal', exact: true })).toHaveClass(/answer-button--wrong/)
      await expect(page.getByRole('button', { name: 'Arsenal', exact: true })).toHaveCount(0)
      await expect(page.getByRole('button', { name: 'Barcelona', exact: true })).toBeFocused()
      await assertActiveLayout(page, viewport, ['Barcelona', 'Celtic', 'Dortmund'])
      if (screenshotDevice) await screenshot(page, screenshotDevice, 'wrong-answer')

      await page.getByRole('button', { name: 'Barcelona', exact: true }).tap()
      await expect.poll(() => api.counters.advanceRequests).toBe(1)
      await assertCorrectFeedbackLayout(page, viewport)

      if (screenshotDevice) {
        await screenshot(page, screenshotDevice, 'correct-feedback')

        api.allowAdvance({ expireSoon: true })
        await page.getByRole('button', { name: 'Retry next crest' }).tap()
        await expect(page.getByRole('button', { name: 'Ajax', exact: true })).toBeVisible()
        await expect(page.getByRole('heading', { name: 'Quest complete.' }), 'timer expiry should route to the result').toBeVisible({ timeout: 5_000 })
        await assertNoHorizontalOverflow(page, viewport, 'result')
        expect(api.counters.expireRequests).toBe(1)
        await screenshot(page, screenshotDevice, 'result', true)

        await page.getByRole('button', { name: 'View leaderboard', exact: true }).tap()
        await expect(page.getByRole('heading', { name: 'Leaderboard' })).toBeVisible()
        await expect(page.getByRole('rowheader', { name: /Playwright.*you/ })).toBeVisible()
        await assertNoHorizontalOverflow(page, viewport, 'leaderboard')
        await screenshot(page, screenshotDevice, 'leaderboard', true)
      }
    } finally {
      await context.close()
    }
  })
}

test('first round resets setup scroll before locking the game viewport', async ({ page }) => {
  const viewport = { width: 390, height: 844 } as const
  await page.setViewportSize(viewport)
  await installApiMock(page)
  await openSetup(page)

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  await page.getByRole('button', { name: /Start quest/i }).tap()

  await expect(page.getByRole('heading', { name: 'Name that crest' })).toBeVisible()
  await expect(page.locator('.app-shell--game')).toHaveCSS('overflow-x', 'clip')
  await assertActiveLayout(page, viewport, firstChoices.map((choice) => choice.name))
})

test('viewport rotation preserves the current active question and removed answer', async ({ page }) => {
  const portrait = { width: 768, height: 1024 } as const
  const landscape = { width: 1024, height: 768 } as const
  await page.setViewportSize(portrait)
  const api = await installApiMock(page)
  await openSetup(page)
  await page.getByRole('button', { name: /Start quest/i }).tap()
  await page.getByRole('button', { name: 'Arsenal', exact: true }).tap()
  await expect(page.getByRole('button', { name: 'Arsenal', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Barcelona', exact: true })).toBeFocused()
  await expect(page.getByText('75 points available', { exact: true })).toBeVisible()

  const timerBefore = Number(await page.getByRole('progressbar', { name: 'Round time remaining' }).getAttribute('aria-valuenow'))
  const stateRequestsBeforeRotation = api.counters.stateRequests
  await page.setViewportSize(landscape)

  await expect(page.getByRole('heading', { name: 'Name that crest' })).toBeVisible()
  await expect(page.getByRole('img', { name: 'Mystery football club crest' })).toHaveAttribute('src', '/api/questions/question-one/crest')
  await expect(page.getByText('75 points available', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Arsenal', exact: true })).toHaveCount(0)
  await assertActiveLayout(page, landscape, ['Barcelona', 'Celtic', 'Dortmund'])

  const timerAfter = Number(await page.getByRole('progressbar', { name: 'Round time remaining' }).getAttribute('aria-valuenow'))
  expect(timerAfter).toBeLessThanOrEqual(timerBefore)
  expect(timerAfter).toBeGreaterThan(0)
  expect(api.counters.stateRequests).toBe(stateRequestsBeforeRotation)
  expect(api.counters.startRequests).toBe(1)
  expect(api.counters.guessRequests).toBe(1)
})

test('duplicate touch taps while a guess is pending submit only one request', async ({ page }) => {
  const api = await installApiMock(page, { holdGuess: true })
  await openSetup(page)
  await page.getByRole('button', { name: /Start quest/i }).tap()

  const answer = page.getByRole('button', { name: 'Arsenal', exact: true })
  await answer.tap()
  await expect.poll(() => api.counters.guessRequests).toBe(1)
  await expect(answer).toBeDisabled()
  await answer.tap({ force: true })
  await expect.poll(() => api.counters.guessRequests).toBe(1)

  api.releaseGuess()
  await expect(page.getByText('Arsenal is not correct. Try again.', { exact: true })).toBeVisible()
  expect(api.counters.guessRequests).toBe(1)
})
