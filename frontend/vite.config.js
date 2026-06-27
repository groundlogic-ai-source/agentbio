import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build straight into the directory FastAPI serves (api/static), so a plain
// `npm run build` produces output the backend already exposes — no copy step.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../api/static",
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` runs the Vite dev server on its own port; proxy API calls
    // to the running FastAPI backend so relative `/api/...` fetches work.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
