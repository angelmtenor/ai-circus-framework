import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { demoThemesPlugin } from './vite-plugins/demo-themes.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), demoThemesPlugin()],
})
