-- One-shot migration for SQLite databases created before the
-- /{build}/{category} URLs and plain category names.
--
-- Run manually once against the existing database BEFORE starting the
-- new code, e.g. from the repo root:
--   sqlite3 data/portal2_runs.db < migration.sql
-- Fresh databases need nothing: create_all already uses the new schema.

-- 1. Categories gained a legacy_slug column holding the old
--    /category/{slug} value (empty means {build_slug}_{slug}).
ALTER TABLE categories ADD COLUMN legacy_slug VARCHAR(80) DEFAULT '';

-- 2. Category.name is now the plain category name, previously stored in
--    the short_name column. Backfill custom rows before dropping it.
--    Seeded categories are overwritten by seed_categories on startup anyway.
UPDATE categories SET name = short_name
WHERE short_name IS NOT NULL AND short_name != '' AND name != short_name;

-- 3. short_name is superseded by name.
ALTER TABLE categories DROP COLUMN short_name;

-- NOTE: name used to be globally UNIQUE. Old databases keep that constraint
-- (SQLite cannot drop autoindex constraints without a table rebuild), so
-- reusing the same category name across builds requires a fresh database.

-- 4. Runs gained an optional splits_url (link to splits).
ALTER TABLE runs ADD COLUMN splits_url VARCHAR(500) DEFAULT '';
