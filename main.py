from fastapi import FastAPI
from app.api.routes import merchant

app = FastAPI(title="GreenStep AI API", version="1.0.0")

app.include_router(merchant.router, prefix="/api", tags=["classify"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
