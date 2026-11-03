"""Web server for Skill Market.

Run from this folder:
    uvicorn server:app --host 0.0.0.0 --port 8000
Then open http://localhost:8000
"""

import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ai_layer import ask

HERE = Path(__file__).parent
MAX_HISTORY = 20

app = FastAPI()
sessions = {}  # session_id -> list of messages, kept in memory


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None


class ResetRequest(BaseModel):
    session_id: str


def trim_history(messages):
    """Keep the last MAX_HISTORY messages, starting at a plain user question.

    Starting mid-exchange would leave a tool_result with no matching tool_use,
    which the API rejects.
    """
    kept = messages[-MAX_HISTORY:]
    for i, msg in enumerate(kept):
        if msg["role"] == "user" and isinstance(msg["content"], str):
            return kept[i:]
    return []


@app.get("/")
def index():
    return FileResponse(HERE / "index.html")


@app.post("/api/ask")
def api_ask(req: AskRequest):
    sid = req.session_id or str(uuid.uuid4())
    history = sessions.get(sid)
    try:
        result = ask(req.question, history)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")
    sessions[sid] = trim_history(result["messages"])
    return {
        "session_id": sid,
        "answer": result["answer"],
        "charts": result["charts"],
        "usage": result["usage"],
    }


@app.post("/api/reset")
def api_reset(req: ResetRequest):
    sessions.pop(req.session_id, None)
    return {"ok": True}
