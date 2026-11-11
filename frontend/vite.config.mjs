import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { normalizeBasePath } from "./src/basePathUtils.js";

const localApiPort = process.env.AGENTSWATCH_LOCAL_API_PORT || "8000";
const frontendBase = normalizeBasePath(process.env.AGENTSWATCH_FRONTEND_BASE || "/");

export default defineConfig({
  base: frontendBase,
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": `http://127.0.0.1:${localApiPort}`,
      "/healthz": `http://127.0.0.1:${localApiPort}`,
    },
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
