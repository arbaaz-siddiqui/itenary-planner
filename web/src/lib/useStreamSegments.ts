"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  appendThinking,
  dropTrailingThinking,
  isWritingText,
  QUIET_GAP_MS,
  reduceSegments,
  segmentsToText,
  segmentsToToolCalls,
} from "./segments";
import type { Segment, StreamEvent } from "./types";

/**
 * Owns the live segment list for one streaming turn, plus the one piece of
 * wall-clock behaviour the reducer deliberately does not have: the idle timer
 * that decides when to admit we are waiting.
 *
 * The timer is a SINGLE timer, re-armed on EVERY incoming event. That is what
 * keeps "Thinking…" from appearing mid-sentence: while tokens flow every ~10ms
 * the timeout never gets to fire, and it only lands during a genuine gap — the
 * 8-40s a tour search spends in the supplier API before the model says anything.
 */
export function useStreamSegments() {
  const [segments, setSegments] = useState<Segment[]>([]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Read inside the timeout so the callback never closes over a stale list.
  const segmentsRef = useRef<Segment[]>([]);

  const write = useCallback((next: Segment[] | ((prev: Segment[]) => Segment[])) => {
    setSegments((prev) => {
      const value = typeof next === "function" ? next(prev) : next;
      segmentsRef.current = value;
      return value;
    });
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const armTimer = useCallback(() => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      // Text mid-flight means the model is not idle — the gap is just network
      // jitter between chunks, and a placeholder would be a lie.
      if (isWritingText(segmentsRef.current)) return;
      write((prev) => appendThinking(prev));
    }, QUIET_GAP_MS);
  }, [clearTimer, write]);

  /** Start a turn: empty list, and arm the timer so a slow first token shows. */
  const begin = useCallback(() => {
    write([]);
    armTimer();
  }, [armTimer, write]);

  /** Fold one event, then re-arm. Every event counts as activity. */
  const push = useCallback(
    (event: StreamEvent) => {
      write((prev) => reduceSegments(prev, event));
      if (event.type === "done" || event.type === "error") clearTimer();
      else armTimer();
    },
    [armTimer, clearTimer, write],
  );

  /** End a turn: stop the timer and drop any placeholder still showing. */
  const settle = useCallback(() => {
    clearTimer();
    write((prev) => dropTrailingThinking(prev));
  }, [clearTimer, write]);

  /** Clear everything (turn persisted, or session switched). */
  const reset = useCallback(() => {
    clearTimer();
    write([]);
  }, [clearTimer, write]);

  // Both read the REF, not the state, so they are correct when called from
  // inside the async stream loop where `segments` would still be the value
  // captured at render time.

  /** The prose to persist as the message's `content`. */
  const text = useCallback(() => segmentsToText(segmentsRef.current), []);

  /** The tool calls to persist as the message's `tool_calls`. */
  const toolCalls = useCallback(() => segmentsToToolCalls(segmentsRef.current), []);

  // A timer must never outlive the component that armed it.
  useEffect(() => clearTimer, [clearTimer]);

  return { segments, begin, push, settle, reset, text, toolCalls };
}
