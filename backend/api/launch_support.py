"""Bornes de consommation et informations publiques de l'éditeur."""
from collections import OrderedDict, deque
from threading import Lock, BoundedSemaphore
from time import monotonic
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
_buckets = OrderedDict()
_lock = Lock()
_calculations = BoundedSemaphore(2)
EXPENSIVE = {'/api/v3/portfolio/optimize', '/api/v3/portfolio/backtest'}

def allowed(key, limit, now=None):
    now = monotonic() if now is None else now
    with _lock:
        bucket = _buckets.pop(key, deque())
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        accepted = len(bucket) < limit
        if accepted:
            bucket.append(now)
        _buckets[key] = bucket
        while len(_buckets) > 10000:
            _buckets.popitem(last=False)
        return accepted

async def guard(request, call_next):
    path = request.url.path
    if not path.startswith('/api/'):
        return await call_next(request)
    # The API listens only on loopback; Nginx overwrites X-Real-IP.
    # Deny by default: only explicitly public API routes remain available to guests.
    public = path in {'/api/health','/api/landing','/api/site-info','/api/access'} or path.startswith(('/api/v3/auth/','/api/v3/analytics/','/api/stat/')) or path=='/api/v3/feedback'
    if not public:
        from auth_module import require_user
        from fastapi import HTTPException
        try:
            require_user(request)
        except HTTPException as exc:
            return JSONResponse({'detail':'Inscrivez-vous pour bénéficier des fonctionnalités.','redirect':'/'},status_code=exc.status_code,headers={'Cache-Control':'no-store'})
    peer = request.client.host if request.client else 'unknown'
    client = request.headers.get('x-real-ip', peer) if peer in {'127.0.0.1', '::1'} else peer
    sensitive = path in {'/api/v3/auth/verify-email', '/api/v3/auth/reset-password', '/api/v3/auth/resend-email'}
    limit = 20 if sensitive else 6 if path in EXPENSIVE else 20 if path == '/api/landing' else 120
    family = 'recovery' if sensitive else 'calculation' if path in EXPENSIVE else 'public' if path == '/api/landing' else 'general'
    if not allowed((client, family), limit):
        return JSONResponse({'detail': 'Trop de demandes. Réessaie dans une minute.'}, status_code=429, headers={'Retry-After': '60', 'Cache-Control':'no-store'})
    acquired = path in EXPENSIVE
    if acquired and not _calculations.acquire(blocking=False):
        return JSONResponse({'detail':'Calculs momentanément occupés. Réessaie dans quelques instants.'},status_code=503,headers={'Retry-After':'10'})
    try:
        response = await call_next(request)
        if path.startswith(('/api/stat/', '/api/v3/auth/', '/api/v3/portfolio/')):
            response.headers['Cache-Control'] = 'no-store'
        return response
    finally:
        if acquired:
            _calculations.release()

@router.get('/api/site-info')
def site_info():
    return {'editor':os.environ.get('BVMAC_EDITOR_NAME','Initiative personnelle indépendante'),
            'contact_url':'/information.html#contact',
            'source_url':'https://www.bvm-ac.org/bulletin-officiel-de-la-cote-boc/',
            'affiliated':False, 'email_delivery':True}


@router.get('/api/access')
def access(request: Request):
    from auth_module import require_user
    from fastapi import Response
    require_user(request)
    return Response(status_code=204,headers={'Cache-Control':'no-store'})
