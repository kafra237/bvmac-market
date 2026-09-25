#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

BEGIN = '# BVMAC_APP_ROUTES_BEGIN'
END = '# BVMAC_APP_ROUTES_END'
BLOCK = r'''# BVMAC_APP_ROUTES_BEGIN
location = /app {
    # Rewrite only to project-owned internal locations. Each internal location
    # repeats auth_request, so no view can bypass the member gate.
    if ($arg_view = feeds) { rewrite ^ /_bvmac_app_feeds last; }
    if ($arg_view = analysis) { rewrite ^ /_bvmac_app_analysis last; }
    if ($arg_view = portfolios) { rewrite ^ /_bvmac_app_portfolios last; }
    auth_request /_member_access;
    error_page 401 403 =302 /auth.html?mode=login;
    add_header Cache-Control "private, no-store, must-revalidate" always;
    try_files /app.html =404;
}
location = /_bvmac_app_feeds {
    internal;
    auth_request /_member_access;
    error_page 401 403 =302 /auth.html?mode=login;
    add_header Cache-Control "private, no-store, must-revalidate" always;
    rewrite ^ /app-feeds.html break;
}
location = /_bvmac_app_analysis {
    internal;
    auth_request /_member_access;
    error_page 401 403 =302 /auth.html?mode=login;
    add_header Cache-Control "private, no-store, must-revalidate" always;
    rewrite ^ /app-analysis.html break;
}
location = /_bvmac_app_portfolios {
    internal;
    auth_request /_member_access;
    error_page 401 403 =302 /auth.html?mode=login;
    add_header Cache-Control "private, no-store, must-revalidate" always;
    rewrite ^ /app-portfolios.html break;
}
# Legacy private URLs remain compatibility redirects only.
location = /app.html { return 308 /app$is_args$args; }
location = /feeds.html { return 308 /app?view=feeds&$args; }
location = /lab.html { return 308 /app?view=analysis&$args; }
location = /account.html { return 308 /app?view=portfolios&$args; }
# The implementation documents are not public endpoints.
location = /app-feeds.html { return 404; }
location = /app-analysis.html { return 404; }
location = /app-portfolios.html { return 404; }
# Release discovery must never be cached; browsers/PWA use it to self-update.
location = /version.json {
    default_type application/json;
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;
    try_files $uri =404;
}
# BVMAC_APP_ROUTES_END'''

def remove_location(text: str, target: str) -> str:
    needle = f'location = {target}'
    while True:
        i = text.find(needle)
        if i < 0:
            return text
        brace = text.find('{', i)
        if brace < 0:
            raise SystemExit(f'Malformed Nginx location for {target}')
        depth = 0
        end = None
        for j in range(brace, len(text)):
            if text[j] == '{': depth += 1
            elif text[j] == '}':
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end is None:
            raise SystemExit(f'Unclosed Nginx location for {target}')
        while end < len(text) and text[end] in ' \t\r\n':
            end += 1
        text = text[:i] + text[end:]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('site')
    args=ap.parse_args()
    p=Path(args.site)
    text=p.read_text(encoding='utf-8')
    # Replace an existing managed block idempotently.
    if BEGIN in text:
        a=text.index(BEGIN); b=text.index(END,a)+len(END)
        text=text[:a]+BLOCK+text[b:]
    else:
        for target in ['/app.html','/lab.html','/account.html','/feeds.html']:
            text=remove_location(text,target)
        anchor='location = /demo.html'
        idx=text.find(anchor)
        if idx < 0:
            anchor='location = /auth.html'; idx=text.find(anchor)
        if idx < 0:
            raise SystemExit('Could not find safe insertion anchor in BVMAC Nginx site')
        text=text[:idx]+BLOCK+'\n'+text[idx:]
    # Avoid duplicate standalone version.json blocks when rerun.
    first=text.find(BEGIN); managed_end=text.find(END,first)+len(END)
    tail=text[managed_end:]
    # No mutation outside the project site beyond managed routing.
    p.write_text(text,encoding='utf-8')

if __name__=='__main__': main()
