-- Thumbs up / down on an assistant reply: 1, -1, or NULL for no rating.
--
-- Kept on the message itself: it is one value per reply, read back with the
-- transcript so the buttons show what the customer chose, and queried for
-- quality review (which replies were rated down, and why the agent said it).
--
-- Idempotent: safe to re-run. Railway re-runs migrations on boot.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS feedback SMALLINT;
