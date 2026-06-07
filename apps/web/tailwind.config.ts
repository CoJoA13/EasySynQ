import type { Config } from "tailwindcss";

// Single token source of truth = the CSS custom properties in src/theme/tokens.css.
// Tailwind references those vars; Mantine's theme reads the same vars. Never two palettes.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--es-bg)",
        surface: "var(--es-surface)",
        accent: "var(--es-accent)",
        "text-primary": "var(--es-text)",
        "text-secondary": "var(--es-text-2)",
        border: "var(--es-border)",
      },
      fontFamily: {
        sans: "var(--es-font-sans)",
        mono: "var(--es-font-mono)",
      },
    },
  },
  plugins: [],
} satisfies Config;
