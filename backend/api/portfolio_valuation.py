"""Valuation of a fixed, virtual allocation. No trades, fees or dividends."""
from bisect import bisect_right
from datetime import date


def value_portfolio(start_date, initial_amount, cash, positions, histories, *,
                    as_of=None, period_start=None, period_end=None, last_only=False):
    as_of = as_of or date.today()
    initial_amount, cash = float(initial_amount), float(cash)
    prices = {p['company_id']: float(p['start_price']) for p in positions}
    price_dates = {p['company_id']: p.get('start_price_date') for p in positions}
    events = {}
    for cid, rows in histories.items():
        for row in rows:
            day = date.fromisoformat(str(row['date'])[:10])
            price = float(row['close'])
            if start_date <= day <= as_of and price > 0:
                events.setdefault(day, {})[cid] = price
    events.setdefault(start_date, {})
    curve = []
    for day in sorted(events):
        for cid, price in events[day].items():
            prices[cid], price_dates[cid] = price, day.isoformat()
        total = cash + sum(prices[p['company_id']] * float(p['quantity']) for p in positions)
        curve.append({'date': day.isoformat(), 'value': round(total, 2),
                      'gain': round(total-initial_amount, 2),
                      'performance_pct': round((total/initial_amount-1)*100, 4)})
    latest = curve[-1]
    enriched = []
    for p in positions:
        cid = p['company_id']
        value = prices[cid] * float(p['quantity'])
        invested = float(p['allocated_amount'])
        source_date = price_dates[cid]
        enriched.append({**p, 'last_price': prices[cid], 'price_date': source_date,
                         'price_age_days': (as_of-date.fromisoformat(str(source_date)[:10])).days if source_date else None,
                         'value': round(value, 2), 'gain': round(value-invested, 2),
                         'performance_pct': round((value/invested-1)*100, 4) if invested else None,
                         'weight_pct': round(value/latest['value']*100, 4) if latest['value'] else None})
    # Period boundaries carry the last known valuation; they never use a future price.
    left = max(start_date, period_start or start_date)
    right = min(as_of, period_end or as_of)
    selected, period = [], None
    days = [date.fromisoformat(x['date']) for x in curve]
    def at(day):
        return {**curve[bisect_right(days, day)-1], 'date': day.isoformat()}
    if right >= left:
        selected = [at(left)] + [x for x in curve if left < date.fromisoformat(x['date']) < right]
        if right > left:
            selected.append(at(right))
        first, final = selected[0], selected[-1]
        period = {'start': left.isoformat(), 'end': right.isoformat(),
                  'gain': round(final['value']-first['value'], 2),
                  'performance_pct': round((final['value']/first['value']-1)*100, 4) if first['value'] else None}
        if last_only:
            selected, period = [dict(latest)], None
    known_dates = [str(d)[:10] for d in price_dates.values() if d]
    return {'latest': latest, 'as_of': as_of.isoformat(), 'curve': selected, 'period': period,
            'positions': enriched, 'oldest_price_date': min(known_dates) if known_dates else None,
            'has_new_quotes': any(day > start_date for day in events),
            'basis': 'Cours connus, hors frais, fiscalité et dividendes. Simulation sans passage d’ordre.'}
