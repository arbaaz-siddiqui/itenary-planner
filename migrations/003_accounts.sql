-- Accounts: real names, and a lever for "sign out everywhere".
--
-- The product greets the customer by name ("Hi Arbaaz, where are we going?"),
-- and sign-up collects a first and last name. Both columns are NOT NULL with an
-- empty default so the migration is safe on the existing rows -- those accounts
-- were created before names were asked for, and the UI falls back to the local
-- part of the email until the customer fills them in.
--
-- Idempotent: safe to re-run. Railway re-runs migrations on boot.

ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name  TEXT NOT NULL DEFAULT '';

-- Bumped to invalidate every cookie this user holds, everywhere, at once.
--
-- Session cookies are signed and stateless, so there is otherwise no way to
-- revoke one before it expires: a laptop left signed in at an airport stays
-- signed in for the full 30 days. The session check compares the epoch in the
-- cookie against this column and rejects anything older, which turns "sign out
-- on all devices" and "force sign-out after a password change" into one UPDATE.
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_epoch INTEGER NOT NULL DEFAULT 0;
