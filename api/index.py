"""Vercel ASGI function entrypoint for the SuperMarks backend."""

from fastapi import FastAPI

from app.main import app as inner_app

app = FastAPI()
app.mount("/api", inner_app)
