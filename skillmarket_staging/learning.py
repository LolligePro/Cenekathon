"""Resolve catalog IDs and recommend courses from the actual course tables."""
import json
import re
import unicodedata
from urllib.parse import urlparse

from .access import read_queries

COURSE_INTENT = r'\b(courses?|cursus(?:sen)?|opleiding(?:en)?|training(?:en)?|leertraject(?:en)?)\b'

ALIASES = {'ml':'machine learning', 'ai':'artificial intelligence', 'python3':'python',
           'ms excel':'microsoft excel', 'kunstmatige intelligentie':'artificial intelligence',
           'machine leren':'machine learning'}


def normalize(value):
    value=unicodedata.normalize('NFKD',value.casefold())
    return re.sub(r'[^a-z0-9+#]+',' ',''.join(c for c in value if not unicodedata.combining(c))).strip()


class SkillCatalog:
    def __init__(self,query):
        rows=query('SELECT skill_id,name,category,synonyms FROM skillmarket_app.skills ORDER BY skill_id',[])
        self.rows={r['skill_id']:r for r in rows}
        self.names={}
        for row in rows:
            synonyms=row.get('synonyms') or []
            if isinstance(synonyms,str): synonyms=json.loads(synonyms)
            for name in [row['name'],*synonyms]:
                self.names.setdefault(normalize(name),set()).add(row['skill_id'])

    def skill(self,skill_id):
        row=self.rows[skill_id]
        return {'skill_id':skill_id,'name':row['name']}

    def resolve(self,values):
        if not isinstance(values,list) or not 1<=len(values)<=20 or any(not isinstance(v,str) or not v.strip() for v in values):
            raise ValueError('skills must contain 1 to 20 nonempty names or skill IDs')
        selected,unresolved,ambiguous=set(),[],{}
        for value in values:
            key=normalize(value)
            skill_id=value.strip().upper()
            matches={skill_id} if skill_id in self.rows else self.names.get(normalize(ALIASES.get(key,key)),set())
            if not matches:
                matches=self.names.get(key,set())
            if len(matches)==1: selected.update(matches)
            elif matches: ambiguous[value]=[self.skill(s) for s in sorted(matches)]
            else: unresolved.append(value)
        return {'skills':[self.skill(s) for s in sorted(selected)],'unresolved':unresolved,'ambiguous':ambiguous}

    def search(self,text,n=5):
        exact=self.resolve([text])
        if exact['skills']: return exact['skills']
        key=normalize(text)
        ids=sorted({s for name,ids in self.names.items() if key in name for s in ids},
                   key=lambda s:(normalize(self.rows[s]['name'])!=key,self.rows[s]['name']))
        return [self.skill(s) for s in ids[:n]]

    def mentions(self,text):
        """Literal catalog mentions for explicit course questions, longest first."""
        text=' '+normalize(text)+' '
        noise={'skills','skill','training','learning','courses','course','learn','knowledge','education','communication'}
        keys=set(self.names)|set(ALIASES)|{s.lower() for s in self.rows}
        used,values=[],[]
        for key in sorted(keys,key=lambda k:(-len(k),k)):
            if key in noise or (len(key)<3 and key not in ALIASES): continue
            match=re.search(r'(?<!\S)'+re.escape(key)+r'(?!\S)',text)
            if match and not any(match.start()<b and match.end()>a for a,b in used):
                used.append(match.span())
                values.append(key)
        return self.resolve(values)['skills'] if values else []


def _recommend(query,catalog,values,max_courses=5):
    if type(max_courses) is not int or not 1<=max_courses<=10:
        raise ValueError('max_courses must be between 1 and 10')
    resolved=catalog.resolve(values)
    ids=[s['skill_id'] for s in resolved['skills']]
    courses=[]
    if ids:
        marks=','.join('?' for _ in ids)
        courses=query(f'''SELECT c.course_id,c.title,c.url,c.site,c.skill_source,
            list(DISTINCT cs.skill_id ORDER BY cs.skill_id) AS skill_ids
            FROM skillmarket_app.courses c JOIN skillmarket_app.course_skills cs USING(course_id)
            WHERE c.parse_status='ok' AND cs.skill_id IN ({marks})
            GROUP BY c.course_id,c.title,c.url,c.site,c.skill_source''',ids)
        courses=[c for c in courses if urlparse(c['url'] or '').scheme in ('http','https') and urlparse(c['url']).netloc]
    available={s for course in courses for s in course['skill_ids']}
    remaining=set(ids)
    plan=[]
    while remaining and len(plan)<max_courses:
        ranked=sorted(courses,key=lambda c:(-len(set(c['skill_ids'])&remaining),c['skill_source']!='listed',
            -sum(normalize(catalog.rows[s]['name']) in normalize(c['title']) for s in set(c['skill_ids'])&remaining),
            c['title'].casefold(),c['course_id']))
        if not ranked: break
        best=ranked[0]
        new=set(best['skill_ids'])&remaining
        if not new: break
        covered=set(best['skill_ids'])&set(ids)
        plan.append({k:best[k] for k in ['course_id','title','url','site']} | {
            'covered_skills':[catalog.skill(s) for s in sorted(covered)],
            'newly_covered_skills':[catalog.skill(s) for s in sorted(new)], 'covered_count':len(covered)})
        remaining-=new
    return resolved | {'courses':plan,
        'skills_without_courses':[catalog.skill(s) for s in ids if s not in available],
        'skills_not_covered_by_plan':[catalog.skill(s) for s in ids if s in remaining]}


def recommend_courses(skills,max_courses=5):
    with read_queries() as query:
        return _recommend(query,SkillCatalog(query),skills,max_courses)


def find_skills(text):
    if not isinstance(text,str) or not text.strip(): raise ValueError('text must be a nonempty string')
    with read_queries() as query:
        catalog=SkillCatalog(query)
        return {'skills':catalog.search(text)}


def canonical_names(values):
    with read_queries() as query:
        result=SkillCatalog(query).resolve(values)
    if result['unresolved'] or result['ambiguous']:
        raise ValueError('Unknown or ambiguous skills; resolve them with find_skills: '+json.dumps(result))
    return [s['name'] for s in result['skills']]


def profile_advice(skills,country='ALL',job_type='ALL'):
    if country not in ['ALL','BE','NL','LU','GB']: raise ValueError('Unknown country code')
    with read_queries() as query:
        catalog=SkillCatalog(query)
        resolved=catalog.resolve(skills)
        ids=[s['skill_id'] for s in resolved['skills']]
        if not ids:
            return {'known_skills':[], 'unresolved':resolved['unresolved'],'ambiguous':resolved['ambiguous'],
                    'suggested_skills':[],'courses':[]}
        jobs={r['job_type'] for r in query('SELECT DISTINCT job_type FROM skillmarket_app.monthly_totals')}
        if job_type!='ALL' and job_type not in jobs: raise ValueError('Unknown job_type')
        marks=','.join('?' for _ in ids)
        cte=f'''WITH cohort AS (
            SELECT posting_id FROM skillmarket_app.postings
            WHERE parse_status='ok' AND date_posted>=DATE '2025-01-01' AND date_posted<DATE '2026-01-01'
              AND (?='ALL' OR country=?) AND (?='ALL' OR job_type=?)
        ), detail AS (
            SELECT p.posting_id,count(ps.skill_id) AS required_count,
                   count(ps.skill_id) FILTER(WHERE ps.skill_id IN ({marks})) AS matched_count
            FROM cohort p LEFT JOIN skillmarket_app.posting_skills ps USING(posting_id) GROUP BY p.posting_id
        )'''
        params=[country,country,job_type,job_type,*ids]
        stats=query(cte+'''SELECT count(*) AS observed_postings,
            count(*) FILTER(WHERE matched_count>0) AS overlap_postings,
            count(*) FILTER(WHERE required_count>0 AND matched_count=required_count) AS full_skill_matches FROM detail''',params)[0]
        gaps=query(cte+f'''SELECT s.skill_id,s.name,count(*) AS postings,
            count(*) FILTER(WHERE d.required_count=d.matched_count+1) AS extra_full_skill_matches
            FROM detail d JOIN skillmarket_app.posting_skills ps USING(posting_id)
            JOIN skillmarket_app.skills s USING(skill_id)
            WHERE d.matched_count>0 AND ps.skill_id NOT IN ({marks})
            GROUP BY s.skill_id,s.name ORDER BY extra_full_skill_matches DESC,postings DESC,s.skill_id LIMIT 5''',params+ids)
        plan=_recommend(query,catalog,[s['skill_id'] for s in gaps]) if gaps else {
            'courses':[],'skills_without_courses':[],'skills_not_covered_by_plan':[]}
        return {'known_skills':resolved['skills'],'unresolved':resolved['unresolved'],'ambiguous':resolved['ambiguous'],
                'country':country,'job_type':job_type,'year':2025,**stats,'suggested_skills':gaps,**{
                    k:plan[k] for k in ['courses','skills_without_courses','skills_not_covered_by_plan']}}


def format_courses(result):
    lines=[]
    if result.get('skills'):
        lines.append('Gevraagde skills: '+', '.join(s['name'] for s in result['skills'])+'.')
    if result.get('known_skills'):
        lines.append('Herkenbare profielskills: '+', '.join(s['name'] for s in result['known_skills'])+'.')
        lines.append(f"In {result['year']} ({result['country']}, {result['job_type']}): "
            f"{result['overlap_postings']} vacatures met minstens één van je skills; "
            f"{result['full_skill_matches']} met volledige overlap met de gevonden parser-skills.")
        if result['suggested_skills']:
            lines.append('Vaak gevraagde ontbrekende skills bij die vacatures: '+', '.join(s['name'] for s in result['suggested_skills'])+'.')
        else:
            lines.append('Geen aanvullende skills gevonden binnen deze selectie.')
    for course in result.get('courses',[]):
        names=', '.join(s['name'] for s in course['covered_skills'])
        lines.append(f"- {course['title']} ({course['site']}): {course['url']}\n  Gekoppelde skills: {names}.")
    if result.get('skills_without_courses'):
        lines.append('Geen cursus gekoppeld in de huidige catalogus voor: '+', '.join(s['name'] for s in result['skills_without_courses'])+'.')
    if result.get('unresolved'): lines.append('Niet herkend: '+', '.join(result['unresolved'])+'. Geef de skillnaam of een vaste skill-ID.')
    if result.get('ambiguous'): lines.append('Meerdere betekenissen; specificeer: '+', '.join(result['ambiguous'])+'.')
    remaining={s['skill_id']:s for s in result.get('skills_not_covered_by_plan',[]) if 'skill_id' in s}
    for missing in result.get('skills_without_courses',[]): remaining.pop(missing.get('skill_id'),None)
    if remaining: lines.append('Nog niet gedekt door dit cursusplan: '+', '.join(s['name'] for s in remaining.values())+'.')
    if not result.get('courses'): lines.append('Geen passend cursusadvies gevonden voor deze selectie.')
    return '\n\n'.join(lines)


def course_request(question,history=None):
    """Route explicit learning targets or a course follow-up to the real tool.

    Questions describing existing skills or a role transition stay with the
    agent, which can distinguish profile advice from skills to learn.
    """
    explicit=bool(re.search(COURSE_INTENT,question,re.I))
    context=any(b.get('type')=='tool_use' and b.get('name') in {'recommend_courses','profile_advice','career_path'}
                for m in (history or []) if isinstance(m.get('content'),list)
                for b in m['content'] if isinstance(b,dict))
    follow_up=context and re.search(r'^\s*(en\b|and\b|what about\b|hoe zit)',question,re.I)
    if not explicit and not follow_up: return None
    if not explicit and re.search(r'\b(trend|grafiek|chart|demand)\b',question,re.I): return None
    if re.search(r'\b(my skills|mijn skills|ik ken|ik beheers|i know|already know)\b|skills\s*:',question,re.I): return None
    if re.search(r'\b(from|van)\b.+\b(to|naar)\b',question,re.I): return None
    deictic=bool(re.search(r'\b(hiervoor|daarvoor|deze|die|those|these|them|above)\b',question,re.I))
    tail=re.search(r'\b(?:for|voor)\s+(.+?)\??$',question,re.I)
    with read_queries() as query:
        catalog=SkillCatalog(query)
        if tail and not deictic:
            value=re.sub(r'\s+(?:te leren|to learn)\??$','',tail.group(1),flags=re.I).strip(' ?. !')
            exact=catalog.resolve([value])
            if exact['skills']: return {'skills':[value]}
            if re.search(r'[,;]|\s+(?:en|and)\s+',value,re.I):
                return {'skills':[v.strip() for v in re.split(r'[,;]|\s+(?:en|and)\s+',value,flags=re.I) if v.strip()][:20]}
        mentioned=catalog.mentions(question)
    if mentioned:
        return {'skills':[s['skill_id'] for s in mentioned]}
    if tail and not deictic:
        return {'skills':[tail.group(1).strip(' ?. !')]}
    for message in reversed(history or []):
        if not isinstance(message.get('content'),list): continue
        for block in reversed(message['content']):
            if block.get('type')!='tool_result' or block.get('is_error'): continue
            try: result=json.loads(block['content'])
            except (ValueError,TypeError): continue
            if result.get('plans'):
                values=[s['skill_id'] for s in result['plans'][0]['skills']]
            elif result.get('candidates'):
                values=[s['skill_id'] for s in result['candidates'][0]['missing_skills']]
            elif result.get('suggested_skills'):
                values=[s['skill_id'] for s in result['suggested_skills']]
            elif isinstance(result.get('data'),dict) and result['data'].get('skills_to_learn'):
                values=[s['skill'] for s in result['data']['skills_to_learn']]
            elif result.get('skills'):
                values=[s.get('skill_id',s.get('skill')) for s in result['skills']]
            elif isinstance(result.get('data'),list):
                values=[s['skill'] for s in result['data'] if isinstance(s,dict) and 'skill' in s]
            else: continue
            if values: return {'skills':list(dict.fromkeys(values))[:20]}
    return None


def profile_request(question):
    """Parse an explicit list of already-known skills; leave complex goals to the agent."""
    match=re.search(r'\b(?:my skills|mijn skills|ik ken|ik beheers|i know|already know)\b\s*:?\s*(.+)',question,re.I)
    if not match or re.search(r'\b(?:ik wil|i want)\b.+\b(?:leren|learn)\b',question,re.I): return None
    text=re.split(r'[.?]|\b(?:welke|which|wat moet|what should|ik wil|i want)\b',match.group(1),maxsplit=1,flags=re.I)[0]
    text=re.sub(r'^(?:zijn|are)\s+','',text,flags=re.I).strip(' ,;:')
    if not text: return None
    values=[v.strip() for v in re.split(r'[,;]|\s+(?:en|and)\s+',text,flags=re.I) if v.strip()]
    country='ALL'
    key=' '+normalize(question)+' '
    explicit=re.search(r'\b(BE|NL|LU|GB|UK)\b',question)
    if explicit: country='GB' if explicit.group(1)=='UK' else explicit.group(1)
    for code,names in {'BE':['belgie','belgium'],'NL':['nederland','netherlands'],
                       'LU':['luxemburg','luxembourg'],'GB':['united kingdom','verenigd koninkrijk']}.items():
        if any(' '+name+' ' in key for name in names): country=code
    job='ALL'
    groups=['risk and compliance','finance and accounting','customer service and support','sales and marketing',
            'product and project management','human resources','operations','technology','legal']
    for value in groups:
        if normalize(value) in key: job=value; break
    return {'skills':values,'country':country,'job_type':job}
