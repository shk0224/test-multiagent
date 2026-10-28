"""TripMate AI FastAPI application.

Chat history is stored by LangGraph's PostgreSQL checkpointer (backend.py).
This file keeps a separate PostgreSQL index of which anonymous browser session
owns each conversation. A thread_id alone never grants access to chat history.

For production across multiple workers, add per-thread execution locking and
user accounts if cross-device access is required.
"""

import os
import secrets
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from backend import get_database_url, get_travel_history, run_travel_agent

BASE_DIR = Path(__file__).resolve().parent
COOKIE_NAME = "tripmate_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

app = FastAPI(
    title="TripMate AI",
    description="LangGraph Multi-Agent Travel Planner with FastAPI Frontend",
    version="1.1.0",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str
    thread_id: str | None = None


def db_connection():
    """Use a fresh connection for each short database operation."""
    return psycopg.connect(get_database_url())


@app.on_event("startup")
def initialize_chat_index():
    """Create an ownership index; LangGraph manages its own checkpoint tables."""
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tripmate_conversations (
                    thread_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS tripmate_conversations_session_idx
                ON tripmate_conversations (session_id, updated_at DESC)
            """)


def get_session_id(request: Request, response: Response) -> str:
    """Assign an unguessable session cookie; ignore user-supplied thread IDs for identity."""
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id or len(session_id) != 64 or any(c not in "0123456789abcdef" for c in session_id):
        session_id = secrets.token_hex(32)
        response.set_cookie(
            key=COOKIE_NAME,
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
            max_age=COOKIE_MAX_AGE,
            path="/",
        )
    return session_id


def require_owned_thread(thread_id: str, session_id: str):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM tripmate_conversations WHERE thread_id = %s AND session_id = %s",
                (thread_id, session_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Conversation not found.")


def create_conversation(session_id: str, title: str = "New Trip") -> str:
    thread_id = f"user_{uuid.uuid4().hex}"
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tripmate_conversations (thread_id, session_id, title) VALUES (%s, %s, %s)",
                (thread_id, session_id, title[:120]),
            )
    return thread_id


def update_conversation(thread_id: str, session_id: str, title: str | None = None):
    with db_connection() as conn:
        with conn.cursor() as cur:
            if title is None:
                cur.execute(
                    "UPDATE tripmate_conversations SET updated_at = NOW() WHERE thread_id = %s AND session_id = %s",
                    (thread_id, session_id),
                )
            else:
                cur.execute(
                    "UPDATE tripmate_conversations SET title = %s, updated_at = NOW() WHERE thread_id = %s AND session_id = %s",
                    (title[:120], thread_id, session_id),
                )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    response = templates.TemplateResponse(request=request, name="index.html", context={})
    get_session_id(request, response)
    return response


@app.post("/api/travel")
def travel_planner(request_data: TravelRequest, request: Request, response: Response):
    session_id = get_session_id(request, response)
    user_message = request_data.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(user_message) > 10000:
        raise HTTPException(status_code=400, detail="Message is too long.")

    if request_data.thread_id:
        thread_id = request_data.thread_id
        require_owned_thread(thread_id, session_id)
        is_new = False
    else:
        thread_id = create_conversation(session_id, user_message[:80])
        is_new = True

    try:
        result = run_travel_agent(user_input=user_message, thread_id=thread_id)
        update_conversation(thread_id, session_id)
        return {
            "success": True,
            "thread_id": result["thread_id"],
            "answer": result["answer"],
            "flight_results": result["flight_results"],
            "hotel_results": result["hotel_results"],
            "itinerary": result["itinerary"],
            "llm_calls": result["llm_calls"],
        }
    except Exception:
        traceback.print_exc()
        # Do not leak database credentials or provider details to browser.
        raise HTTPException(status_code=500, detail="Unable to generate the travel plan. Please try again.")


@app.get("/api/chats")
def list_chats(request: Request, response: Response):
    session_id = get_session_id(request, response)
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT thread_id, title, created_at, updated_at
                   FROM tripmate_conversations WHERE session_id = %s
                   ORDER BY updated_at DESC LIMIT 100""",
                (session_id,),
            )
            rows = cur.fetchall()
    return {"success": True, "chats": [
        {"thread_id": r[0], "title": r[1], "created_at": r[2].isoformat(), "updated_at": r[3].isoformat()}
        for r in rows
    ]}


@app.get("/api/chats/{thread_id}")
def chat_detail(thread_id: str, request: Request, response: Response):
    session_id = get_session_id(request, response)
    require_owned_thread(thread_id, session_id)
    return {"success": True, "thread_id": thread_id, "messages": get_travel_history(thread_id)}


@app.get("/api/history")
def chat_history(request: Request, response: Response, thread_id: str):
    """Optional alias for frontend code that expects /api/history."""
    return chat_detail(thread_id, request, response)


@app.post("/api/new-chat")
def new_chat(request: Request, response: Response):
    session_id = get_session_id(request, response)
    thread_id = create_conversation(session_id)
    return {"success": True, "thread_id": thread_id, "messages": []}


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "AI Travel Planner API is running"}


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
