// Models sometimes write math-mode symbols ("AMD $\to$ DXB"). There is no
// math renderer here, so they would show as raw TeX; swap the few that turn
// up for the characters they mean.
const TEX_SYMBOLS: Record<string, string> = {
  to: "→",
  rightarrow: "→",
  longrightarrow: "→",
  leftarrow: "←",
  leftrightarrow: "↔",
  times: "×",
  approx: "≈",
  geq: "≥",
  leq: "≤",
};
const TEX = /\$\s*\\(to|rightarrow|longrightarrow|leftarrow|leftrightarrow|times|approx|geq|leq)\s*\$/g;

export function plainSymbols(text: string): string {
  return text.replace(TEX, (_, name: string) => TEX_SYMBOLS[name]);
}
