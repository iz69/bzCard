import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Production paths are supplied by the container at startup.
  base: command === 'build' ? './' : process.env.VITE_BASE_PATH || '/bzcard/',
}));
