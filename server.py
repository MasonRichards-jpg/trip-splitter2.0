import os
from datetime import date, datetime
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

import main

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'payback-dev-secret-change-me')

# ── Auth helpers ───────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        # User was deleted or data was reset — kill the stale session
        if _current_user() is None:
            session.clear()
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def _current_user():
    uid = session.get('user_id')
    return main.get_user_by_id(uid) if uid else None


def _active_trip_id():
    return session.get('active_trip_id')


def _active_trip():
    tid = _active_trip_id()
    return main.get_trip(tid) if tid else None


def _set_active_trip(user_id):
    """Set session active_trip_id to user's first trip, creating one if needed."""
    user_trips = main.get_user_trips(user_id)
    if user_trips:
        session['active_trip_id'] = user_trips[0]['id']
    else:
        t = main.create_trip(user_id, 'My Trip')
        session['active_trip_id'] = t['id']


def _require_owner():
    """Abort 403 if current user is not the active trip owner."""
    t  = _active_trip()
    cu = _current_user()
    if not t or not cu or t.get('owner_id') != cu['id']:
        abort(403)
    return t, cu


# ── Template filters ────────────────────────────────────────────────────────────

@app.template_filter('fmtdate')
def fmtdate(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%b %d')
    except Exception:
        return value


@app.template_filter('currency')
def currency_filter(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return str(value)


@app.template_filter('signed_currency')
def signed_currency(value):
    try:
        v = float(value)
        return f"+${v:,.2f}" if v >= 0 else f"−${abs(v):,.2f}"
    except Exception:
        return str(value)


@app.template_filter('abs')
def abs_filter(value):
    try:
        return abs(float(value))
    except Exception:
        return value


# ── Template globals ─────────────────────────────────────────────────────────────

MEMBER_COLORS = ['#6366f1', '#ec4899', '#14b8a6', '#f59e0b', '#22c55e',
                 '#64748b', '#8b5cf6', '#f97316']


@app.context_processor
def inject_globals():
    t        = _active_trip() or {}
    members  = t.get('members', [])
    color_map = {m: MEMBER_COLORS[i % len(MEMBER_COLORS)] for i, m in enumerate(members)}
    cu       = _current_user()
    is_owner = bool(t and cu and t.get('owner_id') == cu['id'])
    return {
        'today':         date.today().strftime('%Y-%m-%d'),
        'member_colors': color_map,
        'current_user':  cu,
        'is_owner':      is_owner,
    }


@app.template_global()
def member_color(name):
    t = _active_trip() or {}
    members = t.get('members', [])
    try:
        idx = members.index(name)
    except ValueError:
        idx = abs(hash(name))
    return MEMBER_COLORS[idx % len(MEMBER_COLORS)]


# ── Auth routes ──────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    invite_token = request.args.get('invite', '')
    error = None
    if request.method == 'POST':
        invite_token = request.form.get('invite_token', invite_token)
        user = main.verify_user(
            request.form.get('email', ''),
            request.form.get('password', '')
        )
        if user:
            session['user_id'] = user['id']
            if invite_token:
                payload = main.verify_invite_token(invite_token)
                trip_id = payload.get('trip_id') if payload else None
                trip    = main.get_trip(trip_id) if trip_id else None
                if trip:
                    main.add_member(trip_id, user['name'], user_id=user['id'])
                    session['active_trip_id'] = trip_id
                    return redirect(url_for('index'))
            _set_active_trip(user['id'])
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        error = 'Invalid email or password'
    return render_template('login.html', error=error, invite_token=invite_token)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))

    invite_token  = request.args.get('invite', '')
    prefill_email = request.args.get('email', '')
    invite_payload = None

    if invite_token:
        invite_payload = main.verify_invite_token(invite_token)
        if invite_payload:
            prefill_email = invite_payload.get('email', prefill_email)
        else:
            invite_token = ''  # expired / invalid

    error = None
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')
        post_tok = request.form.get('invite_token', '')
        # Restore invite_token so it survives validation errors and re-renders correctly
        invite_token = post_tok

        if not name or not email or not password:
            error = 'All fields are required'
        elif password != confirm:
            error = 'Passwords do not match'
        elif len(password) < 6:
            error = 'Password must be at least 6 characters'
        else:
            # Check if email is already registered (existing user with invite)
            existing = main.get_user_by_email(email)
            if existing and post_tok:
                error = 'That email already has an account. Sign in to join the trip.'
                # pass invite_token through so template shows the login link
            elif existing:
                error = 'Email already registered'
            else:
                user, err = main.create_user(name, email, password)
                if err:
                    error = err
                else:
                    session['user_id'] = user['id']

                    payload = main.verify_invite_token(post_tok) if post_tok else None
                    trip_id = payload.get('trip_id') if payload else None
                    trip    = main.get_trip(trip_id) if trip_id else None

                    if trip:
                        main.add_member(trip_id, name, user_id=user['id'])
                        session['active_trip_id'] = trip_id
                    else:
                        new_trip = main.create_trip(user['id'], 'My Trip')
                        session['active_trip_id'] = new_trip['id']

                    return redirect(url_for('index'))

    return render_template(
        'register.html',
        error=error,
        prefill_email=prefill_email,
        invite_token=invite_token,
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Overview ───────────────────────────────────────────────────────────────────

@app.route('/')
@app.route('/index')
@login_required
def index():
    t            = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    balances      = main.get_balances(t['id'])
    expense_data  = main.get_expenses(t['id'], sort='newest')
    recent        = expense_data['expense_list'][:6]
    all_s         = main.get_settlements(t['id'])
    settled_amounts = {(s['debtor'], s['creditor']): s['amount']
                       for s in all_s if s['status'] == 'settled'}
    pending_debts = []
    for d in balances['debts']:
        remaining = round(d['amount'] - settled_amounts.get((d['debtor'], d['creditor']), 0), 2)
        if remaining > 0.005:
            pending_debts.append({**d, 'amount': remaining})
    unsettled_amount = round(sum(d['amount'] for d in pending_debts), 2)
    return render_template(
        'index.html',
        trip=t,
        balances=balances,
        recent_expenses=recent,
        pending_debts=pending_debts,
        unsettled_amount=unsettled_amount,
        total_count=expense_data['total_count'],
    )


# ── Expenses ───────────────────────────────────────────────────────────────────

@app.route('/expenses')
@login_required
def expenses():
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    sort       = request.args.get('sort', 'newest')
    search     = request.args.get('search', '')
    cat_filter = request.args.get('cat_filter', 'all')
    page       = int(request.args.get('page', 1))
    data = main.get_expenses(t['id'], sort=sort, search=search,
                             cat_filter=cat_filter, page=page)
    return render_template(
        'expenses.html',
        expenses=data,
        trip=t,
        sort=sort, search=search, cat_filter=cat_filter,
    )


@app.route('/expenses/add', methods=['POST'])
@login_required
def expenses_add():
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    main.add_expense(
        trip_id=t['id'],
        description=request.form['description'],
        amount=request.form['amount'],
        date=request.form['date'],
        paid_by=request.form['paid_by'],
        category=request.form['category'],
        split=request.form.getlist('split'),
        notes=request.form.get('notes', ''),
    )
    return redirect(url_for('expenses'))


@app.route('/expenses/export')
@login_required
def expenses_export():
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    csv_data = main.export_csv(t['id'])
    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition':
                 f'attachment; filename=expenses_{t["name"].replace(" ", "_")}.csv'}
    )


@app.route('/expenses/<int:expense_id>/edit')
@login_required
def expenses_edit(expense_id):
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    expense = main.get_expense(expense_id)
    if expense is None:
        return redirect(url_for('expenses'))
    return render_template(
        'expenses.html',
        editing=expense,
        expenses=main.get_expenses(t['id']),
        trip=t,
        sort='newest', search='', cat_filter='all',
    )


@app.route('/expenses/<int:expense_id>/update', methods=['POST'])
@login_required
def expenses_update(expense_id):
    main.update_expense(
        expense_id,
        description=request.form['description'],
        amount=request.form['amount'],
        date=request.form['date'],
        paid_by=request.form['paid_by'],
        category=request.form['category'],
        split=request.form.getlist('split'),
        notes=request.form.get('notes', ''),
    )
    return redirect(url_for('expenses'))


@app.route('/expenses/<int:expense_id>/delete', methods=['POST'])
@login_required
def expenses_delete(expense_id):
    main.delete_expense(expense_id)
    return redirect(url_for('expenses'))


# ── Balances ───────────────────────────────────────────────────────────────────

@app.route('/balances')
@login_required
def balances():
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    member       = request.args.get('member', 'all')
    balance_data = main.get_balances(t['id'], member=member)
    all_s        = main.get_settlements(t['id'])
    settled_amounts = {(s['debtor'], s['creditor']): s['amount']
                       for s in all_s if s['status'] == 'settled'}
    pending_debts = []
    for d in balance_data['debts']:
        remaining = round(d['amount'] - settled_amounts.get((d['debtor'], d['creditor']), 0), 2)
        if remaining > 0.005:
            pending_debts.append({**d, 'amount': remaining})
    return render_template(
        'balances.html',
        balances=balance_data,
        pending_debts=pending_debts,
        trip=t,
        member=member,
    )


# ── Settle Up ──────────────────────────────────────────────────────────────────

@app.route('/settle-up')
@app.route('/settle-up/history')
@login_required
def settle_up():
    t = _active_trip()
    if not t:
        return redirect(url_for('trip'))
    balance_data  = main.get_balances(t['id'])
    all_s         = main.get_settlements(t['id'])
    settled_amounts = {(s['debtor'], s['creditor']): s['amount']
                       for s in all_s if s['status'] == 'settled'}
    pending_debts = []
    for d in balance_data['debts']:
        remaining = round(d['amount'] - settled_amounts.get((d['debtor'], d['creditor']), 0), 2)
        if remaining > 0.005:
            pending_debts.append({**d, 'amount': remaining})
    settled_debts    = [s for s in all_s if s['status'] == 'settled']
    unsettled_total  = round(sum(d['amount'] for d in pending_debts), 2)
    settled_total    = round(sum(s['amount'] for s in settled_debts), 2)
    return render_template(
        'settle-up.html',
        pending_debts=pending_debts,
        settled_debts=settled_debts,
        unsettled_total=unsettled_total,
        settled_total=settled_total,
        trip=t,
    )


@app.route('/settle-up/mark-all', methods=['POST'])
@login_required
def settle_up_mark_all():
    t = _active_trip()
    if t:
        main.mark_all_settled(t['id'])
    return redirect(url_for('settle_up'))



@app.route('/settle-up/settle', methods=['POST'])
@login_required
def settle_up_settle():
    t = _active_trip()
    if t:
        main.settle_debt(
            t['id'],
            debtor=request.form['debtor'],
            creditor=request.form['creditor'],
        )
    return redirect(url_for('settle_up'))


# ── Trip selector ──────────────────────────────────────────────────────────────

@app.route('/trips')
@login_required
def trips_list():
    cu = _current_user()
    user_trips = main.get_user_trips(cu['id'])
    return render_template('trips.html', user_trips=user_trips,
                           active_trip_id=_active_trip_id())


@app.route('/trips/switch/<int:trip_id>', methods=['POST'])
@login_required
def trips_switch(trip_id):
    cu = _current_user()
    # Only allow switching to trips the user actually belongs to
    if any(t['id'] == trip_id for t in main.get_user_trips(cu['id'])):
        session['active_trip_id'] = trip_id
    return redirect(url_for('index'))


# ── Trip ───────────────────────────────────────────────────────────────────────

@app.route('/trip')
@login_required
def trip():
    t  = _active_trip()
    cu = _current_user()
    # If no trip exists yet, create one
    if not t:
        t = main.create_trip(cu['id'], 'My Trip')
        session['active_trip_id'] = t['id']
    is_owner = cu and t.get('owner_id') == cu['id']
    return render_template(
        'trip.html',
        trip=t,
        balances=main.get_balances(t['id']),
        is_owner=is_owner,
    )


@app.route('/trip/save', methods=['POST'])
@login_required
def trip_save():
    t, _ = _require_owner()
    main.save_trip(
        t['id'],
        trip_name=request.form['trip_name'],
        destination=request.form['destination'],
        start_date=request.form['start_date'],
        end_date=request.form['end_date'],
        currency=request.form['currency'],
        cover=request.form['cover'],
        description=request.form['description'],
        budget=request.form['budget'],
        split_method=request.form['split_method'],
    )
    return redirect(url_for('trip'))


@app.route('/trip/new', methods=['POST'])
@login_required
def trip_new():
    cu = _current_user()
    t  = main.create_trip(cu['id'], 'New Trip')
    session['active_trip_id'] = t['id']
    return redirect(url_for('trip'))


@app.route('/trip/add-member', methods=['POST'])
@login_required
def trip_add_member():
    t, _ = _require_owner()
    member = request.form.get('member', '').strip()
    if member:
        main.add_member(t['id'], member)
    return redirect(url_for('trip'))


@app.route('/trip/join-link', methods=['POST'])
@login_required
def trip_join_link():
    """Generate a shareable join link for this trip."""
    t, _ = _require_owner()
    token   = main.make_invite_token(t['id'])
    app_url = os.environ.get('APP_URL', request.host_url.rstrip('/'))
    return {'link': f'{app_url}/join/{token}'}


@app.route('/join/<token>')
def join_trip(token):
    """Handle invite links for both logged-in and new users."""
    payload = main.verify_invite_token(token)
    if not payload:
        return render_template('join_invalid.html'), 400

    trip_id = payload.get('trip_id')
    trip    = main.get_trip(trip_id) if trip_id else None
    if not trip:
        return render_template('join_invalid.html'), 400

    # Already logged in — join immediately
    if 'user_id' in session and _current_user():
        user = _current_user()
        main.add_member(trip_id, user['name'], user_id=user['id'])
        session['active_trip_id'] = trip_id
        return redirect(url_for('index'))

    # Not logged in — send to register (or login) with token
    return redirect(url_for('register', invite=token))


@app.route('/trip/rename-member', methods=['POST'])
@login_required
def trip_rename_member():
    t, _ = _require_owner()
    main.rename_member(t['id'], request.form['old_name'], request.form['new_name'])
    return redirect(url_for('trip'))


@app.route('/trip/remove-member', methods=['POST'])
@login_required
def trip_remove_member():
    t, _ = _require_owner()
    main.remove_member(t['id'], request.form['member'])
    return redirect(url_for('trip'))


@app.route('/trip/reset', methods=['POST'])
@login_required
def trip_reset():
    t, _ = _require_owner()
    main.reset_expenses(t['id'])
    return redirect(url_for('trip'))


@app.route('/trip/delete', methods=['POST'])
@login_required
def trip_delete():
    t, cu = _require_owner()
    main.delete_trip(t['id'])
    session.pop('active_trip_id', None)
    # Redirect to trip page which will create a new one
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
