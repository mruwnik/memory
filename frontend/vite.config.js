/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/ui/',
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
      // Everything outside the app's own /ui/ base is the API
      '^/(?!ui(/|$))': 'http://localhost:8000',
    },
  },
})
