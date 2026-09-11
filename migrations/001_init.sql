-- Chat persistence for the Next.js surface.
--
-- Scope note: LangGraph owns its own checkpoint tables and creates them via
-- PostgresSaver.setup(). Those hold the agent's WORKING state (message objects,
-- tool calls mid-turn) and are an implementation detail we must not read or
-- write directly. The tables here are our own readable transcript, session list
-- and auth -- deliberately separate, so a LangGraph upgrade cannot break the
-- chat history and a schema change here cannot corrupt agent state.
--
-- Idempotent: safe to re-run. Railway deploys re-run migrations on boot.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS users (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Case-insensitive uniqueness: "Arbaaz@x.com" and "arbaaz@x.com" are one
-- account. A plain UNIQUE(email) would let both exist and split the history.
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key
    ON users (lower(email));

-- ------------------------------------------------------------- sessions
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT        NOT NULL DEFAULT 'New chat',
    -- The LangGraph thread_id for this session. Kept as TEXT because
    -- thread ids are opaque strings, not necessarily UUIDs.
    thread_id  TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One thread per session, enforced in the DB rather than trusted in code:
-- two sessions sharing a thread_id would silently merge two customers'
-- conversations into one agent context.
CREATE UNIQUE INDEX IF NOT EXISTS chat_sessions_thread_id_key
    ON chat_sessions (thread_id);

-- The sidebar query is "my sessions, newest activity first".
CREATE INDEX IF NOT EXISTS chat_sessions_user_updated_idx
    ON chat_sessions (user_id, updated_at DESC);

-- ------------------------------------------------------------- messages
CREATE TABLE IF NOT EXISTS chat_messages (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID        NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content    TEXT        NOT NULL DEFAULT '',
    -- Which API was called, with what input, and what came back. JSONB so the
    -- debug panel survives a page reload -- today that detail lives only in
    -- Streamlit's session_state and is lost on refresh. Payloads reach 200KB,
    -- so the UI must fetch this lazily rather than with every transcript load.
    tool_calls JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The transcript query is "this session, oldest first".
CREATE INDEX IF NOT EXISTS chat_messages_session_created_idx
    ON chat_messages (session_id, created_at);

-- ---------------------------------------------------------- user memory
-- Durable facts worth carrying BETWEEN sessions, so a returning customer is
-- not re-asked their departure city every time. Deliberately a narrow JSONB
-- blob rather than columns: the useful fields are still being discovered, and
-- a migration per field would slow that down.
--
-- Explicitly NOT a vector store. Semantic recall is a later step; adding it
-- before the basics are proven would be building on an untested foundation.
CREATE TABLE IF NOT EXISTS user_memory (
    user_id    UUID        PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    facts      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------- updated_at bump
-- Kept in the DB, not the app: every write path must move the sidebar's sort
-- order, and one forgotten UPDATE in application code makes a session look
-- stale forever.
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chat_sessions_touch ON chat_sessions;
CREATE TRIGGER chat_sessions_touch
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

DROP TRIGGER IF EXISTS user_memory_touch ON user_memory;
CREATE TRIGGER user_memory_touch
    BEFORE UPDATE ON user_memory
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- A new message is activity on its session, so the sidebar reorders without
-- the API having to remember a second write.
CREATE OR REPLACE FUNCTION touch_session_on_message() RETURNS TRIGGER AS $$
BEGIN
    UPDATE chat_sessions SET updated_at = now() WHERE id = NEW.session_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS chat_messages_touch_session ON chat_messages;
CREATE TRIGGER chat_messages_touch_session
    AFTER INSERT ON chat_messages
    FOR EACH ROW EXECUTE FUNCTION touch_session_on_message();
