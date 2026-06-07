import { createTheme } from "@mantine/core";

// The Mantine theme reads the SAME CSS variables as Tailwind (src/theme/tokens.css) — one token
// source, never two palettes. Fonts use the mockup's system stack (no web fonts; air-gap-safe).
export const theme = createTheme({
  fontFamily: "var(--es-font-sans)",
  fontFamilyMonospace: "var(--es-font-mono)",
  primaryColor: "indigo", // closest built-in to the mockup accent #4f5bd5; exact accent via tokens
  defaultRadius: "md",
});
