import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  // The contract lives versioned at the repo root: the frontend generates without
  // Python, and any API change shows up as a diff in review.
  input: "../openapi.json",
  output: "./src/generated/api",
  plugins: [
    "@hey-api/typescript",
    "@hey-api/schemas",
    "@hey-api/sdk",
    "@hey-api/client-fetch",
    "zod",
  ],
});
