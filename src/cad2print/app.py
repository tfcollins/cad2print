"""FastAPI application factory and dependency wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .db import Database
from .jobs import JobRunner
from .onshape import OnshapeClient
from .presets import PresetConfig, load_presets
from .slicer import run as real_slice

_HERE = Path(__file__).parent


def create_app(settings: Settings | None = None, *, onshape=None, presets: PresetConfig | None = None,
               slice_fn=real_slice) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    onshape = onshape or OnshapeClient(settings.onshape_access_key, settings.onshape_secret_key,
                                       settings.onshape_base_url)
    presets = presets or load_presets(settings.profiles_file)
    db = Database(settings.db_path)
    runner = JobRunner(db=db, presets=presets, onshape=onshape, work_dir=settings.work_dir,
                       handoff_dir=settings.handoff_dir, orca_bin=settings.orca_bin,
                       use_xvfb=settings.use_xvfb, slice_timeout_s=settings.slice_timeout_s,
                       tail_lines=settings.log_tail_lines, slice_fn=slice_fn)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner.start()
        try:
            yield
        finally:
            runner.stop()
            db.close()

    app = FastAPI(title="cad2print", lifespan=lifespan)
    app.state.settings = settings
    app.state.onshape = onshape
    app.state.presets = presets
    app.state.db = db
    app.state.runner = runner
    app.state.templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    from .routes import browse, jobs  # noqa: E402  (import after state exists)

    app.include_router(browse.router)
    app.include_router(jobs.router)
    return app


def factory() -> FastAPI:
    """Entry point for `uvicorn cad2print.app:factory --factory`."""
    return create_app()


def render(request: Request, name: str, **ctx):
    """Render a template with the app-wide context every page needs."""
    templates: Jinja2Templates = request.app.state.templates
    status_code = ctx.pop("status_code", 200)
    ctx.setdefault("presets", request.app.state.presets)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
