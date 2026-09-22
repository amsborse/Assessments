import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Same-origin in dev, so the app needs no CORS config and no API base URL.
  server: { proxy: { "/items": "http://127.0.0.1:8000" } },
});
