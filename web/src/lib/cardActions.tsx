"use client";

import { createContext, useContext } from "react";
import type { CardKind } from "./cards";
import type { OptionItem } from "./types";

/** A card as the detail view, the saved list and the agent hand-off see it. */
export interface CardRef {
  id: string;
  kind: CardKind;
  item: OptionItem;
}

export interface CardActions {
  /** Open the detail panel for a card. */
  open: (card: CardRef) => void;
  isSaved: (id: string) => boolean;
  toggleSave: (card: CardRef) => void;
  /** Book / enquire: hands the selection to the agent as the next message. */
  book: (card: CardRef) => void;
}

const noop: CardActions = {
  open: () => {},
  isSaved: () => false,
  toggleSave: () => {},
  book: () => {},
};

const Ctx = createContext<CardActions>(noop);

export const CardActionsProvider = Ctx.Provider;

/** Card interactions, provided once by the chat page. */
export function useCardActions(): CardActions {
  return useContext(Ctx);
}
