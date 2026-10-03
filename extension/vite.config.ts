import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds the unpacked extension into dist/. public/manifest.json is copied as-is.
// background.js and content.js get fixed names so the manifest can point at them.
// content.ts must stay import-free: content scripts can't load shared chunks.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: resolve(__dirname, "sidepanel.html"),
        permission: resolve(__dirname, "permission.html"),
        background: resolve(__dirname, "src/background.ts"),
        content: resolve(__dirname, "src/content.ts"),
      },
      output: {
        entryFileNames: (chunk) =>
          chunk.name === "background" || chunk.name === "content" ? "[name].js" : "assets/[name]-[hash].js",
      },
    },
  },
});
