DELETE FROM skillmarket_app.monthly_totals;
INSERT INTO skillmarket_app.monthly_totals
WITH counts AS (
    SELECT job_type, date_trunc('month',date_posted)::DATE AS month, count(*) AS n
    FROM skillmarket_app.postings
    WHERE parse_status='ok' AND date_posted IS NOT NULL
    GROUP BY job_type, month
), periods AS (
    SELECT job_type, unnest(generate_series(min(month), max(month), INTERVAL '1 month'))::DATE AS month
    FROM counts GROUP BY job_type
)
SELECT p.job_type,p.month,coalesce(c.n,0)
FROM periods p LEFT JOIN counts c USING (job_type,month);

DELETE FROM skillmarket_app.skill_demand;
INSERT INTO skillmarket_app.skill_demand
WITH counts AS (
    SELECT p.job_type,date_trunc('month',p.date_posted)::DATE AS month,s.name AS skill,count(*) AS n
    FROM skillmarket_app.posting_skills ps
    JOIN skillmarket_app.postings p USING (posting_id)
    JOIN skillmarket_app.skills s USING (skill_id)
    WHERE p.parse_status='ok' AND p.date_posted IS NOT NULL
    GROUP BY p.job_type,month,s.name
), job_skills AS (
    SELECT DISTINCT job_type,skill FROM counts
)
SELECT t.job_type,t.month,s.skill,coalesce(c.n,0)
FROM skillmarket_app.monthly_totals t JOIN job_skills s USING (job_type)
LEFT JOIN counts c USING (job_type,month,skill);
