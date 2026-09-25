"""RSS collector for public BVMAC notices and market communications."""

#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg

APP_DIR = Path(__file__).resolve().parent / 'app'
if not APP_DIR.is_dir():
    APP_DIR = Path(os.environ.get('BVMAC_APP_DIR', '/opt/bvmac/current')) / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from push_service import broadcast_all

DSN = os.environ.get('BVMAC_DB_DSN', 'dbname=bvmac user=bvmacapi host=/var/run/postgresql')
FEEDS = {
    'avis': 'https://www.bvm-ac.org/category/avis-de-la-bvmac/feed/',
    'communique': 'https://www.bvm-ac.org/category/communiques-du-marche/feed/',
}
WP_RE = re.compile(r'[?&]p=(\d+)')
DC = '{http://purl.org/dc/elements/1.1/}'


def extract_wp_id(guid: str | None) -> int | None:
    m = WP_RE.search(guid or '')
    return int(m.group(1)) if m else None


def clean_url(url: str | None) -> str | None:
    return url.split('?', 1)[0] if url else None


def parse_date(raw: str | None):
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def text(item, name: str) -> str:
    e = item.find(name)
    return (e.text or '').strip() if e is not None and e.text else ''


def fetch_xml(url: str, etag: str | None, last_modified: str | None):
    headers = {'User-Agent': 'BVMAC-Market-RSS/1.0'}
    if etag:
        headers['If-None-Match'] = etag
    if last_modified:
        headers['If-Modified-Since'] = last_modified
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read(2_000_000), r.headers.get('ETag'), r.headers.get('Last-Modified'), False
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return b'', etag, last_modified, True
        raise


def ensure_monitor(conn, feed: str, url: str):
    conn.execute('''INSERT INTO admin.feed_monitor(feed,feed_url) VALUES(%s,%s)
                    ON CONFLICT(feed) DO UPDATE SET feed_url=EXCLUDED.feed_url''', (feed, url))
    conn.commit()


def process_feed(conn, feed: str, url: str):
    ensure_monitor(conn, feed, url)
    state = conn.execute('SELECT bootstrap_completed,etag,last_modified FROM admin.feed_monitor WHERE feed=%s', (feed,)).fetchone()
    bootstrap = not bool(state[0])
    conn.execute('UPDATE admin.feed_monitor SET last_check=now(),updated_at=now() WHERE feed=%s', (feed,)); conn.commit()
    raw, etag, last_modified, not_modified = fetch_xml(url, state[1], state[2])
    if not_modified:
        conn.execute('''UPDATE admin.feed_monitor SET last_success=now(),last_error=NULL,etag=%s,last_modified=%s,updated_at=now()
                        WHERE feed=%s''', (etag, last_modified, feed)); conn.commit(); return
    root = ET.fromstring(raw)
    items = root.findall('./channel/item')
    new_count = 0
    for item in reversed(items):
        guid = text(item, 'guid')
        wp_id = extract_wp_id(guid)
        if wp_id is None:
            continue
        title = text(item, 'title')
        url_clean = clean_url(text(item, 'link'))
        published = parse_date(text(item, 'pubDate'))
        author = text(item, DC+'creator') or None
        inserted = conn.execute(
            '''INSERT INTO market.bvmac_communication
               (wp_id,publication_type,title,url,guid,published_at,author,detected_at,source_feed)
               VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s)
               ON CONFLICT(wp_id) DO NOTHING RETURNING wp_id''',
            (wp_id, feed, title, url_clean, guid, published, author, feed),
        ).fetchone()
        conn.commit()
        if not inserted:
            continue
        new_count += 1
        if not bootstrap:
            label = 'Nouvel avis BVMAC' if feed == 'avis' else 'Nouveau communiqué du marché'
            broadcast_all(
                conn, category='feed_'+feed, event_key=f'feed:{feed}:{wp_id}', title='BVMAC · '+label,
                body=title[:220], target_url=f'/app?view=feeds#item-{wp_id}',
            )
    conn.execute('''UPDATE admin.feed_monitor SET bootstrap_completed=true,last_success=now(),items_received=%s,
                    new_items_total=new_items_total+%s,last_error=NULL,etag=%s,last_modified=%s,updated_at=now()
                    WHERE feed=%s''', (len(items), new_count, etag, last_modified, feed))
    conn.commit()


def main():
    failures = 0
    with psycopg.connect(DSN) as conn:
        for feed, url in FEEDS.items():
            try:
                process_feed(conn, feed, url)
            except Exception as exc:
                failures += 1
                ensure_monitor(conn, feed, url)
                conn.execute('''UPDATE admin.feed_monitor SET errors_total=errors_total+1,last_error=%s,last_check=now(),updated_at=now()
                                WHERE feed=%s''', (f'{type(exc).__name__}: {str(exc)[:600]}', feed))
                conn.commit()
                print(f'[feed:{feed}] {type(exc).__name__}: {exc}', file=sys.stderr)
    # Une panne d'un flux n'empêche jamais l'autre. Le service échoue seulement si les deux sont indisponibles.
    if failures == len(FEEDS):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
