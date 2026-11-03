"""Write an HTML gallery of all chart specs built from the stub data, then open it.

Usage: python -m visualization.preview
Charts are rendered in the browser with vega-embed (loaded from a CDN), so an
internet connection is needed when viewing the page.
"""

import json
import webbrowser
from pathlib import Path

from visualization import api

OUT = Path(__file__).parent / "preview.html"

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Visualization preview</title>
<script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
<style>
  body{font-family:"Segoe UI",Helvetica,Arial,sans-serif;margin:0;background:#f3f4f6;color:#111827}
  header{background:#111827;color:white;padding:28px 40px}
  header h1{margin:0 0 6px;font-size:24px;font-weight:600}
  header p{margin:0;color:#9CA3AF;font-size:14px}
  main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%%,720px),1fr));gap:24px;padding:32px 40px}
  .card{background:white;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08),0 8px 24px rgba(0,0,0,.04);padding:24px 24px 16px;min-width:0;overflow:hidden}
  .card > div{overflow-x:auto}
  .card h2{margin:0 0 4px;font-size:15px;font-weight:600;color:#2563EB;text-transform:uppercase;letter-spacing:.06em}
  .card p.note{margin:0 0 12px;color:#6B7280;font-size:13px}
</style>
</head><body>
<header>
  <h1>Skill demand advisor: visualization preview</h1>
  <p>Synthetic stub data. Charts are rendered with vega-embed from a CDN.</p>
</header>
<main>
%s
</main>
<script>
%s
</script>
</body></html>
"""


def build_gallery() -> list[tuple[str, str, dict]]:
    job = "technology"
    return [
        (
            "Skill share over time",
            "Top 5 skills by average share. Pass explicit skills to compare others.",
            api.skill_trends(job)["chart"],
        ),
        (
            "Skill postings over time",
            "Raw posting counts for the top 5 skills.",
            api.skill_trends(job, metric="postings")["chart"],
        ),
        (
            "Top skills",
            "Ranking of the 10 most demanded skills over the full period.",
            api.top_skills(job, n=10)["chart"],
        ),
        (
            "Rising and falling skills",
            "Biggest share movers in the last 6 months. Skills under 2% share are ignored.",
            api.rising_and_falling_skills(job, n=4)["chart"],
        ),
    ]


def main():
    charts = build_gallery()
    cards = "\n".join(
        f'<section class="card"><h2>{name}</h2><p class="note">{note}</p>'
        f'<div id="c{i}"></div></section>'
        for i, (name, note, _) in enumerate(charts)
    )
    calls = "\n".join(
        f'vegaEmbed("#c{i}", {json.dumps(spec)}, {{actions: false, renderer: "svg"}});'
        for i, (_, _, spec) in enumerate(charts)
    )
    OUT.write_text(HTML % (cards, calls), encoding="utf-8")
    webbrowser.open(OUT.resolve().as_uri())
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
