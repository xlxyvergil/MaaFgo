import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], base: './', build: {outDir: '../../assets/web/battle-editor', emptyOutDir: true}, server: {proxy: {'/api':{target:'http://127.0.0.1:18765',changeOrigin:true,headers:{Origin:'http://127.0.0.1:18765'}},'/media':{target:'http://127.0.0.1:18765',changeOrigin:true}}} });
