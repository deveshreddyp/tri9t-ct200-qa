from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.routers import documents, generations, nodes, selections


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    init_db()
    yield


app = FastAPI(
    title="CardioTrack CT-200 QA Traceability System",
    description="Backend API for medical device manual QA tracing.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(documents.router)
app.include_router(nodes.router)
app.include_router(selections.router)
app.include_router(generations.router)


@app.get("/")
def read_root():
    return {"message": "Welcome to the CardioTrack CT-200 QA Traceability API"}
