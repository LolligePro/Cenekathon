"""HTTP routes and dashboard page for the visualization API.

Mounted by server.py. `GET /dashboard` serves the page; `GET /api/viz/{name}` calls the
`api.py` function `name` with the query-string arguments (comma-separated for lists).
"""

import inspect
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from visualization import api

router = APIRouter()
# Only these functions are reachable over HTTP.
EXPOSED = {
    f.__name__: f
    for f in (
        api.list_job_types,
        api.skill_trends,
        api.top_skills,
        api.job_type_skills,
        api.skill_trend,
        api.rising_and_falling_skills,
        api.career_path,
    )
}


def _clean(value):
    """Make a result JSON-safe: NaN/inf become null."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _coerce(param: inspect.Parameter, raw: str):
    annotation = str(param.annotation)
    if "list" in annotation:
        return [s.strip() for s in raw.split(",") if s.strip()]
    if param.annotation is int:
        return int(raw)
    if param.annotation is float:
        return float(raw)
    return raw


@router.get("/dashboard")
def dashboard():
    return FileResponse(Path(__file__).parent / "dashboard.html")


@router.get("/api/viz/{name}")
def call(name: str, request: Request):
    fn = EXPOSED.get(name)
    if fn is None:
        raise HTTPException(404, f"Unknown visualization {name!r}")
    params = inspect.signature(fn).parameters
    kwargs = {}
    for key, raw in request.query_params.items():
        if key not in params:
            raise HTTPException(400, f"Unknown parameter {key!r}")
        if raw == "":
            continue
        try:
            kwargs[key] = _coerce(params[key], raw)
        except ValueError:
            raise HTTPException(400, f"Invalid value for {key!r}: {raw!r}")
    try:
        return _clean(fn(**kwargs))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
