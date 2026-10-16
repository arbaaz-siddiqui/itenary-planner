// A table that is still streaming is not a table yet. Until its separator row
// (`| :--- | :--- |`) has fully arrived, markdown reads the header line as a
// paragraph, so the customer saw "| Airline | Departure | ... | :--- |" flash
// up as raw text before the result cards replaced it.

const SEPARATOR = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/;

/** Split streaming text into what can render now and whether a table is
 *  still being written at the end of it. */
export function holdPendingTable(text: string): { ready: string; pending: boolean } {
  const lines = text.split("\n");
  let start = lines.length;
  while (start > 0 && lines[start - 1].trimStart().startsWith("|")) start--;
  if (start === lines.length) return { ready: text, pending: false };

  const run = lines.slice(start);
  // The separator counts only once a newline follows it: the last line may
  // still be growing ("| :--- | :-" is not a whole separator).
  const complete = run.slice(1, -1).some((l) => SEPARATOR.test(l));
  if (complete) return { ready: text, pending: false };
  return { ready: lines.slice(0, start).join("\n").trimEnd(), pending: true };
}
