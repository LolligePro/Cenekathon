-- Extend the small attempt log's operation check without rewriting vacancy data.
-- DuckDB cannot alter an existing CHECK constraint in place.
CREATE TABLE skillmarket_ingest.import_runs_with_courses (
    run_id UUID PRIMARY KEY,
    operation VARCHAR NOT NULL CHECK (operation IN ('load_skills', 'receive_batch', 'import_courses')),
    status VARCHAR NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'staged', 'loaded', 'duplicate', 'failed')),
    batch_id VARCHAR,
    source_name VARCHAR NOT NULL,
    manifest JSON,
    manifest_sha256 VARCHAR,
    records_sha256 VARCHAR,
    expected_records BIGINT,
    received_records BIGINT NOT NULL DEFAULT 0,
    source_ids_verified BOOLEAN NOT NULL DEFAULT false,
    duplicate_of UUID,
    error_code VARCHAR,
    error_message VARCHAR,
    started_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    finished_at TIMESTAMPTZ,
    CHECK ((status = 'started' AND finished_at IS NULL)
        OR (status <> 'started' AND finished_at IS NOT NULL))
);
INSERT INTO skillmarket_ingest.import_runs_with_courses SELECT * FROM skillmarket_ingest.import_runs;
DROP TABLE skillmarket_ingest.import_runs;
ALTER TABLE skillmarket_ingest.import_runs_with_courses RENAME TO import_runs;

-- Courses share the skill catalog, but never enter vacancy counts or trends.
CREATE TABLE skillmarket_app.courses (
    course_id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    site VARCHAR NOT NULL,
    short_intro VARCHAR,
    listed_skills VARCHAR[] NOT NULL,
    skill_source VARCHAR CHECK (skill_source IN ('listed', 'text')),
    normalized_title VARCHAR,
    parse_status VARCHAR NOT NULL CHECK (parse_status IN ('ok', 'failed')),
    parser_version VARCHAR NOT NULL,
    taxonomy_version VARCHAR NOT NULL,
    batch_id VARCHAR NOT NULL,
    metadata JSON NOT NULL,
    parser_payload JSON NOT NULL
);

CREATE TABLE skillmarket_app.course_skills (
    course_id VARCHAR NOT NULL,
    skill_id VARCHAR NOT NULL,
    PRIMARY KEY (course_id, skill_id)
);

CREATE TABLE skillmarket_app.course_imports (
    batch_id VARCHAR PRIMARY KEY,
    run_id UUID NOT NULL,
    manifest JSON NOT NULL,
    manifest_sha256 VARCHAR NOT NULL,
    metadata_sha256 VARCHAR NOT NULL,
    records_sha256 VARCHAR NOT NULL,
    course_count BIGINT NOT NULL,
    skill_link_count BIGINT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
