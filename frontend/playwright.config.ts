import { defineConfig } from '@playwright/test'

const PORT = 4174
const BASE_URL = `http://127.0.0.1:${PORT}`

export default defineConfig({
  testDir: './e2e',
  testMatch: 'responsive.spec.ts',
  timeout: 120_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: BASE_URL,
    hasTouch: true,
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'webkit-touch',
      use: {
        browserName: 'webkit',
        viewport: { width: 1024, height: 768 },
      },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
