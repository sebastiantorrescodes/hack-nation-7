"""Show capture readiness counts without printing record or transcript content."""
import asyncio
import sys
from collections import Counter
from uuid import UUID
import httpx


async def main():
    capture_id=str(UUID(sys.argv[1]))
    async with httpx.AsyncClient(timeout=30) as client:
        result=await client.get(f'http://127.0.0.1:8000/api/capture/sessions/{capture_id}')
    print(f'Capture read: HTTP {result.status_code}')
    if result.status_code!=200: return 1
    capture=result.json()
    roles=Counter(turn['role'] for turn in capture['transcript'])
    print(f"Phase: {capture['phase']}; expert turns: {roles['expert']}; agent turns: {roles['agent']}; observed events: {len(capture['events'])}")
    print('Build prerequisites: '+('ready' if roles['expert'] and capture['events'] else 'missing expert explanation or observed event'))
    return 0


if __name__=='__main__':
    try: result=asyncio.run(main())
    except Exception as exc:
        print(f'Capture diagnostic failed ({type(exc).__name__}); private content was not printed.')
        result=1
    raise SystemExit(result)
