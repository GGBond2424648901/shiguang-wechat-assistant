import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { readFileSync } from "node:fs";

export default defineConfig({ base: "./", plugins: [react(), {
  name: "desktop-build-version",
  generateBundle() {
    const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));
    this.emitFile({ type: "asset", fileName: "build-version.txt", source: version + "\n" });
  },
}] });
