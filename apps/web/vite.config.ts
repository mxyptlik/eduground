import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  envDir: "../..",
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "localhost",
    port: 5173,
    open: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./tests/module/setup.ts",
    include: ["tests/module/**/*.test.{ts,tsx}"],
    css: true,
    restoreMocks: true,
    clearMocks: true,
  },
});
