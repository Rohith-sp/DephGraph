import type { Config } from "tailwindcss";

const c = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        side: c("side"),
        bg: c("bg"),
        card: c("card"),
        raised: c("raised"),
        fg: c("fg"),
        fg2: c("fg2"),
        fg3: c("fg3"),
        accent: c("accent"),
        "on-accent": c("on-accent"),
        crit: c("crit"),
        high: c("high"),
        med: c("med"),
        low: c("low"),
        line: "rgb(var(--fg) / 0.08)",
        "line-strong": "rgb(var(--fg) / 0.16)",
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      transitionTimingFunction: { DEFAULT: "cubic-bezier(0.22, 1, 0.36, 1)" },
      transitionDuration: { DEFAULT: "150ms" },
    },
  },
  plugins: [],
};

export default config;
