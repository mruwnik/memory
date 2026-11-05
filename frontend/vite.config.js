/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Pages the api renders itself, which a browser opens directly.
const API_PAGES = /^\/(authorize|oauth|auth|docs|redoc|files|reports|google-drive|slack|discord|\.well-known)(\/|\?|$)/
const isPageNavigation = req => req.method === 'GET' && req.headers.accept?.includes('text/html') && !API_PAGES.test(req.url)

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    // Tests assert wall-clock labels for UTC slot times
    env: { TZ: 'UTC' },
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    clearMocks: true,
    restoreMocks: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reportsDirectory: './coverage',
      reporter: ['text-summary', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.{test,spec}.{ts,tsx}',
        'src/**/*.testhelper.{ts,tsx}',
        'src/test/**',
        'src/**/index.{ts,js}',
        'src/types/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
    },
  },
  server: {
    proxy: {
      // Vite serves its own modules and the app's pages; the api answers everything else.
      '^/(?!@|src/|node_modules/|favicon\\.ico|mcp-client\\.js)': {
        target: 'http://localhost:8000',
        bypass: req => (isPageNavigation(req) ? req.url : undefined),
      },
    },
  },
})
