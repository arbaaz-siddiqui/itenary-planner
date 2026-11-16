-- Admin panel: one row per tool call the agent made (what each customer
-- searched), and a log of admins reading customers' chats.
--
-- Why a table and not a query over chat_messages.tool_calls: those payloads
-- reach 200KB+ per reply and are JSON strings inside JSON, so a dashboard
-- that counted searches by city or by day would read and parse every result
-- ever returned. search_log keeps only the small facts (tool, where, when,
-- who, how many results, the error) and is indexed for the admin views.
--
-- Rows are written by the chat API when a turn is saved (api/search_log.py
-- derives them from the same tool_calls list) and backfilled below from the
-- replies saved before this table existed.
--
-- Idempotent: safe to re-run. Railway re-runs migrations on boot. The
-- backfill skips every reply that already has rows, and (message_id,
-- call_index) is unique, so a re-run never duplicates a search.

CREATE TABLE IF NOT EXISTS search_log (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Deleting a chat deletes its searches, as it deletes its messages.
    session_id   UUID        REFERENCES chat_sessions(id) ON DELETE CASCADE,
    -- A regenerated reply is deleted; the searches behind it still happened.
    message_id   UUID        REFERENCES chat_messages(id) ON DELETE SET NULL,
    -- Position of the call in the reply's tool_calls list (0-based).
    call_index   INTEGER     NOT NULL DEFAULT 0,
    tool         TEXT        NOT NULL,
    args         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    origin       TEXT,
    destination  TEXT,
    start_date   TEXT,
    party        TEXT,
    result_count INTEGER,
    error        TEXT,
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS search_log_created_idx ON search_log (created_at);
CREATE INDEX IF NOT EXISTS search_log_user_created_idx ON search_log (user_id, created_at);
CREATE INDEX IF NOT EXISTS search_log_tool_idx ON search_log (tool);
CREATE UNIQUE INDEX IF NOT EXISTS search_log_message_call_key
    ON search_log (message_id, call_index);

-- Who read which customer's chat, and when. Written by the admin API.
CREATE TABLE IF NOT EXISTS admin_audit (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_email TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    target      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS admin_audit_created_idx ON admin_audit (created_at);

-- ------------------------------------------------------------ backfill
-- Tool outputs are stored as JSON text (a ToolMessage's content), sometimes
-- plain text ("Error: ..."). This parses what parses and returns NULL for the
-- rest instead of failing the whole migration on one odd row.
CREATE OR REPLACE FUNCTION search_log_try_jsonb(t TEXT) RETURNS JSONB AS $$
BEGIN
    RETURN t::jsonb;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- A JSON value as a whole number, or NULL ("2", 2 and 2.0 all give 2).
CREATE OR REPLACE FUNCTION search_log_int(v JSONB) RETURNS INTEGER AS $$
    SELECT CASE
        WHEN jsonb_typeof(v) = 'number' THEN floor((v #>> '{}')::numeric)::int
        WHEN jsonb_typeof(v) = 'string' AND (v #>> '{}') ~ '^\s*\d{1,6}\s*$'
            THEN trim(v #>> '{}')::int
        ELSE NULL
    END;
$$ LANGUAGE sql IMMUTABLE;

-- "2 adults, 1 child, 1 room": same wording as api/search_log.py party_of().
CREATE OR REPLACE FUNCTION search_log_party(a JSONB) RETURNS TEXT AS $$
    WITH n AS (
        SELECT
            COALESCE(
                search_log_int(a->'adults'),
                CASE WHEN jsonb_typeof(a->'rooms') = 'array' THEN (
                    SELECT NULLIF(SUM(search_log_int(r->'adults')), 0)::int
                    FROM jsonb_array_elements(a->'rooms') r
                    WHERE jsonb_typeof(r) = 'object'
                ) END
            ) AS adults,
            COALESCE(
                search_log_int(a->'children'),
                CASE WHEN jsonb_typeof(a->'child_ages') = 'array'
                     THEN jsonb_array_length(a->'child_ages') END
            ) AS children,
            search_log_int(a->'infants') AS infants,
            CASE
                WHEN jsonb_typeof(a->'rooms') = 'array' THEN jsonb_array_length(a->'rooms')
                ELSE search_log_int(a->'rooms')
            END AS rooms
    )
    SELECT NULLIF(concat_ws(', ',
        CASE WHEN adults > 0 THEN adults || CASE WHEN adults = 1 THEN ' adult' ELSE ' adults' END END,
        CASE WHEN children > 0 THEN children || CASE WHEN children = 1 THEN ' child' ELSE ' children' END END,
        CASE WHEN infants > 0 THEN infants || CASE WHEN infants = 1 THEN ' infant' ELSE ' infants' END END,
        CASE WHEN rooms > 0 THEN rooms || CASE WHEN rooms = 1 THEN ' room' ELSE ' rooms' END END
    ), '')
    FROM n;
$$ LANGUAGE sql IMMUTABLE;

INSERT INTO search_log (
    user_id, session_id, message_id, call_index, tool, args, origin, destination,
    start_date, party, result_count, error, created_at
)
SELECT
    s.user_id,
    m.session_id,
    m.id,
    (e.ord - 1)::int,
    left(COALESCE(NULLIF(e.v->>'tool_name', ''), NULLIF(e.v->>'name', ''), 'unknown'), 80),
    CASE WHEN length(p.a::text) > 8000
         THEN jsonb_build_object('_truncated', true)
         ELSE p.a END,
    left(NULLIF(trim(COALESCE(p.a->>'origin_city', p.a->>'origin', '')), ''), 120),
    left(NULLIF(trim(COALESCE(
        p.a->>'destination_city', p.a->>'destination_country', p.a->>'destination',
        p.a->>'city', '')), ''), 120),
    left(NULLIF(trim(COALESCE(
        p.a->>'check_in', p.a->>'travel_date', p.a->>'departure_date',
        p.a->>'arrival_date', p.a->>'search_date', p.a->>'start_date', p.a->>'date', '')), ''), 40),
    search_log_party(p.a),
    CASE
        WHEN jsonb_typeof(p.o->'options') = 'array' THEN jsonb_array_length(p.o->'options')
        WHEN search_log_int(p.o->'total_results') IS NOT NULL THEN search_log_int(p.o->'total_results')
        WHEN jsonb_typeof(p.o->'results') = 'array' THEN jsonb_array_length(p.o->'results')
        WHEN jsonb_typeof(p.o->'items') = 'array' THEN jsonb_array_length(p.o->'items')
        ELSE NULL
    END,
    CASE
        WHEN p.o IS NOT NULL AND p.o ? 'error'
             AND p.o->'error' NOT IN ('false'::jsonb, 'null'::jsonb, '""'::jsonb, '0'::jsonb)
            THEN left(COALESCE(
                NULLIF(p.o->>'message', ''),
                CASE WHEN jsonb_typeof(p.o->'error') = 'string' THEN p.o->>'error' END,
                'failed'), 300)
        WHEN p.o IS NULL AND jsonb_typeof(e.v->'output') = 'string'
             AND (e.v->>'output') ~* '^\s*error'
            THEN left(trim(e.v->>'output'), 300)
        ELSE NULL
    END,
    m.created_at
FROM chat_messages m
JOIN chat_sessions s ON s.id = m.session_id
CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(m.tool_calls) = 'array' THEN m.tool_calls ELSE '[]'::jsonb END
) WITH ORDINALITY AS e(v, ord)
CROSS JOIN LATERAL (
    SELECT
        CASE
            WHEN jsonb_typeof(e.v->'input') = 'object' THEN e.v->'input'
            WHEN jsonb_typeof(e.v->'input') = 'string'
                 AND jsonb_typeof(search_log_try_jsonb(e.v->>'input')) = 'object'
                THEN search_log_try_jsonb(e.v->>'input')
            ELSE '{}'::jsonb
        END AS a,
        CASE
            WHEN jsonb_typeof(e.v->'output') = 'object' THEN e.v->'output'
            WHEN jsonb_typeof(e.v->'output') = 'string'
                 AND jsonb_typeof(search_log_try_jsonb(e.v->>'output')) = 'object'
                THEN search_log_try_jsonb(e.v->>'output')
            ELSE NULL
        END AS o
) p
WHERE m.role = 'assistant'
  AND jsonb_typeof(e.v) = 'object'
  AND NOT EXISTS (SELECT 1 FROM search_log l WHERE l.message_id = m.id)
ON CONFLICT (message_id, call_index) DO NOTHING;
