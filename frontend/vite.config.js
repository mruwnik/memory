import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
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
      '/tasks': 'http://localhost:8000',
      "/photos": "http://localhost:8000",
      "/books": "http://localhost:8000",
      "/forum-posts": "http://localhost:8000",
      "/jobs": "http://localhost:8000",
      "/polls": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
})
