"""Small internal mobility demo: fixed synthetic profiles, real skill/course IDs."""

from __future__ import annotations

from importlib.resources import files
import json
import os
from pathlib import Path

import duckdb

from .client import RemoteDatabase


def _match(query, *, target_role_id='ai-auditor', n=3, profile_id=None, demo=None):
    if type(n) is not int or not 1 <= n <= 5:
        raise ValueError('n must be an integer from 1 to 5')
    if demo is None:
        demo = json.loads(files('skillmarket_staging').joinpath('demo', 'internal_talent.json').read_text(encoding='utf-8'))
    role = next((r for r in demo['roles'] if r['role_id'] == target_role_id), None)
    if role is None:
        raise ValueError('This demo supports target_role_id=ai-auditor')
    profiles = demo['profiles']
    if profile_id is not None:
        profiles = [p for p in profiles if p['profile_id'] == profile_id]
        if not profiles:
            raise ValueError('Unknown demo profile_id; use demo-a through demo-e')
    required = set(role['skill_ids'])
    if not required:
        raise ValueError('Target role must contain skills')
    all_ids = sorted(required | {s for p in profiles for s in p['skill_ids']})
    placeholders = ','.join('?' for _ in all_ids)
    catalog = {r['skill_id']: r['name'] for r in query(
        f'SELECT skill_id,name FROM skillmarket_app.skills WHERE skill_id IN ({placeholders})', all_ids)}
    unknown = set(all_ids)-catalog.keys()
    if unknown:
        raise ValueError(f'Demo skills missing from the database catalog: {sorted(unknown)}')

    def skills(ids):
        return [{'skill_id': s, 'name': catalog[s]} for s in sorted(ids)]

    candidates = []
    ranked = sorted(profiles, key=lambda p: (-len(required & set(p['skill_ids'])), p['profile_id']))[:n]
    for profile in ranked:
        matched = required & set(profile['skill_ids'])
        missing = required-matched
        courses = []
        if missing:
            ids = sorted(missing)
            placeholders = ','.join('?' for _ in ids)
            courses = query(f'''SELECT c.course_id,c.title,c.url,c.site,
                list(DISTINCT cs.skill_id ORDER BY cs.skill_id) AS covered_skill_ids,
                count(DISTINCT cs.skill_id) AS covered_count
                FROM skillmarket_app.courses c
                JOIN skillmarket_app.course_skills cs USING(course_id)
                WHERE c.parse_status='ok' AND cs.skill_id IN ({placeholders})
                GROUP BY c.course_id,c.title,c.url,c.site
                ORDER BY covered_count DESC,c.title,c.course_id LIMIT 3''', ids)
            # Coverage is checked against the whole catalog, not just the top 3.
            available = {r['skill_id'] for r in query(f'''SELECT DISTINCT cs.skill_id
                FROM skillmarket_app.course_skills cs JOIN skillmarket_app.courses c USING(course_id)
                WHERE c.parse_status='ok' AND cs.skill_id IN ({placeholders})''', ids)}
        else:
            available = set()
        recommended = set()
        for course in courses:
            covered = set(course.pop('covered_skill_ids'))
            course['covered_skills'] = skills(covered)
            recommended |= covered
        candidates.append({
            'profile_id': profile['profile_id'], 'label': profile['label'],
            'current_role': profile['current_role'], 'matched_count': len(matched),
            'required_count': len(required), 'match_percentage': round(100*len(matched)/len(required), 1),
            'matched_skills': skills(matched), 'missing_skills': skills(missing),
            'courses': courses, 'skills_without_courses': skills(missing-available),
            'skills_not_covered_by_recommendations': skills(missing-recommended),
        })
    return {
        'synthetic': True, 'company': demo['company'], 'data_owner': demo['data_owner'],
        'target_role': {'role_id': role['role_id'], 'title': role['title'], 'description': role['description'],
                        'required_skills': skills(required)},
        'profiles_evaluated': len(profiles), 'candidates': candidates,
        'limitations': [
            'Fictieve interne profielen en handmatig samengesteld doelprofiel; geen echte personeelsgegevens.',
            'De score is ongewogen skilloverlap, geen beoordeling van geschiktheid of kans op een baan.',
            'Cursussen komen uit parserkoppelingen; cursusinhoud, niveau en leerresultaten zijn niet geverifieerd.',
            'Geen cursus gevonden betekent alleen dat onze huidige catalogus geen koppeling bevat.',
        ],
    }


def find_internal_talent(target_role_id='ai-auditor', n=3, profile_id=None):
    """Use an explicit remote API, otherwise a local read-only DB or localhost API."""
    api_url = os.environ.get('SKILLMARKET_API_URL')
    database = Path(os.environ.get('SKILLMARKET_DB', Path(__file__).resolve().parents[1]/'data'/'skillmarket.duckdb'))
    kwargs = dict(target_role_id=target_role_id, n=n, profile_id=profile_id)
    if api_url or not database.is_file():
        remote = RemoteDatabase(api_url)

        def query(sql, params):
            result = remote.query(sql, params, max_rows=100)
            if result['truncated']:
                raise ValueError('Internal talent query returned too many rows')
            return [dict(zip([c['name'] for c in result['columns']], row)) for row in result['rows']]

        return _match(query, **kwargs)
    with duckdb.connect(str(database), read_only=True) as conn:
        def query(sql, params):
            cursor = conn.execute(sql, params)
            return [dict(zip([c[0] for c in cursor.description], row)) for row in cursor.fetchall()]

        return _match(query, **kwargs)
