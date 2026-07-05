import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
	plugins: [react()],
	// Relative asset URLs; they resolve against the <base href> tag in
	// index.html, which the backend rewrites to its URL prefix at serve time.
	// One build therefore works at any mount path.
	base: "./",
	build: {
		outDir: "../static",
		emptyOutDir: true,
	},
});
