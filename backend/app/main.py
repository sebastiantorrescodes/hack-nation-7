from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import store
from .config import FRONTEND_ORIGIN
from .llm import ReasoningAPIError
from .routes import capture, tutor, voice, workflows, workmaps


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.init()
    yield


app = FastAPI(title="AI Apprentice", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    # The side panel runs at chrome-extension://<extension id>
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ReasoningAPIError)
async def llm_error_handler(_: Request, exc: ReasoningAPIError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


app.include_router(voice.router)
app.include_router(workflows.router)
app.include_router(capture.router)
app.include_router(workmaps.router)
app.include_router(tutor.router)


@app.get("/api/health")
def health():
    return {"ok": True}
