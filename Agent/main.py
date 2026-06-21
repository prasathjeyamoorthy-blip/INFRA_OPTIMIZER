"""
main.py — FastAPI application entry point.

Creates the FastAPI app, attaches CORS middleware, includes the API router,
and runs the uvicorn server when executed directly.

Environment variables (loaded from .env via python-dotenv):
  API_HOST  — bind address (default: 0.0.0.0)
  API_PORT  — bind port    (default: 8000)
"""

import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import router
from db import init_db

# ---------------------------------------------------------------------------
# Load .env at module level so os.getenv() picks up all configured values
# ---------------------------------------------------------------------------
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(title="Cost Optimizer Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# ---------------------------------------------------------------------------
# Startup event — initialise the database (creates tables on first run)
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
