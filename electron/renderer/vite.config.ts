import path from "node:path";
import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: __dirname,
  base: "./",
  plugins: [vue()],
  build: {
    outDir: path.resolve(__dirname, "../../dist/electron/renderer"),
    emptyOutDir: false,
  },
});
