import { defineConfig } from "vite";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  base: "./",
  build: { outDir: "../yupyter/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8890",
      "/ws": { target: "ws://127.0.0.1:8890", ws: true },
    },
  },
});
