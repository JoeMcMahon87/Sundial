import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// The dev-server proxy stands in for CloudFront's `/api/*` behaviour, so the
// session cookie is first-party in development too (§5.1). It is a different
// mechanism reaching the same place — close enough to build against, and not
// close enough to call M0 done (§15.1).
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Sundial',
        short_name: 'Sundial',
        description: 'Your day, planned and defended',
        theme_color: '#1c1917',
        background_color: '#1c1917',
        display: 'standalone',
        start_url: '/',
      },
      workbox: {
        // Offline is read-only in v1 by design; mutations are disabled, not
        // queued (§10.4). Nothing here may register a background sync queue.
        navigateFallbackDenylist: [/^\/api\//],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
})
