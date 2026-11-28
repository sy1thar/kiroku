import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.routers import api, web
from app.seed import seed_if_empty
from app.services.loop_manager import run_startup_repair


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed_if_empty(db)
    repair_task = None
    if get_settings().app_env == "production":
        repair_task = asyncio.create_task(run_startup_repair())
    try:
        yield
    finally:
        if repair_task and not repair_task.done():
            repair_task.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        description="Global Cross-Media & Character Encyclopedia",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    application.include_router(api.router)
    application.include_router(web.router)
    return application


app = create_app()
