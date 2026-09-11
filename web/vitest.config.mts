import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// The reducer under test is pure TS with no DOM, so the default node
// environment is enough — no jsdom, no React test renderer.
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
