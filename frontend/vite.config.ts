import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import litestar from "litestar-vite-plugin";

import tailwindcss from "@tailwindcss/vite";

// Dev proxy target: the Litestar API started separately (`just dev-api`).
const API_TARGET = process.env.API_URL || "http://127.0.0.1:8000";

// Read by Node here, never inlined into the bundle (that would only happen with a
// VITE_ prefix). Mirrors what nginx injects in production, so the browser holds no
// secret in either environment.
const API_KEY = process.env.API_KEY ?? "";

export default defineConfig({
  // nginx serves the bundle at the root, not under Litestar's asset prefix, so use
  // the standard Vite base instead of the plugin's default.
  base: "/",
  // Assets copied verbatim (favicon, robots.txt). The plugin disables this by
  // default, which would silently drop anything dropped in there.
  publicDir: "public",
  build: {
    outDir: "dist",
    // index.html as the entry, so Vite emits complete HTML with hashed URLs. With
    // the plugin's entries (src/main.ts) the build only produces a manifest, leaving
    // the backend to rewrite the HTML — which it no longer does.
    rolldownOptions: { input: "index.html" },
  },
  server: {
    host: "0.0.0.0",
    port: Number(process.env.VITE_PORT || "5173"),
    // Same single origin as production, where nginx plays this role: client code
    // never knows the API's URL, it calls /api relatively.
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        headers: { "X-API-Key": API_KEY },
      },
      "/schema": { target: API_TARGET, changeOrigin: true },
    },
  },
  plugins: [
    tailwindcss(),

    svelte(),
    // Kept for type generation only; it no longer serves the frontend.
    litestar({
      input: ["src/main.ts", "src/tailwind.css"],

      types: "auto",
    }),
  ],
});
