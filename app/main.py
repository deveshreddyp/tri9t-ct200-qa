from fastapi import FastAPI
from app.routers import documents, nodes, selections, generations

app = FastAPI(
    title="CardioTrack CT-200 QA Traceability System",
    description="Backend API for medical device manual QA tracing.",
    version="0.1.0"
)

app.include_router(documents.router)
app.include_router(nodes.router)
app.include_router(selections.router)
app.include_router(generations.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to the CardioTrack CT-200 QA Traceability API"}
