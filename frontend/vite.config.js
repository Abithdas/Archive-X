import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';
// The backend serves this app at the site root (telegram_archive/web/routes_frontend.py).
// In dev, API and WebSocket calls are proxied to a running backend (HTTPS, self-signed certificate).
const backend = process.env.ARCHIVE_X_BACKEND ?? 'https://127.0.0.1:8443';
export default defineConfig({
    base: '/',
    plugins: [react(), tailwindcss()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
        port: 5173,
        proxy: {
            '/api': { target: backend, secure: false, changeOrigin: false },
            '/login': { target: backend, secure: false },
            '/ws': { target: backend, secure: false, ws: true },
        },
    },
    build: { outDir: 'dist', sourcemap: false, target: 'es2022' },
    test: {
        environment: 'jsdom',
        setupFiles: ['./src/test/setup.ts'],
        css: false,
        globals: true,
    },
});
