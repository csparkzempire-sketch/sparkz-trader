/** @type {import('tailwindcss').Config} */
// Colors come from CSS variables (src/index.css) so light and dark themes share one set of
// class names. Design: Figma "Sparkz Paper Desk — Classic redesign", Dashboard frame.
const v = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        base: {
          bg: v("bg"),
          panel: v("panel"),
          side: v("side"),
          tint: v("tint"),
          border: v("line"),
          text: v("ink"),
          muted: v("muted"),
        },
        accent: {
          up: v("up"),
          down: v("down"),
          buy: v("up"),
          sell: v("down"),
          hold: v("muted"),
          brand: v("brand"),
          brass: v("brass"),
          oxford: v("oxford"),
        },
      },
      fontFamily: {
        sans: ['"Source Sans 3"', "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        display: ['"Libre Caslon Text"', "Baskerville", "Georgia", "serif"],
        figures: ['"Source Serif 4"', "Georgia", "serif"],
        mono: ['"Source Serif 4"', "Georgia", "serif"],
      },
      borderRadius: {
        // Statement style: square, hairline-bordered panels. rounded-full stays round.
        DEFAULT: "2px",
        sm: "1px",
        md: "2px",
        lg: "2px",
        xl: "3px",
      },
    },
  },
  plugins: [],
};
