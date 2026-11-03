"""Small internal mobility demo: fixed synthetic profiles, real skill/course IDs."""

from __future__ import annotations

from importlib.resources import files
import json


from .access import read_queries
from .learning import SkillCatalog, _recommend


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
    matcher = SkillCatalog(query)
    catalog = {s: r['name'] for s, r in matcher.rows.items()}
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
        plan = _recommend(query, matcher, sorted(missing), max_courses=3) if missing else {
            'courses': [], 'skills_without_courses': [], 'skills_not_covered_by_plan': []}
        courses = plan['courses']
        unavailable = {s['skill_id'] for s in plan['skills_without_courses']}
        recommended = {s['skill_id'] for course in courses for s in course['covered_skills']}
        candidates.append({
            'profile_id': profile['profile_id'], 'label': profile['label'],
            'current_role': profile['current_role'], 'matched_count': len(matched),
            'required_count': len(required), 'match_percentage': round(100*len(matched)/len(required), 1),
            'matched_skills': skills(matched), 'missing_skills': skills(missing),
            'courses': courses, 'skills_without_courses': skills(unavailable),
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
    with read_queries() as query:
        return _match(query, target_role_id=target_role_id, n=n, profile_id=profile_id)


def format_internal_talent(result):
    """Render measured results directly, preventing invented course coverage."""
    role = result['target_role']['title']
    lines = [f"Fictieve HR-demo voor {result['company']}: de profielen en het {role}-doelprofiel zijn verzonnen.",
             '', 'Ranking op skilloverlap:']
    for index, candidate in enumerate(result['candidates'], 1):
        lines.append(f"{index}. {candidate['label']} ({candidate['current_role']}): "
                     f"{candidate['matched_count']}/{candidate['required_count']} skills "
                     f"({candidate['match_percentage']}% overlap).")
    best = result['candidates'][0]
    names = lambda items: ', '.join(s['name'] for s in items) or 'geen'
    lines += ['', f"Voor {best['label']}:", 'Aanwezige doelvaardigheden: '+names(best['matched_skills'])+'.',
              'Ontbrekende doelvaardigheden: '+names(best['missing_skills'])+'.']
    if best['courses']:
        lines += ['', 'Cursusopties voor ontbrekende skills:']
        for course in best['courses']:
            lines.append(f"- {course['title']} ({course['site']}): {course['url']}\n"
                         f"  Gekoppelde ontbrekende skills: {names(course['covered_skills'])}.")
    if best['skills_without_courses']:
        lines += ['', 'Geen cursus gekoppeld in onze huidige catalogus voor: '+names(best['skills_without_courses'])+'.']
    not_shown = {s['skill_id'] for s in best['skills_not_covered_by_recommendations']} - {
        s['skill_id'] for s in best['skills_without_courses']}
    if not_shown:
        lines.append('Niet afgedekt door deze cursusopties: '+names(
            [s for s in best['missing_skills'] if s['skill_id'] in not_shown])+'.')
    lines += ['',
              f"Volgende stap: bespreek met HR of {best['label']} wil doorgroeien en toets de ontbrekende skills voordat je een leertraject kiest."]
    return '\n'.join(lines)
