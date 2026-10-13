"""FastAPI app: JSON API for the tracker app, plus the built web app itself."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from healthtracker.api.routes import body, coach, food, goals, settings, training
from healthtracker.config import get_settings
from healthtracker.db import current_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Health & Training Tracker", version="0.1.0")
for r in (settings.router, goals.router, food.router, training.router, body.router, coach.router):
    app.include_router(r)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "database": current_url().rsplit("/", 1)[-1]}


dist: Path = get_settings().web_dist
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        file = dist / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(dist / "index.html")
