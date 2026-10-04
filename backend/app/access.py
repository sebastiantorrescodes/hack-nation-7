"""One request identity and one access policy; service credentials never reach clients."""

import os
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .config import FRONTEND_ORIGIN, SUPABASE_URL


@dataclass(frozen=True)
class Principal:
    auth_id: str | None = None
    user_id: str | None = None
    role: str = 'admin'
    local: bool = True


caller: ContextVar[Principal] = ContextVar('caller', default=Principal())


def mode() -> str:
    value = os.getenv('APP_AUTH_MODE', 'local')
    if value not in {'local', 'supabase'}:
        raise RuntimeError('APP_AUTH_MODE must be local or supabase')
    return value


def authorize_owner(row: dict) -> None:
    principal = caller.get()
    if not principal.local and principal.role != 'admin' and row.get('owner_principal') != principal.auth_id:
        raise HTTPException(403, 'This session belongs to another team member.')


async def request_principal(request: Request) -> Principal:
    if mode() == 'local':
        # Local mode is an explicit development mode, never an anonymous public deployment.
        if not request.client or request.client.host not in {'127.0.0.1', '::1', 'testclient'} or request.url.hostname not in {'127.0.0.1', 'localhost', '::1', 'test'}:
            raise HTTPException(401, 'Public access requires APP_AUTH_MODE=supabase.')
        origin = request.headers.get('origin')
        if origin and origin != FRONTEND_ORIGIN and urlsplit(origin).scheme != 'chrome-extension':
            raise HTTPException(403, 'This browser origin is not allowed.')
        if any(request.headers.get(h) for h in ('x-forwarded-for','forwarded','x-forwarded-host','cf-connecting-ip')):
            raise HTTPException(401, 'Proxied access requires authenticated mode.')
        return Principal()
    token = request.headers.get('authorization', '')
    if not token.startswith('Bearer '):
        raise HTTPException(401, 'Sign in to your team workspace.')
    public_key = os.getenv('SUPABASE_PUBLIC_KEY', '')
    if not public_key:
        raise HTTPException(503, 'The backend needs SUPABASE_PUBLIC_KEY for team sign-in.')
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(SUPABASE_URL + '/auth/v1/user', headers={'apikey': public_key, 'Authorization': token})
    if response.status_code != 200:
        raise HTTPException(401, 'Your sign-in expired. Sign in again.')
    auth_id = response.json().get('id')
    from . import store
    rows = (await store._t('app_members').select('user_id,role').eq('auth_id', auth_id).execute()).data
    if not rows:
        raise HTTPException(403, 'A team administrator must grant your workspace membership.')
    return Principal(auth_id=auth_id, user_id=rows[0]['user_id'], role=rows[0]['role'], local=False)


async def protect(request: Request, call_next):
    if not request.url.path.startswith('/api/') or request.url.path in {'/api/health', '/api/access', '/api/access/login'} or request.method == 'OPTIONS':
        return await call_next(request)
    try:
        principal = await request_principal(request)
        expert_write = request.method not in {'GET', 'HEAD'} and request.url.path.startswith(('/api/capture', '/api/workmaps', '/api/skills', '/api/workflows', '/api/cases', '/api/apprentice'))
        expert_read = request.url.path.startswith(('/api/capture', '/api/workmaps', '/api/skills', '/api/apprentice'))
        if (expert_write or expert_read) and principal.role not in {'expert', 'admin'}:
            raise HTTPException(403, 'An expert must review or change this knowledge.')
        key = caller.set(principal)
        try:
            return await call_next(request)
        finally:
            caller.reset(key)
    except httpx.RequestError:
        return JSONResponse(status_code=503, content={"detail": "The sign-in service could not be reached. Retry before continuing."})
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})
