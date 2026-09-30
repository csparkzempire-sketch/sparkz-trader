// Chart colors resolved from the CSS theme variables (src/index.css), so Recharts SVG
// attributes get concrete colors that follow the light/dark palette.
const read = (name: string) =>
  typeof window === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export const themeColor = (name: string, alpha = 1) => {
  const rgb = read(`--${name}`);
  return rgb ? `rgb(${rgb} / ${alpha})` : "currentColor";
};

export const chart = () => ({
  grid: themeColor("c-line"),
  axis: themeColor("c-muted"),
  text: themeColor("c-ink"),
  panel: themeColor("c-panel"),
  up: themeColor("c-up"),
  down: themeColor("c-down"),
  line: themeColor("c-ink"),
  series1: themeColor("chart-series-1"),
  series2: themeColor("chart-series-2"),
  series3: themeColor("chart-series-3"),
  series4: themeColor("chart-series-4"),
  tooltip: {
    background: themeColor("c-panel"),
    border: `1px solid ${themeColor("c-line")}`,
    fontSize: 12,
    fontFamily: '"Source Serif 4", Georgia, serif',
    color: themeColor("c-ink"),
  },
});
