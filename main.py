import base64
import csv
import io
import json
import os
from datetime import datetime

import requests as _http
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

# ── Config ─────────────────────────────────────────────────────────────────────

DATA_FILE  = os.environ.get('DATA_FILE', os.path.join(os.path.dirname(__file__), 'data.json'))
SECRET_KEY = os.environ.get('SECRET_KEY', 'payback-dev-secret-change-me')
_serializer = URLSafeTimedSerializer(SECRET_KEY)
PAGE_SIZE = 10

# GitHub storage (optional — set all three env vars to enable)
_GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
_GH_REPO  = os.environ.get('GITHUB_REPO', '')   # "owner/repo-name"
_GH_PATH  = os.environ.get('GITHUB_PATH', 'data.json')
_gh_sha   = None  # cached blob SHA; required by GitHub API to update a file


def _gh_headers():
    return {'Authorization': f'token {_GH_TOKEN}', 'Accept': 'application/vnd.github+json'}


def _gh_enabled():
    return bool(_GH_TOKEN and _GH_REPO)

# ── In-memory state ────────────────────────────────────────────────────────────

trips = []
expenses = []
settlements = []
users = []
_next_id = 1
_next_user_id = 1
_next_trip_id = 1


# ── Persistence ────────────────────────────────────────────────────────────────

def _make_default_trip(owner_id, name='My Trip', trip_id=1):
    return {
        'id':             trip_id,
        'name':           name,
        'owner_id':       owner_id,
        'member_user_ids': [owner_id],
        'destination':    '',
        'start_date':     datetime.today().strftime('%Y-%m-%d'),
        'end_date':       datetime.today().strftime('%Y-%m-%d'),
        'currency':       'USD',
        'cover':          '✈️',
        'description':    '',
        'budget':         0.0,
        'split_method':   'equal',
        'members':        [],
    }


def _apply_data(d):
    """Populate in-memory state from a parsed data dict."""
    global _next_id, _next_user_id, _next_trip_id
    users[:] = d.get('users', [])
    _next_user_id = d.get('next_user_id', 1)
    _next_id      = d.get('next_id', 1)
    _next_trip_id = d.get('next_trip_id', 1)

    # ── Migrate old single-trip format ──────────────────────────────────
    if 'trip' in d and 'trips' not in d:
        old = d['trip']
        old['id']              = 1
        old['owner_id']        = users[0]['id'] if users else 1
        old['member_user_ids'] = [u['id'] for u in users]
        if 'members' not in old:
            old['members'] = []
        owner = next((u for u in users if u['id'] == old['owner_id']), None)
        if owner and owner['name'] not in old['members']:
            old['members'].insert(0, owner['name'])
        trips[:] = [old]
        _next_trip_id = max(_next_trip_id, 2)
        raw_expenses    = d.get('expenses', [])
        raw_settlements = d.get('settlements', [])
        for e in raw_expenses:
            e.setdefault('trip_id', 1)
        for s in raw_settlements:
            s.setdefault('trip_id', 1)
        expenses[:]    = raw_expenses
        settlements[:] = raw_settlements
    else:
        trips[:]       = d.get('trips', [])
        expenses[:]    = d.get('expenses', [])
        settlements[:] = d.get('settlements', [])


def _load(silent=False):
    global trips, expenses, settlements, users, _next_id, _next_user_id, _next_trip_id, _gh_sha
    if _gh_enabled():
        try:
            url = f'https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PATH}'
            r = _http.get(url, headers=_gh_headers(), timeout=10)
            if r.status_code == 200:
                blob = r.json()
                _gh_sha = blob['sha']
                raw = base64.b64decode(blob['content'])
                _apply_data(json.loads(raw))
                if not silent:
                    print(f'[PayBack] Loaded data from GitHub ({_GH_REPO}/{_GH_PATH})')
                return
            elif r.status_code == 404:
                if not silent:
                    print('[PayBack] No data file in GitHub yet — starting fresh')
                return
            else:
                print(f'[PayBack] GitHub load failed ({r.status_code}) — falling back to local file')
        except Exception as e:
            print(f'[PayBack] GitHub load error: {e} — falling back to local file')

    # Local fallback
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE) as f:
            _apply_data(json.load(f))
    except Exception as e:
        print(f'[PayBack] Failed to load data: {e}')


def _save():
    global _gh_sha
    data = {
        'trips':        trips,
        'expenses':     expenses,
        'settlements':  settlements,
        'users':        users,
        'next_id':      _next_id,
        'next_user_id': _next_user_id,
        'next_trip_id': _next_trip_id,
    }
    json_bytes = json.dumps(data, indent=2).encode()

    if _gh_enabled():
        try:
            url     = f'https://api.github.com/repos/{_GH_REPO}/contents/{_GH_PATH}'
            payload = {
                'message': 'chore: update data',
                'content': base64.b64encode(json_bytes).decode(),
            }
            if _gh_sha:
                payload['sha'] = _gh_sha
            r = _http.put(url, json=payload, headers=_gh_headers(), timeout=15)
            if r.status_code == 409:
                # SHA mismatch — someone else wrote first; re-fetch SHA and retry once
                r2 = _http.get(url, headers=_gh_headers(), timeout=10)
                if r2.status_code == 200:
                    _gh_sha = r2.json()['sha']
                    payload['sha'] = _gh_sha
                    r = _http.put(url, json=payload, headers=_gh_headers(), timeout=15)
            if r.status_code in (200, 201):
                _gh_sha = r.json()['content']['sha']
                return
            else:
                print(f'[PayBack] GitHub save failed ({r.status_code}): {r.text[:200]}')
        except Exception as e:
            print(f'[PayBack] GitHub save error: {e}')

    # Local fallback
    with open(DATA_FILE, 'w') as f:
        f.write(json_bytes.decode())


_load()


# ── Users ──────────────────────────────────────────────────────────────────────

def get_user_by_email(email):
    email = email.strip().lower()
    return next((u for u in users if u['email'] == email), None)


def get_user_by_id(user_id):
    return next((u for u in users if u['id'] == user_id), None)


def create_user(name, email, password):
    global _next_user_id
    email = email.strip().lower()
    if get_user_by_email(email):
        return None, 'Email already registered'
    user = {
        'id':            _next_user_id,
        'name':          name.strip(),
        'email':         email,
        'password_hash': generate_password_hash(password),
        'created_at':    datetime.today().strftime('%Y-%m-%d'),
    }
    _next_user_id += 1
    users.append(user)
    _save()
    return user, None


def verify_user(email, password):
    user = get_user_by_email(email)
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None


# ── Trips ──────────────────────────────────────────────────────────────────────

def get_trip(trip_id):
    return next((t for t in trips if t['id'] == trip_id), None)


def get_user_trips(user_id):
    """Return all trips the user owns or is a member of."""
    return [t for t in trips if user_id in t.get('member_user_ids', [])]


def create_trip(owner_id, name='My Trip'):
    global _next_trip_id
    owner = get_user_by_id(owner_id)
    t = _make_default_trip(owner_id, name, _next_trip_id)
    if owner and owner['name'] not in t['members']:
        t['members'] = [owner['name']]
    _next_trip_id += 1
    trips.append(t)
    _save()
    return t


def save_trip(trip_id, trip_name, destination, start_date, end_date,
              currency, cover, description, budget, split_method):
    t = get_trip(trip_id)
    if not t:
        return
    t.update({
        'name':         trip_name.strip(),
        'destination':  destination.strip(),
        'start_date':   start_date,
        'end_date':     end_date,
        'currency':     currency,
        'cover':        cover,
        'description':  description.strip(),
        'budget':       round(float(str(budget).replace(',', '')), 2),
        'split_method': split_method,
    })
    _save()


def reset_expenses(trip_id):
    global expenses, settlements, _next_id
    expenses[:]    = [e for e in expenses    if e.get('trip_id') != trip_id]
    settlements[:] = [s for s in settlements if s.get('trip_id') != trip_id]
    _save()


def delete_trip(trip_id):
    global trips, expenses, settlements
    trips[:]       = [t for t in trips       if t['id'] != trip_id]
    expenses[:]    = [e for e in expenses    if e.get('trip_id') != trip_id]
    settlements[:] = [s for s in settlements if s.get('trip_id') != trip_id]
    _save()


# ── Members ────────────────────────────────────────────────────────────────────

def add_member(trip_id, display_name, user_id=None):
    t = get_trip(trip_id)
    if not t:
        return
    name = display_name.strip()
    if name and name not in t['members']:
        t['members'].append(name)
    if user_id and user_id not in t.get('member_user_ids', []):
        t.setdefault('member_user_ids', []).append(user_id)
    _save()


def remove_member(trip_id, name):
    t = get_trip(trip_id)
    if not t:
        return
    name = name.strip()
    if name in t['members']:
        t['members'].remove(name)
    _save()


def rename_member(trip_id, old_name, new_name):
    t = get_trip(trip_id)
    if not t:
        return
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name or old_name not in t['members']:
        return
    idx = t['members'].index(old_name)
    t['members'][idx] = new_name
    # Update all expenses that reference the old name
    for e in expenses:
        if e.get('trip_id') == trip_id:
            if e['paid_by'] == old_name:
                e['paid_by'] = new_name
            e['split'] = [new_name if m == old_name else m for m in e['split']]
    # Update settlements
    for s in settlements:
        if s.get('trip_id') == trip_id:
            if s['debtor'] == old_name:
                s['debtor'] = new_name
            if s['creditor'] == old_name:
                s['creditor'] = new_name
    _save()


# ── Invites ────────────────────────────────────────────────────────────────────

def make_invite_token(trip_id):
    return _serializer.dumps({'trip_id': trip_id}, salt='invite')


def verify_invite_token(token):
    try:
        payload = _serializer.loads(token, salt='invite', max_age=2592000)  # 30 days
        if isinstance(payload, dict) and 'trip_id' in payload:
            return payload
        return None
    except (BadSignature, SignatureExpired):
        return None


# ── Expenses ───────────────────────────────────────────────────────────────────

def get_expenses(trip_id, sort=None, search=None, cat_filter=None, page=1):
    result = [e for e in expenses if e.get('trip_id') == trip_id]

    if search:
        q = search.lower()
        result = [e for e in result if q in e['description'].lower()
                  or q in e.get('notes', '').lower()
                  or q in e['paid_by'].lower()]

    if cat_filter and cat_filter != 'all':
        result = [e for e in result if e['category'] == cat_filter]

    if sort == 'oldest':
        result.sort(key=lambda e: e['date'])
    elif sort == 'highest':
        result.sort(key=lambda e: e['amount'], reverse=True)
    elif sort == 'lowest':
        result.sort(key=lambda e: e['amount'])
    else:
        result.sort(key=lambda e: e['date'], reverse=True)

    total_count  = len(result)
    total_amount = round(sum(e['amount'] for e in result), 2)
    pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page  = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    # Key is 'expense_list' (not 'items') to avoid shadowing dict.items()
    expense_list = result[start:start + PAGE_SIZE]

    today_str      = datetime.today().strftime('%Y-%m-%d')
    today_expenses = [e for e in expenses
                      if e.get('trip_id') == trip_id and e['date'] == today_str]

    return {
        'expense_list': expense_list,
        'total_count':  total_count,
        'total_amount': total_amount,
        'page':         page,
        'pages':        pages,
        'page_size':    PAGE_SIZE,
        'today_count':  len(today_expenses),
        'today_total':  round(sum(e['amount'] for e in today_expenses), 2),
    }


def add_expense(trip_id, description, amount, date, paid_by, category, split, notes=''):
    global _next_id
    t = get_trip(trip_id)
    members = t['members'] if t else []
    expenses.append({
        'id':          _next_id,
        'trip_id':     trip_id,
        'description': description.strip(),
        'amount':      round(float(str(amount).replace(',', '')), 2),
        'date':        date,
        'paid_by':     paid_by,
        'category':    category,
        'split':       split if split else list(members),
        'notes':       notes.strip(),
    })
    _next_id += 1
    _save()


def get_expense(expense_id):
    return next((e for e in expenses if e['id'] == expense_id), None)


def update_expense(expense_id, **fields):
    for e in expenses:
        if e['id'] == expense_id:
            if 'amount' in fields:
                fields['amount'] = round(float(str(fields['amount']).replace(',', '')), 2)
            e.update(fields)
            _save()
            return True
    return False


def delete_expense(expense_id):
    global expenses
    before   = len(expenses)
    expenses = [e for e in expenses if e['id'] != expense_id]
    _save()
    return len(expenses) < before


def export_csv(trip_id):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Description', 'Amount', 'Date', 'Paid By', 'Category', 'Split', 'Notes'])
    trip_expenses = sorted(
        (e for e in expenses if e.get('trip_id') == trip_id),
        key=lambda x: x['date']
    )
    for e in trip_expenses:
        writer.writerow([
            e['id'], e['description'], f"{e['amount']:.2f}", e['date'],
            e['paid_by'], e['category'], ','.join(e['split']), e.get('notes', '')
        ])
    return output.getvalue()


# ── Balances ───────────────────────────────────────────────────────────────────

def get_balances(trip_id, member=None):
    t = get_trip(trip_id)
    members = t['members'] if t else []
    if not members:
        return {
            'members': {}, 'debts': [], 'total': 0,
            'per_person': 0, 'category_totals': {}, 'transactions': []
        }

    trip_expenses = [e for e in expenses if e.get('trip_id') == trip_id]

    paid       = {m: 0.0 for m in members}
    share_owed = {m: 0.0 for m in members}
    count      = {m: 0   for m in members}
    cat_totals = {}
    transactions = []

    for e in trip_expenses:
        amt        = e['amount']
        payer      = e['paid_by']
        split_list = [m for m in e['split'] if m in members]
        if not split_list:
            split_list = list(members)
        per_share = amt / len(split_list)

        if payer in paid:
            paid[payer]  += amt
            count[payer] += 1

        for m in split_list:
            share_owed[m] += per_share

        cat = e['category']
        cat_totals[cat] = cat_totals.get(cat, 0) + amt

        txn_splits = {}
        for m in members:
            in_split = m in split_list
            is_payer = m == payer
            if is_payer and in_split:
                txn_splits[m] = amt - per_share
            elif is_payer:
                txn_splits[m] = amt
            elif in_split:
                txn_splits[m] = -per_share
            else:
                txn_splits[m] = 0.0

        transactions.append({
            'date':        e['date'],
            'description': e['description'],
            'paid_by':     payer,
            'amount':      amt,
            'splits':      txn_splits,
        })

    transactions.sort(key=lambda t: t['date'], reverse=True)

    if member and member != 'all' and member in members:
        transactions = [
            t for t in transactions
            if t['paid_by'] == member or t['splits'].get(member, 0) != 0
        ]

    total      = round(sum(paid.values()), 2)
    n          = len(members)
    per_person = round(total / n, 2) if n else 0
    net        = {m: round(paid[m] - share_owed[m], 2) for m in members}

    member_stats = {
        m: {
            'paid':  round(paid[m], 2),
            'share': round(share_owed[m], 2),
            'net':   net[m],
            'count': count[m],
            'pct':   round(paid[m] / total * 100, 1) if total else 0,
        }
        for m in members
    }

    creds     = [(m, net[m])  for m in members if net[m] >  0.005]
    debts_raw = [(m, -net[m]) for m in members if net[m] < -0.005]
    creds.sort(key=lambda x: x[1], reverse=True)
    debts_raw.sort(key=lambda x: x[1], reverse=True)

    debts = []
    ci, di = 0, 0
    while ci < len(creds) and di < len(debts_raw):
        creditor, c_amt = creds[ci]
        debtor,   d_amt = debts_raw[di]
        settle = min(c_amt, d_amt)
        debts.append({'debtor': debtor, 'creditor': creditor, 'amount': round(settle, 2)})
        creds[ci]     = (creditor, c_amt - settle)
        debts_raw[di] = (debtor,   d_amt - settle)
        if creds[ci][1]     < 0.005: ci += 1
        if debts_raw[di][1] < 0.005: di += 1

    cat_breakdown = {}
    if total > 0:
        for cat, amt in sorted(cat_totals.items(), key=lambda x: x[1], reverse=True):
            cat_breakdown[cat] = {
                'amount': round(amt, 2),
                'pct':    round(amt / total * 100),
            }

    return {
        'members':         member_stats,
        'debts':           debts,
        'total':           total,
        'per_person':      per_person,
        'category_totals': cat_breakdown,
        'transactions':    transactions,
    }


# ── Settle Up ──────────────────────────────────────────────────────────────────

def get_settlements(trip_id):
    return [s for s in settlements if s.get('trip_id') == trip_id]


def settle_debt(trip_id, debtor, creditor):
    balance_data = get_balances(trip_id)
    amount = next(
        (d['amount'] for d in balance_data['debts']
         if d['debtor'] == debtor and d['creditor'] == creditor),
        0.0
    )
    today = datetime.today().strftime('%Y-%m-%d')
    for s in settlements:
        if (s.get('trip_id') == trip_id
                and s['debtor'] == debtor and s['creditor'] == creditor):
            s.update({'status': 'settled', 'settled_on': today, 'amount': amount})
            _save()
            return
    settlements.append({
        'trip_id':    trip_id,
        'debtor':     debtor,
        'creditor':   creditor,
        'amount':     amount,
        'status':     'settled',
        'settled_on': today,
    })
    _save()


def mark_all_settled(trip_id):
    balance_data     = get_balances(trip_id)
    trip_settlements = get_settlements(trip_id)
    settled_amounts  = {(s['debtor'], s['creditor']): s['amount']
                        for s in trip_settlements if s['status'] == 'settled'}
    today = datetime.today().strftime('%Y-%m-%d')
    for debt in balance_data['debts']:
        key = (debt['debtor'], debt['creditor'])
        remaining = round(debt['amount'] - settled_amounts.get(key, 0), 2)
        if remaining > 0.005:
            # Update existing settlement record or create a new one
            for s in settlements:
                if (s.get('trip_id') == trip_id
                        and s['debtor'] == debt['debtor']
                        and s['creditor'] == debt['creditor']):
                    s.update({'status': 'settled', 'settled_on': today,
                              'amount': debt['amount']})
                    break
            else:
                settlements.append({
                    'trip_id':    trip_id,
                    'debtor':     debt['debtor'],
                    'creditor':   debt['creditor'],
                    'amount':     debt['amount'],
                    'status':     'settled',
                    'settled_on': today,
                })
    _save()


