-- Runs once, when the db volume is first created (docker compose up on a fresh machine).
-- The backend test suite uses this database; it is dropped and rebuilt on every run.
CREATE DATABASE carekin_test OWNER carekin;
