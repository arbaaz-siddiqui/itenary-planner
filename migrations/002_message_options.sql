-- Option cards (tours, hotels, flights) rendered under an assistant message.
--
-- These were streamed to the browser but never stored, so every card -- and
-- every tour image with it -- vanished on reload while the message text
-- survived. The customer saw a reply referring to pictures that were no longer
-- there.
--
-- JSONB and not a table of its own: the card payload is the tool result
-- verbatim, its shape is still moving, and it is only ever read back whole for
-- one message.
ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS options JSONB NOT NULL DEFAULT '[]'::jsonb;
