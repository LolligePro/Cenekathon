-- Uniqueness is enforced by the importer: one row per posting and list_distinct
-- per skill list. Avoid a giant ART primary-key index on 36M analytical edges.
CREATE TABLE skillmarket_app.posting_skills_compact (
    posting_id VARCHAR NOT NULL,
    skill_id VARCHAR NOT NULL
);
INSERT INTO skillmarket_app.posting_skills_compact
SELECT DISTINCT posting_id,skill_id FROM skillmarket_app.posting_skills;
DROP TABLE skillmarket_app.posting_skills;
ALTER TABLE skillmarket_app.posting_skills_compact RENAME TO posting_skills;
