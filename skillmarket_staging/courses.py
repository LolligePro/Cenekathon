"""Bulk-import course metadata and parser-contract skills into separate tables."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time

from .contracts import ContractError, decode, strings, text, validate_manifest, validate_record
from .db import WRITER_LOCK, connect, initialize, transaction
from .ingest import _failure, _finish, _json, _start


def _metadata(path):
    rows, digest = {}, hashlib.sha256()
    with path.open('rb') as source:
        for number, line in enumerate(source, 1):
            digest.update(line)
            row = decode(line, f'courses.jsonl line {number}')
            course_id = text(row.get('posting_id'), 'course posting_id')
            if course_id in rows:
                raise ContractError(f'Duplicate course metadata ID: {course_id}')
            for key in ('title', 'url', 'site'):
                if not isinstance(row.get(key), str) or '\x00' in row[key]:
                    raise ContractError(f'Course {key} must be a string without NUL')
            if row.get('short_intro') is not None and not isinstance(row['short_intro'], str):
                raise ContractError('Course short_intro must be a string or null')
            strings(row.get('listed_skills'), 'listed_skills')
            if row.get('skill_source') not in (None, 'listed', 'text'):
                raise ContractError('Course skill_source must be listed, text or null')
            rows[course_id] = row
    if not rows:
        raise ContractError('Course metadata is empty')
    return rows, digest.hexdigest()


def import_courses(database, directory='courses', *, batch_directory=None):
    """Import one complete course batch. Identical batches are safe to replay.

    Reuse the parser's validation and durable import log, keeping course records
    out of vacancy staging so later vacancy publication cannot consume them.
    """
    started = time.perf_counter()
    directory = Path(directory)
    initialize(database)
    with WRITER_LOCK, connect(database) as conn:
        run_id = _start(conn, 'import_courses', directory)
        state = {}
        try:
            if batch_directory is None:
                manifests = sorted((directory / 'batches').glob('*/manifest.json'))
                if len(manifests) != 1:
                    raise ContractError('Expected one course batch; select it with --batch-directory')
                batch_directory = manifests[0].parent
            batch_directory = Path(batch_directory)
            manifest = decode((batch_directory / 'manifest.json').read_bytes(), 'manifest.json')
            validate_manifest(manifest)
            manifest_hash = hashlib.sha256(_json(manifest).encode()).hexdigest()
            state.update(manifest=manifest, batch_id=manifest['batch_id'],
                         manifest_sha256=manifest_hash, expected_records=manifest['record_count'])
            taxonomy = manifest['skill_taxonomy_version']
            if not conn.execute('SELECT 1 FROM skillmarket_ingest.catalogs WHERE catalog_version=?', [taxonomy]).fetchone():
                raise ContractError(f'Load the shared skill catalog first: {taxonomy}')
            known = {r[0] for r in conn.execute('''SELECT c.item_id
                FROM skillmarket_ingest.catalog_items c JOIN skillmarket_app.skills s ON s.skill_id=c.item_id
                WHERE c.catalog_version=?''', [taxonomy]).fetchall()}
            metadata, metadata_hash = _metadata(directory / 'courses.jsonl')
            with tempfile.TemporaryDirectory(prefix='skillmarket-courses-') as temporary:
                envelope = Path(temporary) / 'courses.jsonl'
                digest, seen, edges, with_skills, failed = hashlib.sha256(), set(), 0, 0, 0
                with (batch_directory / 'parsed.jsonl').open('rb') as source, envelope.open('w', encoding='utf-8') as target:
                    for number, line in enumerate(source, 1):
                        digest.update(line)
                        state['received_records'] = number
                        row = decode(line, f'course parsed.jsonl line {number}')
                        validate_record(row, known)
                        course_id = row['posting_id']
                        if course_id in seen:
                            raise ContractError(f'Duplicate parsed course ID: {course_id}')
                        if course_id not in metadata:
                            raise ContractError(f'Course metadata missing for ID: {course_id}')
                        seen.add(course_id)
                        edges += len(row['skill_ids'])
                        with_skills += bool(row['skill_ids'])
                        failed += row['parse_status'] == 'failed'
                        target.write(_json({'course_id': course_id, 'metadata': metadata[course_id], 'payload': row}) + '\n')
                state['records_sha256'] = digest.hexdigest()
                if len(seen) != manifest['record_count'] or seen != metadata.keys():
                    raise ContractError('Course metadata IDs, parser IDs and manifest record_count must match')
                expected = batch_directory / 'input-ids.txt'
                if expected.is_file():
                    ids = expected.read_text(encoding='utf-8').splitlines()
                    if len(ids) != len(set(ids)) or set(ids) != seen:
                        raise ContractError('Course input-ids.txt differs from the parser IDs')
                state['source_ids_verified'] = True
                result = {'run_id': str(run_id), 'batch_id': manifest['batch_id'], 'courses': len(seen),
                          'course_skills': edges, 'with_skills': with_skills, 'without_skills': len(seen)-with_skills,
                          'failed': failed, 'taxonomy_version': taxonomy}
                with transaction(conn):
                    old = conn.execute('''SELECT manifest_sha256, metadata_sha256, records_sha256, run_id
                        FROM skillmarket_app.course_imports WHERE batch_id=?''', [manifest['batch_id']]).fetchone()
                    if old:
                        if old[:3] != (manifest_hash, metadata_hash, state['records_sha256']):
                            raise ContractError('Course batch_id is immutable; use a new batch_id for changes')
                        _finish(conn, run_id, status='duplicate', state=state, duplicate_of=old[3])
                        return result | {'status': 'duplicate', 'seconds': round(time.perf_counter()-started, 3)}
                    conn.execute('''CREATE TEMP TABLE incoming_courses AS SELECT * FROM read_json(?,
                        format='newline_delimited', columns={course_id:'VARCHAR', metadata:'JSON', payload:'JSON'})''', [str(envelope)])
                    conn.execute('''DELETE FROM skillmarket_app.course_skills USING incoming_courses i
                        WHERE course_skills.course_id=i.course_id''')
                    conn.execute('''DELETE FROM skillmarket_app.courses USING incoming_courses i
                        WHERE courses.course_id=i.course_id''')
                    conn.execute('''INSERT INTO skillmarket_app.courses SELECT course_id,
                        json_extract_string(metadata,'$.title'), json_extract_string(metadata,'$.url'),
                        json_extract_string(metadata,'$.site'), json_extract_string(metadata,'$.short_intro'),
                        json_extract(metadata,'$.listed_skills')::VARCHAR[], json_extract_string(metadata,'$.skill_source'),
                        json_extract_string(payload,'$.normalized_title'), json_extract_string(payload,'$.parse_status'),
                        ?, ?, ?, metadata, payload FROM incoming_courses''',
                        [manifest['parser_version'], taxonomy, manifest['batch_id']])
                    conn.execute('''INSERT INTO skillmarket_app.course_skills
                        SELECT course_id, unnest(json_extract(payload,'$.skill_ids')::VARCHAR[])
                        FROM incoming_courses WHERE json_extract_string(payload,'$.parse_status')='ok' ''')
                    conn.execute('''INSERT INTO skillmarket_app.course_imports
                        (batch_id,run_id,manifest,manifest_sha256,metadata_sha256,records_sha256,course_count,skill_link_count)
                        VALUES (?,?,?::JSON,?,?,?,?,?)''',
                        [manifest['batch_id'], run_id, _json(manifest), manifest_hash, metadata_hash, state['records_sha256'], len(seen), edges])
                    _finish(conn, run_id, status='loaded', state=state)
            return result | {'status': 'loaded', 'seconds': round(time.perf_counter()-started, 3)}
        except BaseException as exc:
            _failure(conn, run_id, state, exc)
