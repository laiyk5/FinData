import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { version } from "./package.json";

export default defineConfig({
  plugins: [react()],
  base: "./",
  define: {
    __APP_VERSION__: JSON.stringify(version),
  },
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "../src/findata/webui",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
  },
});
