import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const require = createRequire(import.meta.url);
// Use the client installed with the React SDK, so processors stay version-matched.
const clientEntry = require.resolve("@elevenlabs/client", {
  paths: [dirname(require.resolve("@elevenlabs/react"))],
});
const workletDir = resolve(dirname(clientEntry), "../worklets");

// Builds the unpacked extension into dist/. public/manifest.json is copied as-is.
// background.js and content.js get fixed names so the manifest can point at them.
// content.ts must stay import-free: content scripts can't load shared chunks.
export default defineConfig({
  plugins: [
    react(),
    {
      name: "package-elevenlabs-worklets",
      generateBundle() {
        for (const name of ["rawAudioProcessor", "audioConcatProcessor"]) {
          this.emitFile({
            type: "asset",
            fileName: `worklets/${name}.js`,
            source: readFileSync(resolve(workletDir, `${name}.js`)),
          });
        }
      },
    },
  ],
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
