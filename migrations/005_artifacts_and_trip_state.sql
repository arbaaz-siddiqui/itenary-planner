-- Artifacts and trip state: the two stores that keep data OUT of the model.
--
-- Idempotent: safe to re-run.

-- ------------------------------------------------------------- artifacts
-- The full parsed supplier result for one search.
--
-- A hotel or tour search payload runs to ~200KB. Putting that in the model's
-- context is what made turns slow and expensive, because LangGraph replays the
-- whole thread every turn -- so the same 200KB was re-sent on turn 2, 3, 4 and
-- on. The model instead sees five rows and a `ref`; everything else is here.
--
-- The same row serves the UI. "Show me the other 8 hotels" and opening a
-- detail drawer both read this table, so neither costs a supplier call and
-- neither can disagree with what the agent was told.
CREATE TABLE IF NOT EXISTS artifacts (
    ref        TEXT        PRIMARY KEY,
    thread_id  TEXT        NOT NULL,
    kind       TEXT        NOT NULL,

    -- The normalised search params. Kept so a repeat search can be recognised
    -- as the same question, and so a stale result can say what it was a result
    -- OF when we disclose its age.
    params     JSONB       NOT NULL,
    payload    JSONB       NOT NULL,

    -- When the SUPPLIER answered, not when we wrote the row. Freshness is
    -- measured from the supplier's answer; a cache hit re-served an hour later
    -- is an hour old, not new.
    as_of      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- "The most recent hotels result in this conversation" — how the agent answers
-- a follow-up without searching again.
CREATE INDEX IF NOT EXISTS artifacts_thread_kind_idx
    ON artifacts (thread_id, kind, as_of DESC);

-- Reaped by a periodic sweep. Without an index the sweep table-scans a table
-- whose rows are individually large.
CREATE INDEX IF NOT EXISTS artifacts_expiry_idx
    ON artifacts (expires_at);

-- ------------------------------------------------------------ trip state
-- What the customer has told us and chosen, for one conversation.
--
-- This exists because the model was being asked to be a database. Today
-- `apply_selection_tool` takes `current_spent` as an ARGUMENT: the model must
-- remember the running total across turns and hand it back correctly every
-- time. So must `compute_remaining_budget_tool`. That is why totals drift.
--
-- With this table the tools read and write server-side and take no totals. The
-- model cannot get the arithmetic wrong because it never does the arithmetic.
CREATE TABLE IF NOT EXISTS trip_state (
    thread_id      TEXT        PRIMARY KEY,

    -- {adults, children, child_ages}
    party          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- {origin, destination, start_date, end_date, nights}
    window         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    budget_inr     NUMERIC,

    -- [{component, id, title, price_inr, price_basis, priced_for_pax, as_of}]
    -- The running total is SUMMED from this, never stored, so it cannot drift
    -- out of step with the things it is the total of.
    selections     JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- {"tours": "ref_9f2", "hotels": "ref_3ab"} — the latest artifact per kind,
    -- so "show me those again" resolves without a supplier call.
    last_refs      JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- What we still need to ask. Rendered into the turn context so the agent
    -- picks the conversation back up instead of re-asking what it already knows.
    open_questions JSONB       NOT NULL DEFAULT '[]'::jsonb,

    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
