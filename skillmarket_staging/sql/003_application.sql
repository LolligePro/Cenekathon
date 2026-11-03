CREATE SCHEMA skillmarket_app;

-- The seven original columns remain accessible through raw_vacancies, a view
-- over the supplied Parquet files. Compact metadata lives here for fast joins.
CREATE TABLE skillmarket_app.postings (
    posting_id VARCHAR PRIMARY KEY,
    title VARCHAR,
    company_raw VARCHAR,
    company_normalized VARCHAR,
    company_id VARCHAR,
    date_posted DATE,
    country VARCHAR,
    city VARCHAR,
    normalized_title VARCHAR,
    role_family VARCHAR,
    job_type VARCHAR,
    parse_status VARCHAR NOT NULL DEFAULT 'pending'
        CHECK (parse_status IN ('pending', 'ok', 'failed')),
    parser_version VARCHAR,
    taxonomy_version VARCHAR
);

CREATE TABLE skillmarket_app.skills (
    skill_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    category VARCHAR,
    synonyms JSON NOT NULL DEFAULT '[]'
);
CREATE TABLE skillmarket_app.posting_skills (
    posting_id VARCHAR NOT NULL,
    skill_id VARCHAR NOT NULL,
    PRIMARY KEY (posting_id, skill_id)
);
CREATE TABLE skillmarket_app.parser_imports (
    import_id UUID PRIMARY KEY,
    format VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    files JSON NOT NULL,
    row_count BIGINT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE skillmarket_app.monthly_totals (
    job_type VARCHAR NOT NULL,
    month DATE NOT NULL,
    total_postings BIGINT NOT NULL,
    PRIMARY KEY (job_type, month)
);
CREATE TABLE skillmarket_app.skill_demand (
    job_type VARCHAR NOT NULL,
    month DATE NOT NULL,
    skill VARCHAR NOT NULL,
    postings BIGINT NOT NULL,
    PRIMARY KEY (job_type, month, skill)
);
