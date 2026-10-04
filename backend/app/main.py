from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from postgrest.exceptions import APIError
import os
from . import access, rules
from .knowledge import KnowledgeError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import store
from .config import FRONTEND_ORIGIN, SUPABASE_URL
from .llm import ReasoningAPIError
from .routes import apprentice, capture, tutor, voice, workflows, workmaps
from .speech import SpeechError


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.init()
    yield


app = FastAPI(title="AI Apprentice", lifespan=lifespan)
app.middleware("http")(access.protect)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    # The side panel runs at chrome-extension://<extension id>
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(voice.router)
app.include_router(apprentice.router)
app.include_router(workflows.router)
app.include_router(capture.router)
app.include_router(workmaps.router)
app.include_router(tutor.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.exception_handler(ReasoningAPIError)
@app.exception_handler(SpeechError)
async def provider_error(_: Request, exc: RuntimeError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(httpx.RequestError)
async def connection_error(_: Request, exc: httpx.RequestError):
    # HTTP exception strings can contain URLs or private request data. Never echo them.
    try:
        host = exc.request.url.host
    except RuntimeError:
        host = None
    if host and host == urlsplit(SUPABASE_URL).hostname:
        detail = (
            "The database request could not be completed. Check the backend's network connection, "
            "then refresh the workflow list before retrying."
        )
    elif host and host.endswith("elevenlabs.io"):
        detail = "The voice service could not be reached. Check the backend's network connection and retry."
    else:
        detail = "A required service could not be reached. Check the backend's network connection and retry."
    # No automatic replay: a failed response to a write may still have stored data.
    return JSONResponse(status_code=503, content={"detail": detail})


@app.exception_handler(APIError)
async def database_error(_: Request, exc: APIError):
    message = str(getattr(exc, 'message', ''))
    code = getattr(exc, 'code', '')
    if code in {'PGRST202','PGRST204','PGRST205','42P01','42703'}:
        return JSONResponse(status_code=503,content={'detail':'The database needs migrations 003 and 004. The current server can keep running until they are applied.'})
    conflicts = {'revision_conflict','version_conflict','identity_conflict','capture_closed','stale_draft','rejected_version'}
    if any(word in message for word in conflicts):
        return JSONResponse(status_code=409,content={'detail':'This session or version changed. Refresh before retrying; recorded evidence is retained.'})
    return JSONResponse(status_code=422,content={'detail':'The database rejected this write. Check evidence, complete review and refresh the current version.'})


@app.exception_handler(KnowledgeError)
async def knowledge_error(_: Request, exc: KnowledgeError):
    return JSONResponse(status_code=422,content={'detail':str(exc)})


@app.exception_handler(rules.UnknownRecord)
async def unknown_record(_: Request, exc: rules.UnknownRecord):
    return JSONResponse(status_code=422,content={'detail':str(exc),'unknown_fields':exc.fields})


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422,content={'detail':'Request fields are missing or invalid.'})


@app.get('/api/access')
async def access_configuration():
    return {'mode':access.mode()}


class LoginBody(BaseModel):
    email: str = Field(min_length=3,max_length=320)
    password: str = Field(min_length=1,max_length=1000)


@app.post('/api/access/login')
async def login(body: LoginBody):
    if access.mode()!='supabase': raise HTTPException(400,'This backend is in local development mode.')
    public_key=os.getenv('SUPABASE_PUBLIC_KEY','')
    if not public_key: raise HTTPException(503,'Set SUPABASE_PUBLIC_KEY on the backend for team sign-in.')
    async with httpx.AsyncClient(timeout=15) as client:
        res=await client.post(SUPABASE_URL+'/auth/v1/token',params={'grant_type':'password'},
            headers={'apikey':public_key},json=body.model_dump())
    if res.status_code!=200: raise HTTPException(401,'Sign-in failed. Check your email and password.')
    payload=res.json()
    rows=(await store._t('app_members').select('role').eq('auth_id',payload['user']['id']).execute()).data
    if not rows: raise HTTPException(403,'A team administrator must grant your workspace membership.')
    return JSONResponse(content={'access_token':payload['access_token'],'role':rows[0]['role']},headers={'Cache-Control':'no-store'})


@app.exception_handler(ValueError)
async def invalid_state(_: Request, exc: ValueError):
    return JSONResponse(status_code=422,content={'detail':'The capture state or version is invalid. Refresh before retrying.'})
