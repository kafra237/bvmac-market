from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from market import companies

from auth_module import require_user
from common import db_connect

router = APIRouter(prefix='/api/v3/feeds', tags=['feeds'])


@router.get('')
def feeds(request: Request, publication_type: str | None = Query(default=None, pattern=r'^(avis|communique)$'),
          q: str | None = Query(default=None, max_length=120), limit: int = Query(default=100, ge=1, le=300),
          offset: int = Query(default=0, ge=0), portfolio: int | None = Query(default=None, ge=1), scope: str | None = None):
    user = require_user(request)
    where = []
    args = []
    if publication_type:
        where.append('publication_type=%s'); args.append(publication_type)
    if q and q.strip():
        where.append('title ILIKE %s'); args.append('%'+q.strip()+'%')
    with db_connect() as conn:
        if portfolio or scope == 'holdings':
            if portfolio and not conn.execute('SELECT 1 FROM portfolio.portfolio WHERE portfolio_id=%s AND user_id=%s',(portfolio,user['user_id'])).fetchone():
                raise HTTPException(status_code=404,detail='Portefeuille introuvable')
            rows=conn.execute('''SELECT DISTINCT pos.company_id FROM portfolio.position pos
                                JOIN portfolio.portfolio p ON p.portfolio_id=pos.portfolio_id
                                WHERE p.user_id=%s AND (%s::bigint IS NULL OR p.portfolio_id=%s)
                                AND (%s::bigint IS NOT NULL OR p.archived_at IS NULL)''',
                              (user['user_id'],portfolio,portfolio,portfolio)).fetchall()
            ids={int(r[0]) for r in rows}
            terms=sorted({str(c.get(key)).strip() for c in companies(conn) if int(c['company_id']) in ids
                          for key in ('ticker','short_name','full_name') if c.get(key) and len(str(c[key]).strip())>=3})
            where.append('('+' OR '.join("title ILIKE %s ESCAPE '!'" for _ in terms)+')' if terms else 'FALSE')
            args.extend('%'+term.replace('!', '!!').replace('%', '!%').replace('_', '!_')+'%' for term in terms)
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        total = int(conn.execute('SELECT count(*) FROM market.bvmac_communication'+clause, tuple(args)).fetchone()[0])
        rows = conn.execute(
            '''SELECT wp_id,publication_type,title,url,guid,published_at,author,detected_at,source_feed
               FROM market.bvmac_communication''' + clause +
            ''' ORDER BY published_at DESC NULLS LAST, wp_id DESC LIMIT %s OFFSET %s''',
            tuple(args+[limit, offset]),
        ).fetchall()
    return {'total': total, 'limit': limit, 'offset': offset, 'items': [
        {'wp_id': r[0], 'type': r[1], 'title': r[2], 'url': r[3], 'guid': r[4],
         'published_at': r[5], 'author': r[6], 'detected_at': r[7], 'source_feed': r[8]}
        for r in rows
    ]}


@router.get('/status')
def feed_status(request: Request):
    require_user(request)
    with db_connect() as conn:
        rows = conn.execute('''SELECT feed,last_check,last_success,items_received,new_items_total,errors_total,last_error
                               FROM admin.feed_monitor ORDER BY feed''').fetchall()
    return {'feeds': [
        {'feed': r[0], 'last_check': r[1], 'last_success': r[2], 'items_received': r[3],
         'new_items_total': r[4], 'errors_total': r[5], 'last_error': r[6]}
        for r in rows
    ]}
