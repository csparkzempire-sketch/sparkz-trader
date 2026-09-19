/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        base: {
          bg: "#0a0e14",
          panel: "#0f1520",
          border: "#1c2532",
          text: "#c9d1d9",
          muted: "#6e7681",
        },
        accent: {
          up: "#26a969",
          down: "#e5484d",
          buy: "#26a969",
          sell: "#e5484d",
          hold: "#a1a1aa",
          brand: "#3b82f6",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
