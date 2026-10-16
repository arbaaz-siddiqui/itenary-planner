-- Account data that used to live only in the browser.
--
-- Saved cards, pinned chats and UI settings were kept in localStorage, so they
-- belonged to the BROWSER, not the account: a second account signing in on the
-- same browser saw the first account's saved cards, and nothing followed a
-- customer to another device. They live here now, keyed by user.
--
-- Idempotent: safe to re-run. Migrations run on every chat-API boot.

-- --------------------------------------------------------- pinned chats
-- A property of the chat, which already belongs to exactly one user.
ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;

-- ---------------------------------------------------------- saved cards
-- The heart on a result card. `item` is the card's full option payload as the
-- customer saw it (name, price, photos), so a saved card still renders after
-- the chat it came from is deleted. One row per user per card.
CREATE TABLE IF NOT EXISTS saved_cards (
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    card_id    TEXT        NOT NULL,
    kind       TEXT        NOT NULL,
    item       JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, card_id)
);

-- The sidebar lists them newest first.
CREATE INDEX IF NOT EXISTS saved_cards_user_created_idx
    ON saved_cards (user_id, created_at DESC);

-- ----------------------------------------------------------- UI settings
-- {theme: "dark"|"light"|"system", sidebar_collapsed: bool}. Separate from
-- user_memory.facts, which the AGENT reads; these only shape the screen.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS ui_settings JSONB NOT NULL DEFAULT '{}'::jsonb;
