import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

function normalizeBaseUrl(rawValue: string | undefined): string {
	if (rawValue === undefined) {
		return "/";
	}
	const normalized = rawValue.trim().replace(/^\/+|\/+$/g, "");
	if (!normalized) {
		return "/";
	}
	return `/${normalized}/`;
}

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, ".", "");

	return {
		plugins: [react()],
		base: normalizeBaseUrl(env.QUANT_ARENA_BASE_URL || env.VITE_BASE_URL),
		build: {
			outDir: "../static",
			emptyOutDir: true,
		},
	};
});
