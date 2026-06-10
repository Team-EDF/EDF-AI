from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import merchant, ocr

app = FastAPI(title="GreenStep AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(merchant.router, prefix="/api", tags=["classify"])
app.include_router(ocr.router, prefix="/api", tags=["ocr"])

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")


@app.get("/health")
def health_check():
    return {"status": "ok"}
