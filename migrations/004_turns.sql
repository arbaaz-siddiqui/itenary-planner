-- The turn journal: what makes an interrupted answer recoverable.
--
-- Today a turn is persisted only when it FINISHES. `api/main.py::_stream_turn`
-- writes the customer's message up front, streams the reply over SSE, then
-- calls add_message once the stream ends. So closing the tab, refreshing,
-- losing signal or restarting the server during an answer loses that answer
-- permanently -- and because the supplier calls already ran, it also wasted
-- them.
--
-- This table records the turn AS IT HAPPENS: status, the text written so far
-- (debounced), and a monotonic sequence the client can resume from. Reopening a
-- conversation mid-answer reattaches to the live turn instead of restarting it.
--
-- Deliberately separate from chat_messages. chat_messages is the finished
-- transcript -- what the customer is entitled to read back. A turn is the
-- machinery that produces one row of it, and it is allowed to fail, retry and
-- be swept up on boot without leaving debris in the transcript.
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS turns (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,

    -- streaming  -> in flight right now, a client may attach
    -- complete   -> reply written to chat_messages; this row is history
    -- failed     -> the worker died or the model errored; UI offers Retry
    -- cancelled  -> the customer stopped it
    status       TEXT        NOT NULL DEFAULT 'streaming'
                             CHECK (status IN ('streaming', 'complete', 'failed', 'cancelled')),

    user_text    TEXT        NOT NULL,

    -- What the model has written so far. Updated on a debounce (~500ms) rather
    -- than per token: per-token writes would be thousands of UPDATEs per turn
    -- for no visible gain, since a reconnecting client replays from `seq` and
    -- then follows the live stream.
    partial_text TEXT        NOT NULL DEFAULT '',

    -- Monotonic counter of stream events emitted for this turn. The client
    -- sends it back as SSE Last-Event-ID so a reconnect resumes rather than
    -- replaying the whole answer.
    seq          BIGINT      NOT NULL DEFAULT 0,

    -- Tool activity so far, same shape as chat_messages.tool_calls, so the
    -- progress chips survive a reload mid-answer.
    tool_calls   JSONB       NOT NULL DEFAULT '[]'::jsonb,

    -- Result envelopes produced so far, so cards that already arrived are still
    -- there after a refresh even though the prose is unfinished.
    cards        JSONB       NOT NULL DEFAULT '[]'::jsonb,

    error        TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);

-- "Is anything still running in this session?" on every transcript load, and
-- "sweep the turns orphaned by the last restart" on boot. Partial, because only
-- streaming rows are ever queried this way and they are a tiny minority.
CREATE INDEX IF NOT EXISTS turns_live_idx
    ON turns (session_id, started_at DESC)
    WHERE status = 'streaming';

-- The ordinary "show me this session's turns" query.
CREATE INDEX IF NOT EXISTS turns_session_started_idx
    ON turns (session_id, started_at);
