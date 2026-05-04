import csv
import io
import os
from datetime import datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pymongo import MongoClient, ReturnDocument
from werkzeug.security import check_password_hash, generate_password_hash

# ── Config ─────────────────────────────────────────────────────────────────────

SECRET_KEY  = os.environ.get('SECRET_KEY', 'payback-dev-secret-change-me')
_serializer = URLSafeTimedSerializer(SECRET_KEY)
PAGE_SIZE   = 10

# ── MongoDB connection ─────────────────────────────────────────────────────────

_mongo_client = None
_mongo_db     = None


def _get_db():
    global _mongo_client, _mongo_db
    if _mongo_db is None:
        uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/tripsplitter')
        _mongo_client = MongoClient(uri)
        _mongo_db = _mongo_client.get_default_database()
    return _mongo_db


def _next_mongo_id(counter_name):
    """Atomically increment and return the next integer ID for a collection."""
    db = _get_db()
    result = db.counters.find_one_and_update(
        {'_id': counter_name},
        {'$inc': {'seq': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return result['seq']


# ── Users ──────────────────────────────────────────────────────────────────────

def get_user_by_email(email):
    db = _get_db()
    return db.users.find_one({'email': email.strip().lower()})


def get_user_by_id(user_id):
    if user_id is None:
        return None
    db = _get_db()
    return db.users.find_one({'_id': user_id})


def create_user(name, email, password):
    db = _get_db()
    email = email.strip().lower()
    if get_user_by_email(email):
        return None, 'Email already registered'
    user_id = _next_mongo_id('user_id')
    user = {
        '_id':           user_id,
        'id':            user_id,
        'name':          name.strip(),
        'email':         email,
        'password_hash': generate_password_hash(password),
        'created_at':    datetime.today().strftime('%Y-%m-%d'),
    }
    db.users.insert_one(user)
    return user, None


def verify_user(email, password):
    user = get_user_by_email(email)
    if user and check_password_hash(user['password_hash'], password):
        return user
    return None


# ── Trips ──────────────────────────────────────────────────────────────────────

def _make_default_trip(owner_id, name='My Trip', trip_id=1):
    return {
        '_id':             trip_id,
        'id':              trip_id,
        'name':            name,
        'owner_id':        owner_id,
        'member_user_ids': [owner_id],
        'destination':     '',
        'start_date':      datetime.today().strftime('%Y-%m-%d'),
        'end_date':        datetime.today().strftime('%Y-%m-%d'),
        'currency':        'USD',
        'cover':           '✈️',
        'description':     '',
        'budget':          0.0,
        'split_method':    'equal',
        'members':         [],
    }


def get_trip(trip_id):
    db = _get_db()
    return db.trips.find_one({'_id': trip_id})


def get_user_trips(user_id):
    """Return all trips the user owns or is a member of."""
    db = _get_db()
    return list(db.trips.find({'member_user_ids': user_id}))


def create_trip(owner_id, name='My Trip'):
    db = _get_db()
    trip_id = _next_mongo_id('trip_id')
    owner   = get_user_by_id(owner_id)
    t       = _make_default_trip(owner_id, name, trip_id)
    if owner and owner['name'] not in t['members']:
        t['members'] = [owner['name']]
    db.trips.insert_one(t)
    return t


def save_trip(trip_id, trip_name, destination, start_date, end_date,
              currency, cover, description, budget, split_method):
    db = _get_db()
    db.trips.update_one(
        {'_id': trip_id},
        {'$set': {
            'name':         trip_name.strip(),
            'destination':  destination.strip(),
            'start_date':   start_date,
            'end_date':     end_date,
            'currency':     currency,
            'cover':        cover,
            'description':  description.strip(),
            'budget':       round(float(str(budget).replace(',', '')), 2),
            'split_method': split_method,
        }}
    )


def reset_expenses(trip_id):
    db = _get_db()
    db.expenses.delete_many({'trip_id': trip_id})
    db.settlements.delete_many({'trip_id': trip_id})


def delete_trip(trip_id):
    db = _get_db()
    db.trips.delete_one({'_id': trip_id})
    db.expenses.delete_many({'trip_id': trip_id})
    db.settlements.delete_many({'trip_id': trip_id})


# ── Members ────────────────────────────────────────────────────────────────────

def add_member(trip_id, display_name, user_id=None):
    db   = _get_db()
    name = display_name.strip()
    if not name:
        return
    t = get_trip(trip_id)
    if not t:
        return
    update = {}
    if name not in t['members']:
        update['$push'] = {'members': name}
    if user_id and user_id not in t.get('member_user_ids', []):
        update.setdefault('$addToSet', {})['member_user_ids'] = user_id
    if update:
        db.trips.update_one({'_id': trip_id}, update)


def remove_member(trip_id, name):
    db   = _get_db()
    name = name.strip()
    if name:
        db.trips.update_one({'_id': trip_id}, {'$pull': {'members': name}})


def rename_member(trip_id, old_name, new_name):
    db       = _get_db()
    old_name = old_name.strip()
    new_name = new_name.strip()
    t = get_trip(trip_id)
    if not t or not new_name or old_name not in t['members']:
        return
    new_members = [new_name if m == old_name else m for m in t['members']]
    db.trips.update_one({'_id': trip_id}, {'$set': {'members': new_members}})
    db.expenses.update_many(
        {'trip_id': trip_id, 'paid_by': old_name},
        {'$set': {'paid_by': new_name}}
    )
    db.expenses.update_many(
        {'trip_id': trip_id, 'split': old_name},
        {'$set': {'split.$[elem]': new_name}},
        array_filters=[{'elem': {'$eq': old_name}}]
    )
    db.settlements.update_many(
        {'trip_id': trip_id, 'debtor': old_name},
        {'$set': {'debtor': new_name}}
    )
    db.settlements.update_many(
        {'trip_id': trip_id, 'creditor': old_name},
        {'$set': {'creditor': new_name}}
    )


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
    db    = _get_db()
    query = {'trip_id': trip_id}

    if cat_filter and cat_filter != 'all':
        query['category'] = cat_filter

    if search:
        q = search.strip()
        query['$or'] = [
            {'description': {'$regex': q, '$options': 'i'}},
            {'notes':       {'$regex': q, '$options': 'i'}},
            {'paid_by':     {'$regex': q, '$options': 'i'}},
        ]

    if sort == 'oldest':
        sort_spec = [('date', 1)]
    elif sort == 'highest':
        sort_spec = [('amount', -1)]
    elif sort == 'lowest':
        sort_spec = [('amount', 1)]
    else:
        sort_spec = [('date', -1)]

    total_count  = db.expenses.count_documents(query)
    agg_total    = list(db.expenses.aggregate([
        {'$match': query},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}},
    ]))
    total_amount = round(agg_total[0]['total'] if agg_total else 0, 2)

    pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page  = max(1, min(page, pages))
    skip  = (page - 1) * PAGE_SIZE

    expense_list = list(
        db.expenses.find(query).sort(sort_spec).skip(skip).limit(PAGE_SIZE)
    )

    today_str  = datetime.today().strftime('%Y-%m-%d')
    agg_today  = list(db.expenses.aggregate([
        {'$match': {'trip_id': trip_id, 'date': today_str}},
        {'$group': {'_id': None, 'count': {'$sum': 1}, 'total': {'$sum': '$amount'}}},
    ]))
    today_stats = agg_today[0] if agg_today else {'count': 0, 'total': 0}

    return {
        'expense_list': expense_list,
        'total_count':  total_count,
        'total_amount': total_amount,
        'page':         page,
        'pages':        pages,
        'page_size':    PAGE_SIZE,
        'today_count':  today_stats['count'],
        'today_total':  round(today_stats['total'], 2),
    }


def add_expense(trip_id, description, amount, date, paid_by, category, split, notes=''):
    db         = _get_db()
    expense_id = _next_mongo_id('expense_id')
    t          = get_trip(trip_id)
    members    = t['members'] if t else []
    db.expenses.insert_one({
        '_id':         expense_id,
        'id':          expense_id,
        'trip_id':     trip_id,
        'description': description.strip(),
        'amount':      round(float(str(amount).replace(',', '')), 2),
        'date':        date,
        'paid_by':     paid_by,
        'category':    category,
        'split':       split if split else list(members),
        'notes':       notes.strip(),
    })


def get_expense(expense_id):
    db = _get_db()
    return db.expenses.find_one({'_id': expense_id})


def update_expense(expense_id, **fields):
    db = _get_db()
    if 'amount' in fields:
        fields['amount'] = round(float(str(fields['amount']).replace(',', '')), 2)
    result = db.expenses.update_one({'_id': expense_id}, {'$set': fields})
    return result.matched_count > 0


def delete_expense(expense_id):
    db     = _get_db()
    result = db.expenses.delete_one({'_id': expense_id})
    return result.deleted_count > 0


def export_csv(trip_id):
    db     = _get_db()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Description', 'Amount', 'Date', 'Paid By', 'Category', 'Split', 'Notes'])
    for e in db.expenses.find({'trip_id': trip_id}).sort([('date', 1)]):
        writer.writerow([
            e['id'], e['description'], f"{e['amount']:.2f}", e['date'],
            e['paid_by'], e['category'], ','.join(e['split']), e.get('notes', '')
        ])
    return output.getvalue()


# ── Balances ───────────────────────────────────────────────────────────────────

def get_balances(trip_id, member=None):
    db      = _get_db()
    t       = get_trip(trip_id)
    members = t['members'] if t else []
    if not members:
        return {
            'members': {}, 'debts': [], 'total': 0,
            'per_person': 0, 'category_totals': {}, 'transactions': []
        }

    trip_expenses = list(db.expenses.find({'trip_id': trip_id}))

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

    transactions.sort(key=lambda x: x['date'], reverse=True)

    if member and member != 'all' and member in members:
        transactions = [
            x for x in transactions
            if x['paid_by'] == member or x['splits'].get(member, 0) != 0
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
    db = _get_db()
    return list(db.settlements.find({'trip_id': trip_id}))


def settle_debt(trip_id, debtor, creditor):
    db           = _get_db()
    balance_data = get_balances(trip_id)
    amount       = next(
        (d['amount'] for d in balance_data['debts']
         if d['debtor'] == debtor and d['creditor'] == creditor),
        0.0
    )
    today = datetime.today().strftime('%Y-%m-%d')
    db.settlements.update_one(
        {'trip_id': trip_id, 'debtor': debtor, 'creditor': creditor},
        {'$set': {'status': 'settled', 'settled_on': today, 'amount': amount}},
        upsert=True,
    )


def mark_all_settled(trip_id):
    db           = _get_db()
    balance_data = get_balances(trip_id)
    existing     = {
        (s['debtor'], s['creditor']): s['amount']
        for s in db.settlements.find({'trip_id': trip_id, 'status': 'settled'})
    }
    today = datetime.today().strftime('%Y-%m-%d')
    for debt in balance_data['debts']:
        key       = (debt['debtor'], debt['creditor'])
        remaining = round(debt['amount'] - existing.get(key, 0), 2)
        if remaining > 0.005:
            db.settlements.update_one(
                {'trip_id': trip_id, 'debtor': debt['debtor'], 'creditor': debt['creditor']},
                {'$set': {'status': 'settled', 'settled_on': today, 'amount': debt['amount']}},
                upsert=True,
            )
