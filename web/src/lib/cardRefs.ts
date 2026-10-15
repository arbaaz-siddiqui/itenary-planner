import type { CardRef } from "./cardActions";
import { idOf, kindOf } from "./cards";
import type { OptionItem } from "./types";

/** Key under which a message tags an item with its group's kind. */
export const KIND_TAG = "__kind";

/**
 * Items to card refs: kind from the group (or the item's own tag), a stable
 * id, and no duplicates. Two rows naming the same result render once.
 */
export function toCardRefs(items: OptionItem[], groupKind?: string): CardRef[] {
  const seen = new Set<string>();
  const out: CardRef[] = [];
  for (const item of items) {
    const tag = typeof item[KIND_TAG] === "string" ? (item[KIND_TAG] as string) : undefined;
    const kind = kindOf(item, groupKind ?? tag);
    let id = idOf(item, kind);
    if (seen.has(id)) id = `${id}-${out.length}`;
    seen.add(id);
    out.push({ id, kind, item });
  }
  return out;
}
