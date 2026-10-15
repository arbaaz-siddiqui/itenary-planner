// Matching the names the agent types in its reply to the option rows
// behind them, so a card can show the item's photos and data.

/**
 * Key for matching a comparison-table row to its option card.
 *
 * The agent retypes the tour name into the table, so it can differ from the
 * option row by case, spacing, the recommended star, or a trailing marker.
 * Comparing on letters and digits alone is what makes the match hold.
 */
export function normaliseName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * Same item, allowing the agent's shortening: "Khandani Rajdhani" for
 * "Khandani Rajdhani - Indian Restaurant in Karama". Both are normalised.
 */
export function sameName(a: string, b: string): boolean {
  if (a === b) return true;
  if (a.length < 5 || b.length < 5) return false;
  return a.startsWith(b) || b.startsWith(a);
}

/** Words of a name, for matching a shortened one ("Khandani Rajdhani - Karama"). */
export function nameWords(name: string): string[] {
  return name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

/**
 * The agent's name is the supplier's with words dropped, in order:
 * "Khandani Rajdhani - Karama" for "Khandani Rajdhani - Indian Restaurant in
 * Karama". Needs two or more words so a single shared word never links two
 * different restaurants.
 */
export function shortenedFrom(short: string, full: string): boolean {
  const s = nameWords(short);
  const f = nameWords(full);
  if (s.length < 2 || s.length >= f.length) return false;
  let i = 0;
  for (const w of f) if (w === s[i]) i += 1;
  return i === s.length;
}

/** First-cell names of every markdown table row, as written. */
export function tableRowNames(markdown: string): string[] {
  const out: string[] = [];
  for (const line of markdown.split(/\r?\n/)) {
    const t = line.trim();
    if (!t.startsWith("|") || /^\|[\s:|-]+\|?$/.test(t)) continue;
    const cell = (t.split("|")[1] ?? "").trim();
    if (normaliseName(cell)) out.push(cell);
  }
  return out;
}

/** Does a name the agent typed refer to this item's full name? normaliseName
 *  drops markdown emphasis and the star marker with every other symbol. */
export function refersTo(typed: string, full: string): boolean {
  return sameName(normaliseName(typed), normaliseName(full)) || shortenedFrom(typed, full);
}

/**
 * Two table rows can name the same result: a visa listed once for Normal and
 * once for Express processing. Both matched the one option, so the Express row
 * became a second copy of the Normal card, showing the Normal price. Only the
 * first row keeps the option's card; a repeat becomes a text card that shows
 * its own row, price included.
 */
export function oneCardPerOption<R, I>(rows: { row: R; item: I | null }[]): { row: R; item: I | null }[] {
  const used = new Set<I>();
  return rows.map((m) => {
    if (m.item === null) return m;
    if (used.has(m.item)) return { row: m.row, item: null };
    used.add(m.item);
    return m;
  });
}
