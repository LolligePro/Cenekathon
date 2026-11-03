CREATE TABLE skillmarket_staging.company_names (
    company_raw VARCHAR PRIMARY KEY,
    company_normalized VARCHAR,
    company_id VARCHAR,
    normalization_method VARCHAR NOT NULL,
    normalizer_version VARCHAR NOT NULL
);
