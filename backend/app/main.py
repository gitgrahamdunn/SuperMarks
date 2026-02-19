"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import create_db_and_tables
from app.routers.exams import router as exams_router
from app.routers.questions import router as questions_router
from app.routers.submissions import router as submissions_router
from app.settings import settings
from app.storage import ensure_dir

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(exams_router)
app.include_router(questions_router)
app.include_router(submissions_router)


@app.on_event("startup")
def on_startup() -> None:
    ensure_dir(settings.data_path)
    create_db_and_tables()


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
