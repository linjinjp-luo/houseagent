import { readFileSync } from "node:fs";
import { join } from "node:path";
import react from "@vitejs/plugin-react";
import type { ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";

// Dev only: the backend writes its port and per-launch session token to runtime.json.
// The proxy attaches the token so the browser never has to fetch it from an API.
function runtimeInfo(): { port: number; token: string } {
  const dataDir =
    process.env.HOUSEAGENT_DATA_DIR ?? join(process.env.LOCALAPPDATA ?? "", "HouseAgent");
  try {
    return JSON.parse(readFileSync(join(dataDir, "config", "runtime.json"), "utf-8"));
  } catch {
    return { port: 8765, token: "" };
  }
}

function backendProxy(): ProxyOptions {
  const info = runtimeInfo();
  return {
    target: `http://127.0.0.1:${info.port}`,
    changeOrigin: false,
    configure: (proxy) => {
      proxy.on("proxyReq", (req) => {
        const current = runtimeInfo();
        req.setHeader("X-HouseAgent-Token", current.token);
      });
    },
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": backendProxy(), "/mock-site": backendProxy() },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
  test: { environment: "node" },
});
