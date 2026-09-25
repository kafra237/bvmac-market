"""Weekly market report builder: metrics, tables, charts and email delivery."""

from __future__ import annotations

import html
import math
import os
import smtplib
import ssl
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from io import BytesIO
from email.utils import parseaddr
from statistics import median
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auth_module import require_user
from email_auth import settings as mail_settings
from common import PUBLIC_ORIGIN, db_connect, decrypt_pii

router = APIRouter(prefix='/api/v3/email', tags=['weekly-email'])
DOUALA = ZoneInfo('Africa/Douala')


class EmailPreferencesIn(BaseModel):
    weekly_market_summary: bool = True


def _num(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _did(value) -> int | None:
    try:
        number = int(float(value))
        return number if 19000101 <= number <= 29991231 else None
    except Exception:
        return None


def _date_from_id(value) -> date | None:
    number = _did(value)
    if not number:
        return None
    try:
        return datetime.strptime(str(number), '%Y%m%d').date()
    except ValueError:
        return None


def _pct(new, old):
    if new is None or old in (None, 0):
        return None
    return (new / old - 1) * 100


def _safe_sum(values):
    return sum(v for v in values if v is not None)


def period_bounds(now: datetime | None = None) -> tuple[date, date, date, date]:
    current = (now or datetime.now(DOUALA)).astimezone(DOUALA)
    this_monday = (current - timedelta(days=current.weekday())).date()
    start = this_monday - timedelta(days=7)
    end = this_monday - timedelta(days=1)
    prev_start = start - timedelta(days=7)
    prev_end = start - timedelta(days=1)
    return start, end, prev_start, prev_end


def _sheet(conn, name: str) -> list[dict]:
    return [dict(r[0]) for r in conn.execute(
        'SELECT data FROM market.current_excel_row WHERE sheet_name=%s ORDER BY row_number', (name,)
    ).fetchall()]


def _company_map(conn):
    out = {}
    for row in _sheet(conn, 'dim_company'):
        cid = row.get('company_id')
        if cid is not None:
            out[str(cid)] = row.get('ticker') or row.get('short_name') or row.get('full_name') or f'#{cid}'
    return out


def _fund_map(conn):
    out = {}
    for row in _sheet(conn, 'dim_opcvm'):
        fid = row.get('fund_id')
        if fid is not None:
            out[str(fid)] = row
    return out


def _action_week(conn, start: date, end: date) -> dict:
    start_id, end_id = int(start.strftime('%Y%m%d')), int(end.strftime('%Y%m%d'))
    rows = [r for r in _sheet(conn, 'fact_prices') if (d := _did(r.get('bulletin_date_id'))) and start_id <= d <= end_id]
    cmap = _company_map(conn)
    by_company = defaultdict(list)
    by_day = defaultdict(list)
    for row in rows:
        d = _did(row.get('bulletin_date_id'))
        cid = str(row.get('company_id'))
        by_company[cid].append(row)
        by_day[d].append(row)

    performances = []
    for cid, items in by_company.items():
        points = sorted(((_did(r.get('bulletin_date_id')), _num(r.get('close_price'))) for r in items), key=lambda x: x[0] or 0)
        points = [(d, p) for d, p in points if d and p is not None and p > 0]
        if len(points) >= 2:
            performances.append({'company_id': cid, 'name': cmap.get(cid, cid), 'performance': _pct(points[-1][1], points[0][1])})

    perf_values = [x['performance'] for x in performances if x['performance'] is not None]
    daily_bid, daily_ask = [], []
    for day in sorted(by_day):
        daily_bid.append(_safe_sum(_num(r.get('vol_bid')) for r in by_day[day]))
        daily_ask.append(_safe_sum(_num(r.get('vol_ask')) for r in by_day[day]))
    value_traded = _safe_sum(_num(r.get('value_traded')) for r in rows)
    volume_traded = _safe_sum(_num(r.get('vol_traded')) for r in rows)
    transactions = _safe_sum(_num(r.get('num_transactions')) for r in rows)
    active = len({str(r.get('company_id')) for r in rows if (_num(r.get('vol_traded')) or 0) > 0 or (_num(r.get('value_traded')) or 0) > 0})
    positive = sorted((x for x in performances if (x['performance'] or 0) > 0), key=lambda x: x['performance'], reverse=True)
    return {
        'sessions': len(by_day), 'count': len(performances),
        'up': sum(1 for x in perf_values if x > 0), 'flat': sum(1 for x in perf_values if abs(x) < 1e-12), 'down': sum(1 for x in perf_values if x < 0),
        'median_performance': median(perf_values) if perf_values else None,
        'top': positive[:3], 'value_traded': value_traded, 'volume_traded': volume_traded,
        'transactions': transactions, 'active_companies': active,
        'avg_bid': sum(daily_bid) / len(daily_bid) if daily_bid else None,
        'avg_ask': sum(daily_ask) / len(daily_ask) if daily_ask else None,
    }


def _index_week(conn, start: date, end: date) -> dict:
    start_id, end_id = int(start.strftime('%Y%m%d')), int(end.strftime('%Y%m%d'))
    points = []
    for row in _sheet(conn, 'fact_index'):
        d = _did(row.get('date_id'))
        value = _num(row.get('index_value'))
        if d and start_id <= d <= end_id and value is not None:
            points.append((d, value))
    points.sort()
    return {
        'start': points[0][1] if points else None,
        'end': points[-1][1] if points else None,
        'performance': _pct(points[-1][1], points[0][1]) if len(points) >= 2 else None,
        'points': [{'date': str(d), 'value': v} for d, v in points],
    }


def _opcvm_week(conn, start: date, end: date) -> dict:
    start_id, end_id = int(start.strftime('%Y%m%d')), int(end.strftime('%Y%m%d'))
    funds = _fund_map(conn)
    by_fund = defaultdict(dict)
    for row in _sheet(conn, 'fact_opcvm_nav'):
        fid = str(row.get('fund_id'))
        d = _did(row.get('nav_date_id')) or _did(row.get('bulletin_date_id'))
        nav = _num(row.get('nav'))
        if d and d <= end_id and nav is not None and nav > 0:
            by_fund[fid][d] = nav
    performances = []
    for fid, pts in by_fund.items():
        ordered = sorted(pts.items())
        in_week = [(d, nav) for d, nav in ordered if start_id <= d <= end_id]
        if not in_week:
            continue
        end_point = in_week[-1]
        before = [(d, nav) for d, nav in ordered if d < start_id]
        baseline = before[-1] if before else (in_week[0] if len(in_week) >= 2 else None)
        if baseline and baseline[1] > 0 and baseline[0] != end_point[0]:
            meta = funds.get(fid, {})
            performances.append({
                'fund_id': fid, 'name': meta.get('fund_name') or f'OPCVM {fid}',
                'manager': meta.get('manager'), 'category': meta.get('category'),
                'performance': _pct(end_point[1], baseline[1]),
            })
    values = [x['performance'] for x in performances if x['performance'] is not None]
    positive = sorted((x for x in performances if (x['performance'] or 0) > 0), key=lambda x: x['performance'], reverse=True)
    return {
        'count': len(performances), 'up': sum(1 for x in values if x > 0), 'flat': sum(1 for x in values if abs(x) < 1e-12),
        'down': sum(1 for x in values if x < 0), 'median_performance': median(values) if values else None, 'top': positive[:3],
    }


def _radar_snapshot(conn) -> dict:
    try:
        run = conn.execute("SELECT run_id,model_version,data_through,generated_at FROM ml.latest_run").fetchone()
        if not run:
            return {'available': False, 'actions': [], 'opcvm': []}
        actions = []
        for row in conn.execute("""SELECT ticker,trade_5_prob,trade5_confidence,up_20_prob,up20_confidence,book_imbalance
                                   FROM ml.latest_action_prediction ORDER BY trade_5_prob DESC NULLS LAST""").fetchall():
            actions.append({'ticker': row[0], 'trade_5_prob': row[1], 'trade5_confidence': row[2], 'up_20_prob': row[3], 'up20_confidence': row[4], 'book_imbalance': row[5]})
        funds = []
        for row in conn.execute("""SELECT fund_id,fund_name,risk_down_next_nav,confidence,nav,nav_date
                                   FROM ml.latest_opcvm_prediction ORDER BY risk_down_next_nav DESC NULLS LAST""").fetchall():
            funds.append({'fund_id': row[0], 'fund_name': row[1], 'risk_down_next_nav': row[2], 'confidence': row[3], 'nav': row[4], 'nav_date': row[5]})
        return {'available': True, 'model_version': run[1], 'data_through': run[2], 'generated_at': run[3], 'actions': actions, 'opcvm': funds}
    except Exception:
        return {'available': False, 'actions': [], 'opcvm': []}


def build_summary(conn, start: date, end: date, prev_start: date, prev_end: date) -> dict:
    current_actions = _action_week(conn, start, end)
    previous_actions = _action_week(conn, prev_start, prev_end)
    current_index = _index_week(conn, start, end)
    previous_index = _index_week(conn, prev_start, prev_end)
    current_funds = _opcvm_week(conn, start, end)
    previous_funds = _opcvm_week(conn, prev_start, prev_end)
    return {
        'period': {'start': start, 'end': end, 'prev_start': prev_start, 'prev_end': prev_end},
        'actions': current_actions, 'actions_prev': previous_actions,
        'index': current_index, 'index_prev': previous_index,
        'opcvm': current_funds, 'opcvm_prev': previous_funds,
        'radar': _radar_snapshot(conn),
    }


def _fmt_pct(value, digits=2):
    return '—' if value is None else f'{value:+.{digits}f} %'.replace('.', ',')


def _fmt_num(value, digits=0):
    if value is None:
        return '—'
    return f'{value:,.{digits}f}'.replace(',', ' ').replace('.', ',')


def _delta(current, previous):
    return _pct(current, previous)


def _comparison_text(current, previous, unit=''):
    delta = _delta(current, previous)
    if delta is None:
        return 'Comparaison S-1 indisponible'
    sign = '+' if delta >= 0 else ''
    return f'{sign}{delta:.1f} % vs S-1'.replace('.', ',')


def _subject(summary: dict) -> str:
    idx = summary['index']['performance']
    liq = _delta(summary['actions']['value_traded'], summary['actions_prev']['value_traded'])
    if idx is not None and idx > 0 and liq is not None and liq > 0:
        return f"BVMAC Hebdo · indice {_fmt_pct(idx)} et échanges {_fmt_pct(liq,1)} vs S-1"
    if idx is not None and idx > 0:
        return f"BVMAC Hebdo · l’indice progresse de {_fmt_pct(idx)}"
    if liq is not None and liq > 0:
        return f"BVMAC Hebdo · les échanges progressent de {_fmt_pct(liq,1)} vs S-1"
    p = summary['period']
    return f"BVMAC Hebdo · Synthèse du {p['start'].strftime('%d/%m')} au {p['end'].strftime('%d/%m/%Y')}"


def _top_rows(items, label='Performance'):
    if not items:
        return '<p style="margin:8px 0;color:#67726c">Aucune progression calculable sur la période.</p>'
    rows = []
    for item in items:
        details = ' · '.join(x for x in [item.get('category'), item.get('manager')] if x)
        rows.append(f'''<tr><td style="padding:8px 0;border-bottom:1px solid #edf0ec"><b>{html.escape(str(item['name']))}</b>{('<br><span style="color:#67726c;font-size:12px">'+html.escape(details)+'</span>') if details else ''}</td><td style="padding:8px 0;border-bottom:1px solid #edf0ec;text-align:right;color:#27734b;font-weight:700">{_fmt_pct(item['performance'])}</td></tr>''')
    return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">' + ''.join(rows) + '</table>'


def _metric(label, value, comparison=None, positive=None):
    color = '#27734b' if positive is True else '#a54235' if positive is False else '#17201c'
    return f'''<td width="50%" style="padding:6px;vertical-align:top"><div style="border:1px solid #d9ded8;border-radius:10px;padding:12px;background:#f7f9f7"><div style="font-size:12px;color:#67726c">{html.escape(label)}</div><div style="font-size:22px;font-weight:800;color:{color};margin-top:3px">{html.escape(value)}</div>{f'<div style="font-size:12px;color:#67726c;margin-top:4px">{html.escape(comparison)}</div>' if comparison else ''}</div></td>'''


def _insights(summary):
    a, ap = summary['actions'], summary['actions_prev']
    i, ip = summary['index'], summary['index_prev']
    f, fp = summary['opcvm'], summary['opcvm_prev']
    lines = []
    if i['performance'] is not None:
        text = f"L’indice BVMAC-AS termine la semaine à {_fmt_num(i['end'],2)} points ({_fmt_pct(i['performance'])})."
        if ip['performance'] is not None:
            text += f" La semaine précédente : {_fmt_pct(ip['performance'])}."
        lines.append(text)
    if a['value_traded'] or ap['value_traded']:
        lines.append(f"La valeur échangée atteint {_fmt_num(a['value_traded'],0)} XAF ({_comparison_text(a['value_traded'], ap['value_traded'])}), avec {a['active_companies']} valeur(s) active(s).")
    if a['up']:
        lines.append(f"{a['up']} action(s) progressent sur la semaine, contre {ap['up']} la semaine précédente.")
    if f['count']:
        lines.append(f"Côté OPCVM, {f['up']} fonds sur {f['count']} calculables progressent ; performance médiane {_fmt_pct(f['median_performance'])}.")
    return lines[:4]


def _prob(value):
    return '—' if value is None else f'{float(value)*100:.1f} %'.replace('.', ',')


def _radar_rows(summary: dict) -> str:
    radar = summary.get('radar') or {}
    if not radar.get('available'):
        return '<p style="color:#67726c">Radar en attente du prochain calcul.</p>'
    usable = [x for x in radar.get('actions', []) if x.get('trade5_confidence') in ('medium','high')]
    usable = sorted(usable, key=lambda x: x.get('trade_5_prob') or 0, reverse=True)[:5]
    if not usable:
        return '<p style="color:#67726c">Aucune prévision action avec un niveau de confiance suffisant cette semaine.</p>'
    rows=[]
    for x in usable:
        imb = _num(x.get('book_imbalance'))
        pressure = 'Acheteuse' if imb is not None and imb > .2 else 'Vendeuse' if imb is not None and imb < -.2 else 'Équilibrée'
        rows.append(f'''<tr><td style="padding:8px 0;border-bottom:1px solid #edf0ec"><b>{html.escape(str(x['ticker']))}</b><br><span style="font-size:11px;color:#67726c">{pressure}</span></td><td style="padding:8px 4px;border-bottom:1px solid #edf0ec;text-align:right"><b>{_prob(x.get('trade_5_prob'))}</b><br><span style="font-size:11px;color:#67726c">échange ≤ 5 bulletins</span></td><td style="padding:8px 0;border-bottom:1px solid #edf0ec;text-align:right"><b>{_prob(x.get('up_20_prob'))}</b><br><span style="font-size:11px;color:#67726c">cours supérieur ~20</span></td></tr>''')
    return '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'+''.join(rows)+'</table>'


def _chart_pngs(summary: dict) -> dict[str, bytes]:
    # systemd durcit l’accès au HOME ; Matplotlib écrit donc son cache dans le /tmp privé du service.
    os.environ.setdefault('MPLCONFIGDIR','/tmp/bvmac-matplotlib')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception:
        return {}
    charts={}
    def save(fig, key):
        buf=BytesIO();fig.savefig(buf,format='png',dpi=150,bbox_inches='tight',facecolor='white');plt.close(fig);charts[key]=buf.getvalue()
    pts=summary['index'].get('points') or []
    if len(pts)>=2:
        fig,ax=plt.subplots(figsize=(6.2,2.35));x=list(range(len(pts)));y=[v['value'] for v in pts]
        ax.plot(x,y,marker='o',linewidth=2,color='#2e5e4e');ax.fill_between(x,y,min(y),alpha=.08,color='#2e5e4e')
        ax.set_title('BVMAC-AS · semaine écoulée',loc='left',fontsize=11,fontweight='bold');ax.grid(axis='y',alpha=.18);ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='y',labelsize=8);ax.set_xticks(x);ax.set_xticklabels([datetime.strptime(str(v['date']),'%Y%m%d').strftime('%d/%m') for v in pts],fontsize=8)
        save(fig,'index_chart')
    a,ap=summary['actions'],summary['actions_prev']
    metrics=[('Valeur échangée',a['value_traded'],ap['value_traded']),('Volume échangé',a['volume_traded'],ap['volume_traded']),('Transactions',a['transactions'],ap['transactions']),('Demande moy.',a['avg_bid'],ap['avg_bid']),('Offre moy.',a['avg_ask'],ap['avg_ask'])]
    vals=[];labels=[]
    for label,cur,prev in metrics:
        d=_delta(cur,prev)
        if d is not None and math.isfinite(d): labels.append(label);vals.append(max(-200,min(200,d)))
    if vals:
        fig,ax=plt.subplots(figsize=(6.2,2.8));yy=list(range(len(vals)));colors=['#2e5e4e' if v>=0 else '#a54235' for v in vals]
        ax.barh(yy,vals,color=colors,alpha=.9);ax.axvline(0,color='#7b847f',linewidth=.8);ax.set_yticks(yy);ax.set_yticklabels(labels,fontsize=8);ax.invert_yaxis();ax.set_title('Activité · variation vs semaine précédente',loc='left',fontsize=11,fontweight='bold');ax.spines[['top','right','left','bottom']].set_visible(False);ax.grid(axis='x',alpha=.15)
        for yi,v in zip(yy,vals): ax.text(v+(3 if v>=0 else -3),yi,f'{v:+.0f} %',va='center',ha='left' if v>=0 else 'right',fontsize=8,fontweight='bold')
        save(fig,'activity_chart')
    radar=summary.get('radar') or {}
    rx=[x for x in radar.get('actions',[]) if x.get('trade5_confidence') in ('medium','high')]
    rx=sorted(rx,key=lambda x:x.get('trade_5_prob') or 0,reverse=True)[:6]
    if rx:
        fig,ax=plt.subplots(figsize=(6.2,3.0));xx=np.arange(len(rx));w=.36
        ax.bar(xx-w/2,[100*(x.get('trade_5_prob') or 0) for x in rx],w,label='Liquidité ≤ 5',color='#2e5e4e')
        ax.bar(xx+w/2,[100*(x.get('up_20_prob') or 0) for x in rx],w,label='Cours supérieur ~20',color='#c9a23f')
        ax.set_ylim(0,100);ax.set_xticks(xx);ax.set_xticklabels([x['ticker'] for x in rx],fontsize=8);ax.set_ylabel('Probabilité',fontsize=8);ax.set_title('Radar prédictif · dernier bulletin disponible',loc='left',fontsize=11,fontweight='bold');ax.legend(frameon=False,fontsize=8,ncol=2,loc='upper center');ax.grid(axis='y',alpha=.15);ax.spines[['top','right','left']].set_visible(False);ax.tick_params(axis='y',labelsize=8)
        save(fig,'radar_chart')
    return charts


def _img_block(cid: str, alt: str) -> str:
    return f'<div style="margin:12px 0 4px"><img src="cid:{cid}" alt="{html.escape(alt)}" width="636" style="display:block;width:100%;max-width:636px;height:auto;border:1px solid #edf0ec;border-radius:10px"></div>'


def render_email(summary: dict, pseudo: str, chart_keys: set[str] | None = None) -> tuple[str, str, str]:
    chart_keys=chart_keys or set()
    a,ap=summary['actions'],summary['actions_prev'];i,ip=summary['index'],summary['index_prev'];f,fp=summary['opcvm'],summary['opcvm_prev'];p=summary['period'];radar=summary.get('radar') or {}
    subject=_subject(summary);bid_delta=_delta(a['avg_bid'],ap['avg_bid']);ask_delta=_delta(a['avg_ask'],ap['avg_ask']);traded_delta=_delta(a['volume_traded'],ap['volume_traded']);value_delta=_delta(a['value_traded'],ap['value_traded']);idx_positive=i['performance'] is not None and i['performance']>=0
    insights=''.join(f'<li style="margin:7px 0">{html.escape(line)}</li>' for line in _insights(summary));unsub=PUBLIC_ORIGIN+'/app?view=portfolios#notifications'
    index_img=_img_block('index_chart','Évolution hebdomadaire du BVMAC-AS') if 'index_chart' in chart_keys else ''
    activity_img=_img_block('activity_chart','Comparaison de l’activité avec la semaine précédente') if 'activity_chart' in chart_keys else ''
    radar_img=_img_block('radar_chart','Probabilités du Radar prédictif') if 'radar_chart' in chart_keys else ''
    radar_date=radar.get('data_through');radar_date=radar_date.strftime('%d/%m/%Y') if hasattr(radar_date,'strftime') else str(radar_date or '—')
    html_body=f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#f2f4f1;color:#17201c;font:15px Arial,sans-serif"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:24px 12px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;margin:auto;background:#fff;border:1px solid #d9ded8;border-radius:14px;overflow:hidden"><tr><td style="background:#17251f;color:#fff;padding:20px 22px"><div style="font-size:25px;font-weight:800">Marché <span style="color:#c9a23f">BVMAC</span></div><div style="margin-top:5px;color:#dfe7e2">BVMAC Hebdo · {p['start'].strftime('%d/%m')} → {p['end'].strftime('%d/%m/%Y')}</div></td></tr><tr><td style="padding:22px"><p style="margin-top:0">Bonjour {html.escape(pseudo)},</p><h1 style="font-size:22px;color:#2e5e4e;margin:0 0 12px">La semaine sur le marché en quelques chiffres</h1><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{_metric('BVMAC-AS',_fmt_pct(i['performance']),f"S-1 : {_fmt_pct(ip['performance'])}",idx_positive)}{_metric('Valeur échangée',_fmt_num(a['value_traded'])+' XAF',_comparison_text(a['value_traded'],ap['value_traded']),value_delta is not None and value_delta>=0)}</tr><tr>{_metric('Actions en hausse',str(a['up']),f"S-1 : {ap['up']}",a['up']>=ap['up'])}{_metric('OPCVM en hausse',f"{f['up']} / {f['count']}" if f['count'] else '—',f"S-1 : {fp['up']} / {fp['count']}",f['up']>=fp['up'] if f['count'] else None)}</tr></table><h2 style="font-size:17px;margin:22px 0 8px">Ce qu’il faut retenir</h2><ul style="padding-left:20px;line-height:1.5">{insights or '<li>Données insuffisantes pour établir des faits saillants sur cette période.</li>'}</ul><h2 style="font-size:17px;margin:22px 0 8px">Indice BVMAC-AS</h2>{index_img}<p style="color:#67726c">Performance semaine : <b>{_fmt_pct(i['performance'])}</b> · semaine précédente : {_fmt_pct(ip['performance'])}.</p><h2 style="font-size:17px;margin:22px 0 8px">Marché actions · performance</h2><p style="color:#67726c">{a['up']} hausse(s) · {a['flat']} stable(s) · {a['down']} baisse(s) · médiane {_fmt_pct(a['median_performance'])}. S-1 : {ap['up']} / {ap['flat']} / {ap['down']}.</p>{_top_rows(a['top'])}<h2 style="font-size:17px;margin:22px 0 8px">Liquidité et activité</h2>{activity_img}<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{_metric('Volume échangé',_fmt_num(a['volume_traded']),_comparison_text(a['volume_traded'],ap['volume_traded']),traded_delta is not None and traded_delta>=0)}{_metric('Transactions',_fmt_num(a['transactions']),_comparison_text(a['transactions'],ap['transactions']),_delta(a['transactions'],ap['transactions']) is not None and _delta(a['transactions'],ap['transactions'])>=0)}</tr><tr>{_metric('Demande moyenne / séance',_fmt_num(a['avg_bid']),_comparison_text(a['avg_bid'],ap['avg_bid']),bid_delta is not None and bid_delta>=0)}{_metric('Offre moyenne / séance',_fmt_num(a['avg_ask']),_comparison_text(a['avg_ask'],ap['avg_ask']),ask_delta is not None and ask_delta<=0)}</tr></table><p style="font-size:12px;color:#67726c;line-height:1.5">Demande et offre sont les volumes publiés, agrégés par séance puis moyennés. Le volume échangé correspond aux titres effectivement déclarés comme échangés.</p><h2 style="font-size:17px;margin:22px 0 8px">OPCVM · progressions</h2><p style="color:#67726c">{f['up']} fonds en progression sur {f['count']} calculables · médiane {_fmt_pct(f['median_performance'])}.</p>{_top_rows(f['top'])}<h2 style="font-size:17px;margin:22px 0 8px">Radar prédictif</h2><p style="font-size:12px;color:#67726c">Situation au dernier bulletin disponible ({html.escape(radar_date)}). Prévisions probabilistes calibrées, distinctes des performances de la semaine passée.</p>{radar_img}{_radar_rows(summary)}<div style="margin:24px 0 8px;text-align:center"><a href="{PUBLIC_ORIGIN}/app?view=radar" style="display:inline-block;padding:12px 18px;background:#2e5e4e;color:#fff;border-radius:7px;text-decoration:none;font-weight:700">Ouvrir le Radar prédictif</a></div><p style="text-align:center;font-size:13px"><a href="{PUBLIC_ORIGIN}/app?view=overview" style="color:#2e5e4e">Synthèse</a> · <a href="{PUBLIC_ORIGIN}/app?view=stocks" style="color:#2e5e4e">Actions</a> · <a href="{PUBLIC_ORIGIN}/app?view=funds" style="color:#2e5e4e">OPCVM</a> · <a href="{PUBLIC_ORIGIN}/app?view=index" style="color:#2e5e4e">Indice</a></p><p style="margin-top:26px;font-size:12px;color:#67726c;line-height:1.5">Les graphiques et tableaux sont générés à partir des Bulletins Officiels de la Cote et du dernier calcul Radar disponible. Les prévisions ML sont probabilistes et ne constituent pas un conseil en investissement.</p><p style="font-size:12px;color:#67726c">Tu reçois cette synthèse car elle est activée par défaut sur ton compte. <a href="{unsub}" style="color:#2e5e4e">Gérer ou désactiver les emails hebdomadaires</a>.</p></td></tr><tr><td style="border-top:1px solid #d9ded8;padding:14px 22px;color:#67726c;font-size:11px">Initiative personnelle indépendante, sans affiliation à la BVMAC. Données sources : Bulletins Officiels de la Cote.</td></tr></table></td></tr></table></body></html>'''
    plain=[f'BVMAC Hebdo — {p["start"].strftime("%d/%m/%Y")} au {p["end"].strftime("%d/%m/%Y")}', '', *[f'- {x}' for x in _insights(summary)], '', f'Actions : {a["up"]} hausses, {a["flat"]} stables, {a["down"]} baisses.', f'Valeur échangée : {_fmt_num(a["value_traded"])} XAF ({_comparison_text(a["value_traded"],ap["value_traded"])}).', f'Volume échangé : {_fmt_num(a["volume_traded"])} ({_comparison_text(a["volume_traded"],ap["volume_traded"])}).', f'OPCVM : {f["up"]} fonds en hausse sur {f["count"]} calculables.', '', 'Radar : '+PUBLIC_ORIGIN+'/app?view=radar', 'Préférences : '+unsub]
    return subject,html_body,'\n'.join(plain)


def _message(sender: str, recipient: str, subject: str, html_body: str, plain_body: str, charts: dict[str,bytes] | None = None):
    root=MIMEMultipart('related');root['From']=sender;root['To']=recipient;root['Subject']=subject[:180]
    alt=MIMEMultipart('alternative');alt.attach(MIMEText(plain_body,'plain','utf-8'));alt.attach(MIMEText(html_body,'html','utf-8'));root.attach(alt)
    for cid,payload in (charts or {}).items():
        part=MIMEImage(payload,_subtype='png');part.add_header('Content-ID',f'<{cid}>');part.add_header('Content-Disposition','inline',filename=f'{cid}.png');root.attach(part)
    return root

def create_run(conn, *, trigger: str, requested_by: str | None = None, now: datetime | None = None) -> int:
    start, end, prev_start, prev_end = period_bounds(now)
    if trigger == 'scheduled':
        existing = conn.execute("SELECT run_id FROM admin.weekly_email_run WHERE trigger='scheduled' AND period_start=%s ORDER BY run_id DESC LIMIT 1", (start,)).fetchone()
        if existing:
            return int(existing[0])
    row = conn.execute('''INSERT INTO admin.weekly_email_run(period_start,period_end,previous_start,previous_end,trigger,requested_by)
                          VALUES(%s,%s,%s,%s,%s,%s) RETURNING run_id''',
                       (start, end, prev_start, prev_end, trigger, requested_by)).fetchone()
    conn.commit()
    return int(row[0])


def process_run(conn, run_id: int) -> dict:
    run = conn.execute('''SELECT period_start,period_end,previous_start,previous_end,trigger,status,started_at FROM admin.weekly_email_run
                          WHERE run_id=%s FOR UPDATE''', (run_id,)).fetchone()
    if not run:
        raise RuntimeError('Campagne hebdomadaire introuvable')
    if run[5] == 'completed':
        row = conn.execute('SELECT recipient_count,sent_count,failed_count,subject FROM admin.weekly_email_run WHERE run_id=%s', (run_id,)).fetchone()
        conn.commit()
        return {'run_id': run_id, 'recipients': row[0], 'sent': row[1], 'failed': row[2], 'subject': row[3], 'already_completed': True}
    if run[5] == 'running' and run[6] is not None:
        started_at = run[6]
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=ZoneInfo('UTC'))
        if datetime.now(ZoneInfo('UTC')) - started_at.astimezone(ZoneInfo('UTC')) < timedelta(minutes=30):
            conn.commit()
            return {'run_id': run_id, 'status': 'running', 'already_running': True}
    conn.execute("UPDATE admin.weekly_email_run SET status='running',started_at=now(),completed_at=NULL,error=NULL WHERE run_id=%s", (run_id,))
    conn.commit()
    start, end, prev_start, prev_end = run[:4]
    summary = build_summary(conn, start, end, prev_start, prev_end)
    config = mail_settings(conn)
    login_address = parseaddr(config.get('sender') or '')[1]
    if not config.get('password') or not login_address:
        conn.execute("UPDATE admin.weekly_email_run SET status='failed',completed_at=now(),error=%s WHERE run_id=%s", ('Envoi email non configuré', run_id))
        conn.commit()
        raise RuntimeError('Envoi email non configuré')
    recipients = conn.execute('''SELECT u.user_id,u.username,u.email_cipher
                                 FROM auth.user_account u
                                 LEFT JOIN auth.email_preference p ON p.user_id=u.user_id
                                 WHERE u.status='active' AND u.email_verified_at IS NOT NULL
                                   AND coalesce(p.weekly_market_summary,true)=true
                                 ORDER BY u.user_id''').fetchall()
    subject = _subject(summary)
    charts = _chart_pngs(summary)
    conn.execute('UPDATE admin.weekly_email_run SET recipient_count=%s,subject=%s WHERE run_id=%s', (len(recipients), subject, run_id))
    conn.commit()
    sent = failed = 0
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as server:
            server.ehlo(); server.starttls(context=ssl.create_default_context()); server.ehlo(); server.login(login_address, config['password'])
            for user_id, pseudo, cipher in recipients:
                exists = conn.execute('SELECT status FROM admin.weekly_email_delivery WHERE run_id=%s AND user_id=%s', (run_id, user_id)).fetchone()
                if exists and exists[0] == 'sent':
                    sent += 1
                    continue
                email = decrypt_pii(cipher)
                if not email:
                    failed += 1
                    conn.execute('''INSERT INTO admin.weekly_email_delivery(run_id,user_id,status,error,attempted_at)
                                    VALUES(%s,%s,'failed','Adresse email indéchiffrable',now())
                                    ON CONFLICT(run_id,user_id) DO UPDATE SET status='failed',error=EXCLUDED.error,attempted_at=now()''', (run_id, user_id))
                    conn.commit(); continue
                _, html_body, plain_body = render_email(summary, pseudo, set(charts))
                try:
                    server.send_message(_message(config['sender'], email, subject, html_body, plain_body, charts))
                    sent += 1
                    conn.execute('''INSERT INTO admin.weekly_email_delivery(run_id,user_id,status,error,attempted_at,sent_at)
                                    VALUES(%s,%s,'sent',NULL,now(),now())
                                    ON CONFLICT(run_id,user_id) DO UPDATE SET status='sent',error=NULL,attempted_at=now(),sent_at=now()''', (run_id, user_id))
                except (smtplib.SMTPException, TimeoutError, OSError) as exc:
                    failed += 1
                    conn.execute('''INSERT INTO admin.weekly_email_delivery(run_id,user_id,status,error,attempted_at)
                                    VALUES(%s,%s,'failed',%s,now())
                                    ON CONFLICT(run_id,user_id) DO UPDATE SET status='failed',error=EXCLUDED.error,attempted_at=now()''', (run_id, user_id, str(exc)[:500]))
                conn.commit()
    except (smtplib.SMTPException, TimeoutError, OSError) as exc:
        conn.execute("UPDATE admin.weekly_email_run SET status='failed',completed_at=now(),sent_count=%s,failed_count=%s,error=%s WHERE run_id=%s", (sent, failed, str(exc)[:1000], run_id))
        conn.commit()
        raise
    conn.execute("UPDATE admin.weekly_email_run SET status='completed',completed_at=now(),sent_count=%s,failed_count=%s,error=NULL WHERE run_id=%s", (sent, failed, run_id))
    conn.commit()
    return {'run_id': run_id, 'recipients': len(recipients), 'sent': sent, 'failed': failed, 'subject': subject}


def process_run_by_id(run_id: int) -> dict:
    with db_connect() as conn:
        return process_run(conn, run_id)


def process_pending_runs(conn, limit: int = 3):
    ids = [int(r[0]) for r in conn.execute("SELECT run_id FROM admin.weekly_email_run WHERE (status='queued' OR (status='running' AND started_at<now()-interval '30 minutes')) ORDER BY run_id LIMIT %s", (limit,)).fetchall()]
    results = []
    for run_id in ids:
        try:
            results.append(process_run(conn, run_id))
        except Exception as exc:
            results.append({'run_id': run_id, 'error': str(exc)})
    return results


@router.get('/preferences')
def get_preferences(request: Request):
    user = require_user(request)
    with db_connect() as conn:
        row = conn.execute('SELECT weekly_market_summary,updated_at FROM auth.email_preference WHERE user_id=%s', (user['user_id'],)).fetchone()
    return {'weekly_market_summary': bool(row[0]) if row else True, 'updated_at': row[1] if row else None,
            'schedule': {'weekday': 'monday', 'time': '09:00', 'timezone': 'Africa/Douala'}}


@router.post('/preferences')
def set_preferences(data: EmailPreferencesIn, request: Request):
    user = require_user(request, csrf=True)
    with db_connect() as conn:
        conn.execute('''INSERT INTO auth.email_preference(user_id,weekly_market_summary) VALUES(%s,%s)
                        ON CONFLICT(user_id) DO UPDATE SET weekly_market_summary=EXCLUDED.weekly_market_summary,updated_at=now()''',
                     (user['user_id'], data.weekly_market_summary))
        conn.commit()
    return {'ok': True, 'weekly_market_summary': data.weekly_market_summary}
