-- Generated itinerary PDFs, kept in the database as well as on disk.
--
-- The PDF was written only to the chat API's local disk (ITINERARY_DIR). On
-- Railway that disk is replaced on every deploy and restart, so a PDF made
-- before the latest deploy answered its Download button with a 404. The
-- bytes are stored here when the turn that built them is saved, and served
-- from here when the file is gone. Owned through the chat session.
CREATE TABLE IF NOT EXISTS itinerary_pdfs (
    id          TEXT PRIMARY KEY,
    session_id  UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL DEFAULT 'itinerary.pdf',
    pdf         BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS itinerary_pdfs_session_idx ON itinerary_pdfs (session_id);
