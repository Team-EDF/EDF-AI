from fastapi import FastAPI
from app.api.routes import carbon, merchant

app = FastAPI(title="GreenStep AI API", version="1.0.0")

app.include_router(carbon.router, prefix="/api/carbon", tags=["carbon"])
app.include_router(merchant.router, prefix="/api/merchant", tags=["merchant"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
