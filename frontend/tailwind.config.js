/** @type {import('tailwindcss').Config} */

// Colours and shadows are driven by CSS custom properties defined in
// `src/index.css`. The `:root` block sets dark-theme defaults; the `.light`
// class on `<html>` overrides them. Triplets (e.g. `--c-bg: 10 10 10`) are
// used so Tailwind's `<alpha-value>` placeholder keeps working — utilities
// like `border-line/40` continue to compose alpha correctly.
const cssVar = (name) => `rgb(var(${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Black & white neumorphism palette — themed via CSS vars.
        bg: cssVar("--c-bg"),
        surface: cssVar("--c-surface"),
        surface2: cssVar("--c-surface2"),
        rim: cssVar("--c-rim"),
        line: cssVar("--c-line"),
        ink: cssVar("--c-ink"),
        ink2: cssVar("--c-ink2"),
        muted: cssVar("--c-muted"),
        accent: cssVar("--c-accent"),
        danger: cssVar("--c-danger"),
        success: cssVar("--c-success"),
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      boxShadow: {
        // Neumorphism: paired light/dark shadows, themed via CSS vars so the
        // same primitives work cleanly on a near-black or near-white surface.
        neu: "8px 8px 18px var(--neu-shadow-dark), -6px -6px 14px var(--neu-shadow-light)",
        "neu-sm": "5px 5px 10px var(--neu-shadow-dark), -3px -3px 8px var(--neu-shadow-light)",
        "neu-pressed":
          "inset 6px 6px 14px var(--neu-shadow-dark), inset -4px -4px 10px var(--neu-shadow-light)",
        "neu-inset":
          "inset 4px 4px 10px var(--neu-shadow-dark), inset -2px -2px 6px var(--neu-shadow-light)",
      },
    },
  },
  plugins: [],
};
