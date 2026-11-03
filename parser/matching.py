"""Shared text cleaning, skill matching and job-type rules."""

import html
import json
import re
from pathlib import Path

PARSER_DIR = Path(__file__).resolve().parent
DATA_DIR = PARSER_DIR.parent / "data"
TAXONOMY_PATH = PARSER_DIR / "taxonomy.json"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"\w[\w+#.]*[\w+#]|\w")

JOB_TYPE_RULES = [
    ("risk and compliance", {"risk", "compliance", "compliant", "aml", "kyc", "audit", "auditor", "regulatory", "fraud", "sanctions"}),
    ("legal", {"legal", "lawyer", "counsel", "paralegal", "solicitor", "attorney"}),
    ("customer service and support", {"customer", "client", "support", "helpdesk", "service", "call", "contact"}),
    ("sales and marketing", {"sales", "marketing", "broker", "advisor", "adviser", "account", "relationship"}),
    ("technology", {"software", "developer", "developers", "programmer", "devops", "engineer", "engineers", "architect", "cloud", "sre", "cyber", "cybersecurity", "it", "data", "analytics"}),
    ("finance and accounting", {"finance", "financial", "accountant", "accounting", "accounts", "controller", "treasury", "tax", "actuary", "actuarial", "underwriter", "underwriting", "credit", "payments", "payroll"}),
    ("product and project management", {"product", "project", "programme", "program", "scrum", "agile", "delivery", "transformation"}),
    ("human resources", {"hr", "human", "recruiter", "recruitment", "talent", "people", "learning", "training"}),
    ("operations", {"operations", "operator", "processing", "claims", "administrator", "administration", "admin", "clerk", "officer", "operational"}),
]
JOB_TYPES = [name for name, _ in JOB_TYPE_RULES] + ["other"]


def clean_text(value):
    if value is None:
        return ""
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", str(value))))


def job_type(title):
    tokens = set(TOKEN_RE.findall(clean_text(title).lower()))
    for name, keywords in JOB_TYPE_RULES:
        if tokens & keywords:
            return name
    return "other"


def load_taxonomy(path=TAXONOMY_PATH):
    data = json.loads(Path(path).read_text())
    aliases = data["aliases"]
    first_tokens = {key.split()[0] for key in aliases}
    return aliases, data["max_tokens"], first_tokens


def find_skills(text, aliases, max_tokens, first_tokens):
    tokens = TOKEN_RE.findall(text.lower())
    found = set()
    for i, token in enumerate(tokens):
        if token not in first_tokens:
            continue
        for n in range(1, min(max_tokens, len(tokens) - i) + 1):
            name = aliases.get(" ".join(tokens[i:i + n]))
            if name:
                found.add(name)
    return sorted(found)
