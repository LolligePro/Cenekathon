CREATE SCHEMA skillmarket_staging;

CREATE TABLE skillmarket_ingest.import_runs (
    run_id UUID PRIMARY KEY,
    operation VARCHAR NOT NULL CHECK (operation IN ('load_skills', 'receive_batch')),
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

-- A validation registry, not the future normalized skills/application table.
CREATE TABLE skillmarket_ingest.catalogs (
    catalog_version VARCHAR PRIMARY KEY CHECK (length(trim(catalog_version)) > 0),
    content_sha256 VARCHAR NOT NULL CHECK (length(content_sha256) = 64),
    item_count BIGINT NOT NULL CHECK (item_count > 0),
    source_run_id UUID NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
CREATE TABLE skillmarket_ingest.catalog_items (
    catalog_version VARCHAR NOT NULL REFERENCES skillmarket_ingest.catalogs (catalog_version),
    item_id VARCHAR NOT NULL CHECK (length(trim(item_id)) > 0),
    payload JSON NOT NULL CHECK (json_type(payload) = 'OBJECT'),
    PRIMARY KEY (catalog_version, item_id)
);

CREATE TABLE skillmarket_ingest.batches (
    batch_key UUID PRIMARY KEY,
    batch_id VARCHAR NOT NULL UNIQUE CHECK (length(trim(batch_id)) > 0),
    source_run_id UUID NOT NULL UNIQUE,
    contract_version VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    catalog_version VARCHAR NOT NULL REFERENCES skillmarket_ingest.catalogs (catalog_version),
    manifest JSON NOT NULL CHECK (json_type(manifest) = 'OBJECT'),
    manifest_sha256 VARCHAR NOT NULL CHECK (length(manifest_sha256) = 64),
    records_sha256 VARCHAR NOT NULL CHECK (length(records_sha256) = 64),
    record_count BIGINT NOT NULL CHECK (record_count > 0),
    ok_records BIGINT NOT NULL CHECK (ok_records >= 0),
    failed_records BIGINT NOT NULL CHECK (failed_records >= 0),
    source_ids_verified BOOLEAN NOT NULL DEFAULT false,
    staged_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    CHECK (ok_records + failed_records = record_count)
);

-- Only transport metadata is typed. All parser fields remain inside payload.
CREATE TABLE skillmarket_staging.records (
    -- DuckDB does not support foreign keys across schemas. The receiver inserts
    -- batch metadata and its records together in one transaction.
    batch_key UUID NOT NULL,
    line_number BIGINT NOT NULL CHECK (line_number > 0),
    source_record_id VARCHAR NOT NULL CHECK (length(trim(source_record_id)) > 0),
    payload JSON NOT NULL CHECK (json_type(payload) = 'OBJECT'),
    PRIMARY KEY (batch_key, line_number),
    UNIQUE (batch_key, source_record_id)
);
