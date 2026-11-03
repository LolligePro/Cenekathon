"""Vega-Lite chart specs. Returned as plain dicts so any frontend can render them."""

import pandas as pd

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

PALETTE = [
    "#2563EB", "#F97316", "#16A34A", "#DC2626", "#0891B2",
    "#9333EA", "#CA8A04", "#DB2777", "#4B5563", "#65A30D",
]
INK = "#111827"
MUTED = "#6B7280"
GRID = "#EEF0F3"
ACCENT = "#2563EB"
RISING = "#16A34A"
FALLING = "#DC2626"
FLAT = "#9CA3AF"

THEME = {
    "background": "white",
    "font": "Segoe UI, Helvetica, Arial, sans-serif",
    "view": {"stroke": None},
    "range": {"category": PALETTE},
    "title": {
        "anchor": "start",
        "fontSize": 18,
        "fontWeight": 600,
        "color": INK,
        "subtitleColor": MUTED,
        "subtitleFontSize": 13,
        "subtitleFontWeight": 400,
        "offset": 16,
    },
    "axis": {
        "labelColor": MUTED,
        "labelFontSize": 12,
        "titleColor": INK,
        "titleFontSize": 12,
        "titleFontWeight": 500,
        "titlePadding": 12,
        "gridColor": GRID,
        "domain": False,
        "tickColor": "#D1D5DB",
    },
    "legend": {
        "labelColor": MUTED,
        "labelFontSize": 12,
        "titleColor": INK,
        "titleFontSize": 12,
    },
}


def _title(text: str, subtitle: str) -> dict:
    return {"text": text, "subtitle": subtitle}


def _records(df: pd.DataFrame) -> list[dict]:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return out.astype(object).where(pd.notna(out), None).to_dict("records")


def skill_trend_chart(shares: pd.DataFrame, job_type: str, metric: str = "share") -> dict:
    """Line chart: one line per skill over time. metric is 'share' or 'postings'.

    Skills are labelled at their last point instead of using a legend.
    """
    if metric not in ("share", "postings"):
        raise ValueError("metric must be 'share' or 'postings'")
    if metric == "share":
        axis_title, axis_format, tip_format = "Share of monthly postings", ".0%", ".1%"
        subtitle = "How often each skill appears in postings for this job type, per month"
    else:
        axis_title, axis_format, tip_format = "Postings per month", ",.0f", ",.0f"
        subtitle = "Number of postings mentioning each skill, per month"

    color = {
        "field": "skill",
        "type": "nominal",
        "legend": None,
        "scale": {"scheme": "tableau10"},
    }
    x = {
        "field": "month",
        "type": "temporal",
        "title": "Month",
        "axis": {"format": "%b %y", "labelAngle": 0, "tickCount": "month"},
    }
    y = {
        "field": metric,
        "type": "quantitative",
        "title": axis_title,
        "axis": {"format": axis_format},
    }
    lines = {
        "mark": {"type": "line", "strokeWidth": 2.5, "interpolate": "monotone"},
        "encoding": {
            "x": x,
            "y": y,
            "color": color,
            "tooltip": [
                {"field": "month", "type": "temporal", "title": "Month", "format": "%B %Y"},
                {"field": "skill", "type": "nominal", "title": "Skill"},
                {"field": metric, "type": "quantitative", "title": axis_title, "format": tip_format},
            ],
        },
    }
    last_only = [
        {"joinaggregate": [{"op": "max", "field": "month", "as": "last_month"}], "groupby": ["skill"]},
        {"filter": "datum.month == datum.last_month"},
    ]
    last_point = {
        "transform": last_only,
        "mark": {"type": "circle", "size": 60},
        "encoding": {"x": x, "y": y, "color": color},
    }
    end_labels = {
        "transform": last_only,
        "mark": {"type": "text", "align": "left", "dx": 10, "fontSize": 12, "fontWeight": 500, "baseline": "middle"},
        "encoding": {
            "x": x,
            "y": y,
            "color": color,
            "text": {"field": "skill", "type": "nominal"},
        },
    }
    # Labels for skills that finish at nearly the same value would overlap; spread them
    # by rank so each stays readable without changing the plotted data.
    end_labels["mark"]["dy"] = {"expr": "datum.label_dy"}
    end_labels["transform"] = last_only + [
        {"window": [{"op": "rank", "as": "label_rank"}], "sort": [{"field": metric, "order": "descending"}]},
        {"calculate": "(datum.label_rank - 1) * 13 - 13", "as": "label_dy"},
    ]
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": _title(f"{job_type}: skill demand over time", subtitle),
        "config": THEME,
        "width": 580,
        "height": 380,
        "padding": {"right": 110, "top": 4},
        "data": {"values": _records(shares)},
        "layer": [lines, last_point, end_labels],
    }


def top_skills_chart(top: pd.DataFrame, job_type: str) -> dict:
    """Horizontal bar chart of the top skills by average share, with value labels."""
    max_share = float(top["avg_share"].max()) if not top.empty else 1.0
    skill_axis = {
        "field": "skill",
        "type": "nominal",
        "sort": "-x",
        "title": None,
        "axis": {"labelFontSize": 13, "labelColor": INK, "ticks": False, "domain": False},
    }
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": _title(
            f"{job_type}: most demanded skills",
            "Skills with the highest average share of postings over the period",
        ),
        "config": THEME,
        "width": 620,
        "height": {"step": 32},
        "data": {"values": _records(top)},
        "layer": [
            {
                "mark": {"type": "bar", "cornerRadiusEnd": 4, "size": 20, "color": ACCENT},
                "encoding": {
                    "y": skill_axis,
                    "x": {
                        "field": "avg_share",
                        "type": "quantitative",
                        "title": "Average share of postings",
                        "scale": {"domain": [0, round(max_share * 1.18, 4)]},
                        "axis": {"format": ".0%", "grid": True, "tickCount": 6},
                    },
                    "tooltip": [
                        {"field": "skill", "type": "nominal", "title": "Skill"},
                        {"field": "avg_share", "type": "quantitative", "title": "Average share", "format": ".1%"},
                        {"field": "postings", "type": "quantitative", "title": "Total postings", "format": ","},
                    ],
                },
            },
            {
                "mark": {"type": "text", "align": "left", "dx": 6, "fontSize": 12, "color": INK, "fontWeight": 500},
                "encoding": {
                    "y": skill_axis,
                    "x": {"field": "avg_share", "type": "quantitative"},
                    "text": {"field": "avg_share", "type": "quantitative", "format": ".1%"},
                },
            },
        ],
    }


def change_chart(rising_falling: dict, job_type: str, window_months: int = 6) -> dict:
    """Diverging bar chart of relative share change, anchored at zero."""
    rows = rising_falling["rising"] + rising_falling["falling"]
    df = pd.DataFrame(rows, columns=["skill", "relative_change", "direction"])
    order = df.sort_values("relative_change", ascending=False)["skill"].tolist()
    pad = max(0.05, float(df["relative_change"].abs().max()) * 1.35) if not df.empty else 0.05
    bars = {
        "mark": {"type": "bar", "cornerRadiusEnd": 4, "size": 20},
        "encoding": {
            "y": {
                "field": "skill",
                "type": "nominal",
                "sort": order,
                "title": None,
                "axis": {"labelFontSize": 13, "labelColor": INK, "ticks": False, "domain": False, "labelPadding": 10},
            },
            "x": {
                "field": "relative_change",
                "type": "quantitative",
                "title": "Relative change in share",
                "scale": {"domain": [-round(pad, 4), round(pad, 4)], "nice": False},
                "axis": {"format": "+.0%", "grid": True, "tickCount": 7},
            },
            "color": {
                "field": "direction",
                "type": "nominal",
                "scale": {"domain": ["rising", "flat", "falling"], "range": [RISING, FLAT, FALLING]},
                "legend": None,
            },
            "tooltip": [
                {"field": "skill", "type": "nominal", "title": "Skill"},
                {"field": "direction", "type": "nominal", "title": "Trend"},
                {"field": "relative_change", "type": "quantitative", "title": "Change", "format": "+.1%"},
            ],
        },
    }
    labels = {
        "mark": {"type": "text", "fontSize": 12, "fontWeight": 500, "color": INK, "dx": {"expr": "datum.relative_change >= 0 ? 6 : -6"}, "align": {"expr": "datum.relative_change >= 0 ? 'left' : 'right'"}},
        "encoding": {
            "y": {"field": "skill", "type": "nominal"},
            "x": {"field": "relative_change", "type": "quantitative"},
            "text": {"field": "relative_change", "type": "quantitative", "format": "+.1%"},
        },
    }
    zero_rule = {
        "mark": {"type": "rule", "color": "#9CA3AF", "strokeWidth": 1},
        "encoding": {"x": {"datum": 0, "type": "quantitative"}},
    }
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": _title(
            f"{job_type}: fastest changing skills",
            f"Change in share of postings: latest {window_months} months vs the {window_months} months before",
        ),
        "config": THEME,
        "width": 620,
        "height": {"step": 32},
        "data": {"values": _records(df)},
        "layer": [zero_rule, bars, labels],
    }
