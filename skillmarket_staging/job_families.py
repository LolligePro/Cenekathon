"""Small keyword classifier matching the parser's broad job-type categories.

Applied to distinct normalized titles only. The original normalized title is kept.
"""

JOB_RULES = [
    ('risk and compliance','risk|compliance|compliant|aml|kyc|audit|auditor|regulatory|fraud|sanctions'),
    ('legal','legal|lawyer|counsel|paralegal|solicitor|attorney'),
    ('customer service and support','customer|client|support|helpdesk|service|call|contact'),
    ('sales and marketing','sales|marketing|broker|advisor|adviser|account|relationship'),
    ('technology','software|developer|developers|programmer|devops|engineer|engineers|architect|cloud|sre|cyber|cybersecurity|it|data|analytics'),
    ('finance and accounting','finance|financial|accountant|accounting|accounts|controller|treasury|tax|actuary|actuarial|underwriter|underwriting|credit|payments|payroll'),
    ('product and project management','product|project|programme|program|scrum|agile|delivery|transformation'),
    ('human resources','hr|human|recruiter|recruitment|talent|people|learning|training'),
    ('operations','operations|operator|processing|claims|administrator|administration|admin|clerk|officer|operational'),
]


def classification_sql():
    clauses=[f"WHEN regexp_matches(lower(normalized_title),'(^|[^[:alnum:]_])({words})([^[:alnum:]_]|$)') THEN '{label}'"
             for label,words in JOB_RULES]
    return 'CASE ' + ' '.join(clauses) + " ELSE 'other' END"
