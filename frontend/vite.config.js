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
      // Proxy API requests to backend during development
      '/register': 'http://localhost:8000',
      '/authorize': 'http://localhost:8000',
      '/token': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/email-accounts': 'http://localhost:8000',
      '/calendar-accounts': 'http://localhost:8000',
      '/article-feeds': 'http://localhost:8000',
      '/github': 'http://localhost:8000',
      '/google-drive': 'http://localhost:8000',
      "/books": "http://localhost:8000",
      "/photos": "http://localhost:8000",
      "/forum-posts": "http://localhost:8000",
      "/jobs": "http://localhost:8000",
      "/polls": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
})
