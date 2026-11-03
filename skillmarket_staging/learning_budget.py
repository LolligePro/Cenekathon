"""Small, deterministic learning-budget demo over actual vacancy skill sets."""
from itertools import combinations
import json
import math
import re

from .access import read_queries
from .learning import SkillCatalog, _recommend, normalize, profile_request

DEFAULT_SKILL_HOURS = 20
MAX_CANDIDATES = 12


def plan_learning_budget(skills, hours_budget=40, country='ALL', job_type='ALL', skill_hours=None):
    if not isinstance(skills, list) or len(skills) > 20 or any(not isinstance(s, str) or not s.strip() for s in skills):
        raise ValueError('skills must contain at most 20 nonempty names or IDs')
    if isinstance(hours_budget, bool) or not isinstance(hours_budget, (int, float)) or not math.isfinite(hours_budget) or not 0 < hours_budget <= 10000:
        raise ValueError('hours_budget must be greater than 0 and at most 10000')
    if country not in ['ALL', 'BE', 'NL', 'LU', 'GB']:
        raise ValueError('Unknown country code')
    if skill_hours is not None and not isinstance(skill_hours, dict):
        raise ValueError('skill_hours must map skill names or IDs to positive hours')
    with read_queries() as query:
        catalog = SkillCatalog(query)
        resolved = catalog.resolve(skills) if skills else {'skills': [], 'unresolved': [], 'ambiguous': {}}
        if resolved['unresolved'] or resolved['ambiguous']:
            raise ValueError('Onbekende of ambigue startskills: ' + ', '.join([*resolved['unresolved'], *resolved['ambiguous']]))
        costs = {}
        for name, hours in (skill_hours or {}).items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError('skill_hours keys must be nonempty names or skill IDs')
            value = catalog.resolve([name])
            if not value['skills'] or value['ambiguous']:
                raise ValueError('Unknown or ambiguous skill in skill_hours: ' + name)
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) or hours <= 0:
                raise ValueError('Skill hours must be positive finite numbers')
            costs[value['skills'][0]['skill_id']] = hours
        jobs = {r['job_type'] for r in query('SELECT DISTINCT job_type FROM skillmarket_app.monthly_totals')}
        if job_type != 'ALL' and job_type not in jobs:
            raise ValueError('Unknown job_type')
        ids = [s['skill_id'] for s in resolved['skills']]
        marks = ','.join('?' for _ in ids)
        known = f'ps.skill_id IN ({marks})' if ids else 'FALSE'
        cte = f'''WITH cohort AS (
            SELECT posting_id FROM skillmarket_app.postings
            WHERE parse_status='ok' AND date_posted>=DATE '2025-01-01' AND date_posted<DATE '2026-01-01'
              AND (?='ALL' OR country=?) AND (?='ALL' OR job_type=?)
        ), detail AS (
            SELECT p.posting_id,count(ps.skill_id) AS required_count,
                   count(ps.skill_id) FILTER(WHERE {known}) AS matched_count
            FROM cohort p LEFT JOIN skillmarket_app.posting_skills ps USING(posting_id)
            GROUP BY p.posting_id
        )'''
        params = [country, country, job_type, job_type, *ids]
        missing = f'ps.skill_id NOT IN ({marks})' if ids else 'TRUE'
        stats = query(cte + f''', candidates AS (
            SELECT ps.skill_id,count(*) AS relevant_count
            FROM detail d JOIN skillmarket_app.posting_skills ps USING(posting_id)
            WHERE d.required_count-d.matched_count BETWEEN 1 AND 2 AND {missing}
              AND EXISTS (SELECT 1 FROM skillmarket_app.course_skills cs
                  JOIN skillmarket_app.courses c USING(course_id)
                  WHERE cs.skill_id=ps.skill_id AND c.parse_status='ok'
                    AND (c.url LIKE 'https://%' OR c.url LIKE 'http://%'))
            GROUP BY ps.skill_id ORDER BY relevant_count DESC,ps.skill_id LIMIT {MAX_CANDIDATES}
        ) SELECT count(*) AS observed_postings,
            count(*) FILTER(WHERE required_count>0 AND required_count=matched_count) AS baseline_matches,
            (SELECT list(skill_id ORDER BY relevant_count DESC,skill_id) FROM candidates) AS candidate_ids
            FROM detail''', params + ids)[0]
        candidates = stats.pop('candidate_ids') or []
        affordable = [s for s in candidates if costs.get(s, DEFAULT_SKILL_HOURS) <= hours_budget]
        counts = []
        if affordable:
            pool = ','.join('?' for _ in affordable)
            counts = query(cte + f''', gaps AS (
                SELECT d.posting_id,list(ps.skill_id ORDER BY ps.skill_id) AS missing_ids
                FROM detail d JOIN skillmarket_app.posting_skills ps USING(posting_id)
                WHERE d.required_count-d.matched_count BETWEEN 1 AND 2 AND {missing}
                  AND ps.skill_id IN ({pool})
                GROUP BY d.posting_id,d.required_count,d.matched_count
                HAVING count(*)=d.required_count-d.matched_count
            ) SELECT missing_ids,count(*) AS postings FROM gaps GROUP BY missing_ids''', params + ids + affordable)
        grouped = [(set(r['missing_ids']), r['postings']) for r in counts]
        singles = {next(iter(gap)): n for gap, n in grouped if len(gap) == 1}
        plans = []
        for size in (1, 2):
            for bundle in combinations(affordable, size):
                hours = sum(costs.get(s, DEFAULT_SKILL_HOURS) for s in bundle)
                if hours > hours_budget:
                    continue
                selected = set(bundle)
                extra = sum(n for gap, n in grouped if gap <= selected)
                if not extra:
                    continue
                plans.append({'skills': [catalog.skill(s) | {'hours': costs.get(s, DEFAULT_SKILL_HOURS)} for s in sorted(bundle)],
                    'hours': hours, 'extra_matches': extra, 'total_matches': stats['baseline_matches'] + extra,
                    'combination_bonus': extra - sum(singles.get(s, 0) for s in bundle)})
        plans.sort(key=lambda p: (-p['extra_matches'], p['hours'], tuple(s['skill_id'] for s in p['skills'])))
        plans = plans[:3]
        for plan in plans:
            courses = _recommend(query, catalog, [s['skill_id'] for s in plan['skills']], max_courses=2)
            plan['courses'] = courses['courses']
        return {'known_skills': resolved['skills'], 'hours_budget': hours_budget, 'default_skill_hours': DEFAULT_SKILL_HOURS,
            'skill_hours': {**costs}, 'country': country, 'job_type': job_type, 'year': 2025,
            'candidate_count': len(candidates), 'plans': plans, **stats}


def format_budget(result):
    names = lambda skills: ', '.join(s['name'] for s in skills)
    lines = [f"Leerbudget: {result['hours_budget']:g} uur. Startskills: {names(result['known_skills']) or 'geen opgegeven'}.",
        f"2025 · {result['country']} · {result['job_type']}: {result['baseline_matches']} vacatures met volledige skillmatch vóór dit leerplan.",
        f"Planning: {result['default_skill_hours']} uur per skill als demo-aanname; opgegeven uren per skill krijgen voorrang."]
    for i, plan in enumerate(result['plans'], 1):
        label = 'Aanbevolen pakket' if i == 1 else f'Alternatief {i-1}'
        lines.append(f"{label}: {names(plan['skills'])} — {plan['hours']:g} uur → +{plan['extra_matches']} extra vacaturematches ({plan['total_matches']} totaal).")
        if plan['combination_bonus']:
            lines.append(f"Combinatie-effect: {plan['combination_bonus']} van die extra vacatures vereisen beide nieuwe skills samen.")
        for course in plan['courses']:
            lines.append(f"- {course['title']} ({course['site']}): {course['url']}\n  Skills: {names(course['covered_skills'])}.")
    if not result['plans']:
        lines.append('Geen pakket met extra volledige skillmatches gevonden binnen dit budget. Probeer meer leeruren of een bredere selectie.')
    else:
        lines.append('Elke vacature telt per pakket één keer. De alternatieven zijn afzonderlijke keuzes; hun aantallen worden niet opgeteld.')
    return '\n\n'.join(lines)


def budget_chart(result):
    if not result['plans']:
        return None
    return {'type': 'bar', 'title': 'Leerbudget: extra vacaturematches per pakket', 'y_label': 'Extra vacatures',
        'series': [{'name': 'Extra volledige skillmatches', 'points': [
            [' + '.join(s['name'] for s in p['skills']), p['extra_matches']] for p in result['plans']]}]}


def budget_request(question, history=None):
    """Fast path for hours + skills; reuse the preceding profile and filters."""
    hour = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(?:leeruren|leeruur|uren|uur|hours?|hrs?)\b', question, re.I)
    if not hour:
        return None
    previous = None
    for message in reversed(history or []):
        if not isinstance(message.get('content'), list):
            continue
        for block in reversed(message['content']):
            if block.get('type') != 'tool_result' or block.get('is_error'):
                continue
            try:
                result = json.loads(block['content'])
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(result, dict) and 'known_skills' in result:
                previous = result
                break
        if previous is not None:
            break
    if not previous and not re.search(r'\b(skills?|skillpakketten|skillpakket|bundles?|leren|learn|learning|training|leerbudget|opleidingsbudget|cursus|courses?|pakket|ik ken|i know|my skills|mijn skills)\b', question, re.I):
        return None
    args = {'skills': [], 'hours_budget': float(hour.group(1).replace(',', '.'))}
    if previous is not None:
        args.update(skills=[s['skill_id'] for s in previous['known_skills']],
            country=previous.get('country', 'ALL'), job_type=previous.get('job_type', 'ALL'),
            skill_hours=previous.get('skill_hours', {}))
    profile_text = re.sub(r'\b(?:(?:en|and)\s+)?(?:ik\s+heb|i\s+have|heb|have)\s+\d+(?:[.,]\d+)?\s*(?:leeruren|leeruur|uren|uur|hours?|hrs?)\b', '.', question, flags=re.I)
    profile = profile_request(profile_text)
    if profile:
        args.update(profile)
    # A filter named in a follow-up overrides the previous filter.
    key = ' ' + normalize(question) + ' '
    for code, aliases in {'BE': ['belgie', 'belgium'], 'NL': ['nederland', 'netherlands'],
                          'LU': ['luxemburg', 'luxembourg'], 'GB': ['united kingdom', 'uk']}.items():
        if any(' ' + alias + ' ' in key for alias in aliases) or re.search(r'\b' + code + r'\b', question):
            args['country'] = code
    for group in ['risk and compliance', 'finance and accounting', 'technology', 'human resources',
                  'operations', 'legal', 'sales and marketing', 'customer service and support', 'product and project management']:
        if normalize(group) in key:
            args['job_type'] = group
            break
    return args
