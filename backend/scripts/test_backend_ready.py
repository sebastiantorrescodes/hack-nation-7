"""Read-only live readiness checks. Never print keys, records or signed URLs."""
import asyncio
import httpx
from supabase import acreate_client
from app.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY


async def main():
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=30) as client:
        for path in ['/api/health','/api/access','/api/workflows','/api/workflows/published','/api/capture/sessions','/api/voice/signed-url']:
            res=await client.get(path)
            print(f'{path}: HTTP {res.status_code}')
            if res.status_code!=200: return 1
            if path.endswith('signed-url') and not res.json().get('signed_url'): return 1
    db=await acreate_client(SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY)
    for table,columns in [('sessions','owner_principal,capture_phase,workmap_revision,skill_snapshot'),
            ('skills','version,supersedes,build_revision'),('capture_batches','client_id'),
            ('workmap_builds','revision'),('apprentice_states','revision'),('app_members','role'),
            ('skill_reviews','version'),('transcript_segments','client_id,question_id,answer_id')]:
        await db.table(table).select(columns).limit(0).execute()
        print(f'{table}: schema and backend access OK')
    print('PASS: live backend, migrated Supabase schema and ElevenLabs connection are ready.')
    return 0


if __name__=='__main__':
    try: result=asyncio.run(main())
    except Exception as exc:
        print(f'Readiness failed ({type(exc).__name__}); no private response was printed.')
        result=1
    raise SystemExit(result)
