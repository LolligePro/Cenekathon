"""Build the application database from original vacancies and finished parser output."""

from __future__ import annotations

import glob
import json
import tempfile
import time
import uuid
from importlib.resources import files
from pathlib import Path

import duckdb

from .companies import normalize_companies
from .contracts import ContractError, decode, validate_manifest
from .db import WRITER_LOCK, as_dicts, connect, initialize, transaction
from .ingest import load_skills, receive_batch
from .job_families import classification_sql


def _paths(patterns):
    paths = sorted({str(Path(p).resolve()) for pattern in patterns for p in glob.glob(str(pattern))})
    if not paths or any(not Path(p).is_file() for p in paths):
        raise ContractError("No matching input files found")
    return paths


def _ready(conn):
    if not conn.execute("""SELECT count(*) FROM information_schema.tables
        WHERE table_schema='skillmarket_app' AND table_name='postings'""").fetchone()[0]:
        raise ContractError("Run init to apply the application database migration")


def _refresh(conn):
    conn.execute(files("skillmarket_staging").joinpath("sql", "refresh_monthly.sql").read_text())


def _status(conn):
    row = conn.execute("""SELECT count(*) AS source_postings,
        count(*) FILTER(WHERE parse_status='pending') AS pending,
        count(*) FILTER(WHERE parse_status='ok') AS parsed,
        count(*) FILTER(WHERE parse_status='failed') AS failed,
        count(*) FILTER(WHERE date_posted IS NULL) AS missing_dates
        FROM skillmarket_app.postings""")
    result = as_dicts(row)[0]
    for name in ['skills', 'posting_skills', 'monthly_totals', 'skill_demand']:
        result[name] = conn.execute(f'SELECT count(*) FROM skillmarket_app.{name}').fetchone()[0]
    return result


def database_status(database):
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        return _status(conn)


def load_vacancies(database, sources):
    """Materialize compact original metadata; keep all seven raw fields in a view."""
    started = time.perf_counter()
    paths = _paths(sources)
    with tempfile.TemporaryDirectory(prefix='source-company-map-') as temp:
        normalize_companies(database, paths, output=Path(temp)/'map.parquet')
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        with transaction(conn):
            conn.execute("""CREATE TEMP TABLE source_metadata AS SELECT
                CAST(vacancy_id AS VARCHAR) AS posting_id,title,company AS company_raw,
                CAST(date_posted AS DATE) AS date_posted,country,city
                FROM read_parquet(?,union_by_name=true)""", [paths])
            if conn.execute("""SELECT posting_id FROM source_metadata
                GROUP BY posting_id HAVING posting_id IS NULL OR count(*)>1 LIMIT 1""").fetchone():
                raise ContractError("Original vacancy IDs must be nonnull and unique across the source files")
            conn.execute("""INSERT INTO skillmarket_app.postings
                (posting_id,title,company_raw,company_normalized,company_id,date_posted,country,city)
                SELECT p.posting_id,p.title,p.company_raw,m.company_normalized,m.company_id,
                       p.date_posted,p.country,p.city
                FROM source_metadata p LEFT JOIN skillmarket_staging.company_names m USING (company_raw)
                ON CONFLICT (posting_id) DO UPDATE SET title=excluded.title,
                    company_raw=excluded.company_raw,company_normalized=excluded.company_normalized,
                    company_id=excluded.company_id,date_posted=excluded.date_posted,
                    country=excluded.country,city=excluded.city""")
            # CREATE VIEW doesn't accept bound paths; quote only string literals.
            quoted = ','.join("'"+p.replace("'","''")+"'" for p in paths)
            conn.execute(f"CREATE OR REPLACE VIEW skillmarket_app.raw_vacancies AS SELECT * FROM read_parquet([{quoted}],union_by_name=true)")
            _refresh(conn)
        result = _status(conn)
    return result | {"seconds": round(time.perf_counter()-started,3)}


def _apply_labels(conn, format_name, parser_version, source_files):
    unknown = conn.execute("""SELECT l.posting_id FROM incoming_labels l
        LEFT JOIN skillmarket_app.postings p USING (posting_id)
        WHERE p.posting_id IS NULL LIMIT 5""").fetchall()
    if unknown:
        raise ContractError(f"Parser IDs not found in original metadata; load-vacancies first: {unknown}")
    if conn.execute("""SELECT posting_id FROM incoming_labels GROUP BY posting_id
        HAVING posting_id IS NULL OR count(*)>1 LIMIT 1""").fetchone():
        raise ContractError("Parser posting IDs must be nonnull and unique within an import")
    count = conn.execute('SELECT count(*) FROM incoming_labels').fetchone()[0]
    if not count:
        raise ContractError('No parser results to publish')
    conn.execute("""UPDATE skillmarket_app.postings p SET
        normalized_title=l.normalized_title,role_family=l.role_family,job_type=l.job_type,
        parse_status=l.parse_status,parser_version=l.parser_version,taxonomy_version=l.taxonomy_version
        FROM incoming_labels l WHERE p.posting_id=l.posting_id""")
    conn.execute("""DELETE FROM skillmarket_app.posting_skills ps USING incoming_labels l
        WHERE ps.posting_id=l.posting_id""")
    conn.execute("""INSERT INTO skillmarket_app.posting_skills
        SELECT l.posting_id,unnest(list_distinct(l.skill_ids)) FROM incoming_labels l WHERE l.parse_status='ok'""")
    _refresh(conn)
    conn.execute("INSERT INTO skillmarket_app.parser_imports (import_id,format,parser_version,files,row_count) VALUES (?,?,?,?::JSON,?)",
                 [uuid.uuid4(),format_name,parser_version,json.dumps(source_files),count])
    return count


def publish_staged(database):
    """Publish the latest staged result per ID. Failed results clear old skill edges."""
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        with transaction(conn):
            conn.execute("""CREATE TEMP TABLE latest_records AS
                SELECT r.source_record_id AS posting_id,r.payload,b.parser_version,b.catalog_version
                FROM skillmarket_staging.records r JOIN skillmarket_ingest.batches b USING (batch_key)
                QUALIFY row_number() OVER (PARTITION BY r.source_record_id ORDER BY b.staged_at DESC,b.batch_id DESC)=1""")
            conn.execute("""INSERT INTO skillmarket_app.skills
                SELECT c.item_id,json_extract_string(c.payload,'$.name'),
                    json_extract_string(c.payload,'$.category'),coalesce(c.payload->'$.synonyms','[]'::JSON)
                FROM skillmarket_ingest.catalog_items c
                WHERE c.catalog_version IN (SELECT DISTINCT catalog_version FROM latest_records)
                QUALIFY row_number() OVER (PARTITION BY c.item_id ORDER BY c.catalog_version DESC)=1
                ON CONFLICT (skill_id) DO UPDATE SET name=excluded.name,category=excluded.category,synonyms=excluded.synonyms""")
            conn.execute(f"""CREATE TEMP TABLE title_families AS
                SELECT normalized_title,{classification_sql()} AS family FROM (
                    SELECT DISTINCT json_extract_string(payload,'$.normalized_title') AS normalized_title
                    FROM latest_records WHERE json_extract_string(payload,'$.parse_status')='ok')""")
            conn.execute("""CREATE TEMP TABLE incoming_labels AS WITH base AS (
                SELECT posting_id,json_extract_string(payload,'$.normalized_title') AS normalized_title,
                    json_extract_string(payload,'$.role_family') AS role_family,
                    json_extract_string(payload,'$.parse_status') AS parse_status,
                    CAST(payload->'$.skill_ids' AS VARCHAR[]) AS skill_ids,
                    parser_version,catalog_version AS taxonomy_version FROM latest_records)
                SELECT b.posting_id,b.normalized_title,b.role_family,
                    coalesce(b.role_family,f.family) AS job_type,b.parse_status,b.skill_ids,
                    b.parser_version,b.taxonomy_version
                FROM base b LEFT JOIN title_families f USING (normalized_title)""")
            imported = _apply_labels(conn,'jsonl','from-batch-manifests',[])
        return _status(conn) | {"published_records": imported}


def import_labeled(database, paths, *, parser_version='matching-v1'):
    """Adapter for parser/match_all.py: canonical skill names are given stable IDs."""
    paths = _paths(paths)
    snapshots = {p:(Path(p).stat().st_size,Path(p).stat().st_mtime_ns) for p in paths}
    with WRITER_LOCK, connect(database) as conn:
        _ready(conn)
        with transaction(conn):
            conn.execute("""CREATE TEMP TABLE native_labels AS SELECT
                CAST(vacancy_id AS VARCHAR) AS posting_id,job_type,CAST(skills AS VARCHAR[]) AS skills
                FROM read_parquet(?,union_by_name=true)""", [paths])
            if conn.execute("""SELECT posting_id FROM native_labels WHERE
                job_type IS NULL OR trim(job_type)='' OR skills IS NULL
                OR list_count(skills)<>len(skills) LIMIT 1""").fetchone():
                raise ContractError('Labeled Parquet requires a job_type and a nonnull skill list')
            if conn.execute("""SELECT s FROM native_labels CROSS JOIN UNNEST(skills) AS u(s)
                WHERE trim(s)='' LIMIT 1""").fetchone():
                raise ContractError('Skill names must be nonempty')
            conn.execute("""INSERT INTO skillmarket_app.skills
                SELECT DISTINCT 'name_'||md5(s),s,NULL,'[]'::JSON
                FROM native_labels CROSS JOIN UNNEST(skills) AS u(s)
                ON CONFLICT (skill_id) DO NOTHING""")
            conn.execute("""CREATE TEMP TABLE incoming_labels AS SELECT
                posting_id,job_type AS normalized_title,job_type AS role_family,job_type,
                'ok' AS parse_status,list_transform(skills,lambda s: 'name_'||md5(s)) AS skill_ids,
                ? AS parser_version,'parser-canonical-names-v1' AS taxonomy_version FROM native_labels""", [parser_version])
            for p,stat in snapshots.items():
                if (Path(p).stat().st_size,Path(p).stat().st_mtime_ns)!=stat:
                    raise ContractError('Parser output changed while reading; wait until the file is closed')
            imported = _apply_labels(conn,'labeled-parquet',parser_version,paths)
        return _status(conn) | {"published_records": imported}


def import_parser(database, directory, *, parser_version='matching-v1', progress=None):
    directory = Path(directory)
    if not directory.is_dir():
        raise ContractError('Parser folder not found; provide parser/ or the received output folder')
    manifests = sorted(p for p in directory.rglob('manifest.json') if (p.parent/'parsed.jsonl').exists())
    if manifests:
        receipts=[]
        loaded_catalogs=set()
        for index,path in enumerate(manifests,1):
            manifest=decode(path.read_bytes(),str(path))
            validate_manifest(manifest)
            catalog=next((p for p in [path.parent/'skills.jsonl',directory/'skills.jsonl',directory.parent/'skills.jsonl'] if p.exists()),None)
            version=manifest['skill_taxonomy_version']
            if catalog and version not in loaded_catalogs:
                load_skills(database,catalog,taxonomy_version=manifest['skill_taxonomy_version'])
                loaded_catalogs.add(version)
            receipts.append(receive_batch(database,path.parent))
            if progress:
                progress(f"Batch {index}/{len(manifests)}: {manifest['batch_id']} ({receipts[-1]['status']})")
        if progress:
            progress('Publishing all staged records and refreshing monthly aggregates...')
        return publish_staged(database) | {"format":"jsonl","batches_received":len(receipts)}
    candidates=sorted(directory.rglob('labeled_*.parquet'))
    if not candidates:
        raise ContractError('No JSONL batches or labeled_*.parquet files found in parser folder')
    readable=[];skipped=[]
    with duckdb.connect() as check:
        for path in candidates:
            try:
                check.execute('DESCRIBE SELECT * FROM read_parquet(?)',[str(path)]).fetchall()
                readable.append(str(path))
            except duckdb.Error:
                skipped.append(str(path))
    if not readable:
        raise ContractError('Parser files have no readable Parquet footer yet; wait until writing finishes')
    return import_labeled(database,readable,parser_version=parser_version) | {"format":"labeled-parquet","skipped_unfinished_files":skipped}


def export_database(database, directory='data'):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    queries={
        'postings':"""SELECT posting_id,company_normalized AS company,company_raw,company_id,
            date_posted AS date,strftime(date_posted,'%Y')||'-Q'||CAST(quarter(date_posted) AS VARCHAR) AS period,
            country,city,title,normalized_title,role_family,job_type,parse_status FROM skillmarket_app.postings""",
        'skills':"SELECT skill_id,name,category,CAST(synonyms AS VARCHAR[]) AS synonyms FROM skillmarket_app.skills",
        'posting_skills':'SELECT * FROM skillmarket_app.posting_skills',
        'monthly_totals':'SELECT * FROM skillmarket_app.monthly_totals ORDER BY job_type,month',
        'skill_demand':'SELECT * FROM skillmarket_app.skill_demand ORDER BY job_type,month,skill',
    }
    with WRITER_LOCK,connect(database) as conn:
        _ready(conn)
        for name,query in queries.items():
            conn.execute(f'COPY ({query}) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)',[str((directory/(name+'.parquet')).resolve())])
    return {"exports":[str((directory/(name+'.parquet')).resolve()) for name in queries]}


def verify_database(database, *, require_complete=False):
    """Check full-data coverage and relational/aggregate invariants."""
    with WRITER_LOCK,connect(database) as conn:
        _ready(conn)
        result=_status(conn)
        checks={
            'successful_without_job_type':"""SELECT count(*) FROM skillmarket_app.postings
                WHERE parse_status='ok' AND (job_type IS NULL OR trim(job_type)='')""",
            'orphan_posting_skills':"""SELECT count(*) FROM skillmarket_app.posting_skills ps
                LEFT JOIN skillmarket_app.postings p USING(posting_id) WHERE p.posting_id IS NULL""",
            'unknown_skill_ids':"""SELECT count(*) FROM skillmarket_app.posting_skills ps
                LEFT JOIN skillmarket_app.skills s USING(skill_id) WHERE s.skill_id IS NULL""",
            'skills_on_failed_or_pending':"""SELECT count(*) FROM skillmarket_app.posting_skills ps
                JOIN skillmarket_app.postings p USING(posting_id) WHERE p.parse_status<>'ok'""",
            'invalid_monthly_skill_counts':"""SELECT count(*) FROM skillmarket_app.skill_demand d
                JOIN skillmarket_app.monthly_totals t USING(job_type,month)
                WHERE d.postings<0 OR d.postings>t.total_postings""",
        }
        result['checks']={name:conn.execute(sql).fetchone()[0] for name,sql in checks.items()}
        expected=conn.execute("SELECT count(*) FROM skillmarket_app.postings WHERE parse_status='ok' AND date_posted IS NOT NULL").fetchone()[0]
        actual=conn.execute('SELECT coalesce(sum(total_postings),0) FROM skillmarket_app.monthly_totals').fetchone()[0]
        result['checks']['monthly_total_difference']=actual-expected
        expected_edges=conn.execute("""SELECT count(*) FROM skillmarket_app.posting_skills ps
            JOIN skillmarket_app.postings p USING(posting_id) WHERE p.parse_status='ok' AND p.date_posted IS NOT NULL""").fetchone()[0]
        actual_edges=conn.execute('SELECT coalesce(sum(postings),0) FROM skillmarket_app.skill_demand').fetchone()[0]
        result['checks']['monthly_skill_difference']=actual_edges-expected_edges
        result['job_types']=conn.execute('SELECT count(DISTINCT job_type) FROM skillmarket_app.monthly_totals').fetchone()[0]
        result['verified']=all(n==0 for n in result['checks'].values()) and (not require_complete or (result['pending']==0 and result['source_postings']>0))
        if not result['verified']:
            raise ContractError('Database verification failed: '+json.dumps(result))
        return result


def build_database(database, *, sources=None, parser_directory='parser', export_directory='data', progress=None):
    started=time.perf_counter()
    initialize(database)
    if not sources:
        repo=Path(__file__).resolve().parents[1]
        local=sorted((repo/'data').glob('vacancies_*.parquet'))
        sources=[str(p) for p in local or sorted((Path.home()/'Downloads').glob('vacancies_*.parquet'))]
    if not sources:
        raise ContractError('Original vacancy Parquet files are required in data/, Downloads, or via --sources')
    if progress: progress('Loading original metadata and company names...')
    load_vacancies(database,sources)
    import_parser(database,parser_directory,progress=progress)
    if progress: progress('Verifying the complete database...')
    result=verify_database(database,require_complete=True)
    if progress: progress('Exporting application tables...')
    result.update(export_database(database,export_directory))
    return result | {'seconds':round(time.perf_counter()-started,3)}
