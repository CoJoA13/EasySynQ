import { createTheme } from "@mantine/core";

// The Mantine theme reads the same CSS variables as Tailwind (tokens.css).
export const theme = createTheme({
  fontFamily: "Inter, system-ui, -apple-system, sans-serif",
  primaryColor: "teal",
  defaultRadius: "md",
});
