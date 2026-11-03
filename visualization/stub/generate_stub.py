"""Generate synthetic stub data for the visualization layer.

This is NOT real vacancy data. It only exists so the visualization code can be
developed before the database is available. Re-run with: python -m visualization.stub.generate_stub
"""

from pathlib import Path

import numpy as np
import pandas as pd

STUB_DIR = Path(__file__).parent
SEED = 42
MONTHS = pd.date_range("2024-01-01", "2026-08-01", freq="MS")

JOB_TYPES = {
    "Software Engineer": 1400,
    "Data Analyst": 700,
    "Risk Manager": 500,
    "Compliance Officer": 450,
}

# skill -> (base share of postings, linear yearly trend in share points)
SKILLS = {
    "Python": (0.30, 0.05),
    "SQL": (0.25, 0.00),
    "Java": (0.20, -0.04),
    "Kubernetes": (0.10, 0.04),
    "Machine Learning": (0.08, 0.03),
    "Excel": (0.22, -0.05),
    "Power BI": (0.12, 0.02),
    "Communication": (0.35, 0.00),
    "AML": (0.10, -0.01),
    "KYC": (0.12, 0.02),
}


def main():
    rng = np.random.default_rng(SEED)
    rows, totals = [], []
    for job_type, base_total in JOB_TYPES.items():
        for i, month in enumerate(MONTHS):
            total = int(base_total * rng.uniform(0.85, 1.15))
            totals.append(
                {"job_type": job_type, "month": month.date(), "total_postings": total}
            )
            years = i / 12
            for skill, (base, trend) in SKILLS.items():
                share = np.clip(base + trend * years + rng.normal(0, 0.01), 0.01, 0.9)
                rows.append(
                    {
                        "job_type": job_type,
                        "month": month.date(),
                        "skill": skill,
                        "postings": round(total * share),
                    }
                )
    pd.DataFrame(rows).to_csv(STUB_DIR / "skill_demand.csv", index=False)
    pd.DataFrame(totals).to_csv(STUB_DIR / "monthly_totals.csv", index=False)


if __name__ == "__main__":
    main()
