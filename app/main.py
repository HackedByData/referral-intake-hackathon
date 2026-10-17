"""FastAPI entrypoint: uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.api import audit as audit_api
from app.api import beds as beds_api
from app.api import criteria as criteria_api
from app.api import referrals as referrals_api
from app.beds.seed import seed_demo_beds, seed_poc_beds
from app.config import get_settings
from app.db import get_engine, init_db
from app.workflow.pipeline import mark_interrupted

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with Session(get_engine()) as s:
        mode = get_settings().seed_mode
        if mode == "demo":
            seed_demo_beds(s)
        elif mode == "poc":
            seed_poc_beds(s)
        mark_interrupted(s)
    yield


app = FastAPI(title="Raft: IHH referral intake (prototype)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(referrals_api.router)
app.include_router(beds_api.router)
app.include_router(audit_api.router)
app.include_router(criteria_api.router)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "model": s.model, "api_key_configured": bool(s.anthropic_api_key)}


# Coordinator UI (web/, plain HTML/JS). Mounted last so /api routes win.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
