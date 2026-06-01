import type { Config } from "tailwindcss";

// Single token source of truth = the CSS custom properties in src/theme/tokens.css.
// Tailwind references those vars; Mantine's theme reads the same vars. Never two palettes.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        accent: "var(--es-accent)",
        surface: "var(--es-bg-surface)",
        "state-effective": "var(--es-state-effective)",
        "state-draft": "var(--es-state-draft)",
      },
    },
  },
  plugins: [],
} satisfies Config;
