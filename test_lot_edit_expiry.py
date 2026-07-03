#!/usr/bin/env python3
"""
Inventory lot edit/delete + expiry-date tests.

Covers the v0.7.x work-in-progress: inventory `expiry_date`, the lot Edit/Delete
routes (full edit only for untouched lots, metadata-only otherwise), the USD
import→VND conversion fix, and expiry columns in export/import.

Every case below was smoke-verified in the 2026-07-03 audit; this file makes them
permanent regressions.

Run: python test_lot_edit_expiry.py
"""
import csv
import io
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import db as db_module
_TEST_DB = tempfile.mktemp(suffix='_inv_lotedit_test.db')
db_module.DATABASE = _TEST_DB

import backup as backup_module
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_lotedit')
shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
os.makedirs(_TEST_BACKUPS, exist_ok=True)
backup_module.BACKUPS_DIR = _TEST_BACKUPS

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True

from datetime import datetime, timedelta
from db import init_db, get_db

# ── helpers ───────────────────────────────────────────────────────────────────
_PASS = _FAIL = 0
_FAILURES = []

def ok(name):
    global _PASS; _PASS += 1
    print(f'  \033[92m✓\033[0m {name}')

def fail(name, detail=''):
    global _FAIL; _FAIL += 1
    _FAILURES.append((name, detail))
    print(f'  \033[91m✗\033[0m {name}')
    if detail:
        print(f'    \033[93m{str(detail)[:400]}\033[0m')

def section(title):
    print(f'\n\033[1m── {title} ──\033[0m')

def check(cond, name, detail=''):
    if cond: ok(name)
    else: fail(name, detail)
    return cond

def has(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text in body, name, f'Expected {text!r} not found')

def missing(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text not in body, name, f'Unexpected {text!r} present')

def status(resp, code, name):
    return check(resp.status_code == code, name, f'HTTP {resp.status_code} != {code}')

CSRF = 'test-csrf-lotedit'

def post(c, url, data=None):
    d = dict(data or {})
    d['csrf_token'] = CSRF
    return c.post(url, data=d, follow_redirects=True)

def post_file(c, url, field, content, filename, extra=None):
    d = dict(extra or {})
    d['csrf_token'] = CSRF
    d[field] = (io.BytesIO(content), filename)
    return c.post(url, data=d, content_type='multipart/form-data', follow_redirects=True)

def db_query(sql, params=()):
    db = get_db()
    try:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        db.close()

def db_count(sql, params=()):
    db = get_db()
    try:
        return db.execute(sql, params).fetchone()[0]
    finally:
        db.close()

def db_exec(sql, params=()):
    db = get_db()
    try:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid
    finally:
        db.close()

def parse_csv_response(resp):
    text = resp.data.decode('utf-8', errors='ignore')
    return list(csv.DictReader(io.StringIO(text)))


CAT_ID = None

def make_product(code, name, cost=100000):
    return db_exec(
        'INSERT INTO products (product_code, name, category_id, sale_price, cost_price, min_stock_level) '
        'VALUES (?, ?, ?, ?, ?, 0)',
        (code, name, CAT_ID, cost * 2, cost),
    )

def make_lot(product_id, quantity=10, cost=100000, shipping=0.0, currency='VND',
             exchange_rate=1.0, intake_date='2026-06-01T10:00:00', expiry=None, notes=''):
    return db_exec(
        'INSERT INTO inventory (product_id, quantity, remaining_quantity, cost_price, shipping_cost, '
        'currency, exchange_rate, intake_date, expiry_date, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (product_id, quantity, quantity, cost, shipping, currency, exchange_rate, intake_date, expiry, notes),
    )

def lot(lot_id):
    return db_query('SELECT * FROM inventory WHERE id = ?', (lot_id,))


def run():
    global _PASS, _FAIL, _FAILURES, CAT_ID
    _PASS = _FAIL = 0
    _FAILURES = []

    init_db()

    CAT_ID = db_exec("INSERT INTO categories (name) VALUES ('LotTest')")
    CUST_ID = db_exec("INSERT INTO customers (customer_code, name) VALUES ('KH000001', 'Lot Cust')")

    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['role'] = 'admin'
            sess['csrf_token'] = CSRF

        # ── 1 & 2: create intake with expiry (valid / garbage) ────────────────
        section('Create intake — expiry_date stored / validated')

        p_create = make_product('LT0001', 'Create Test')

        r = post(c, '/inventory/', {
            'product_id': p_create,
            'quantity': '12',
            'cost_price': '50000',
            'shipping_cost': '0',
            'currency': 'VND',
            'intake_date': '2026-06-10',
            'expiry_date': '2026-09-15',
            'notes': 'good-expiry',
        })
        has(r, 'Inventory intake recorded', 'Create intake with valid expiry succeeds')
        row = db_query("SELECT expiry_date FROM inventory WHERE notes='good-expiry'")
        check(row is not None and row['expiry_date'] == '2026-09-15',
              f'Valid expiry stored as 2026-09-15 (got {row})')

        r = post(c, '/inventory/', {
            'product_id': p_create,
            'quantity': '4',
            'cost_price': '50000',
            'currency': 'VND',
            'expiry_date': 'garbage',
            'notes': 'garbage-expiry',
        })
        has(r, 'Inventory intake recorded', 'Create intake with garbage expiry still succeeds')
        row = db_query("SELECT expiry_date FROM inventory WHERE notes='garbage-expiry'")
        check(row is not None and row['expiry_date'] is None,
              f'Garbage expiry stored as NULL (got {row})')

        # ── 3: edit form renders ──────────────────────────────────────────────
        section('Edit form renders')

        edit_lot = make_lot(p_create, quantity=6, intake_date='2026-06-15T08:00:00')
        r = c.get(f'/inventory/{edit_lot}/edit')
        status(r, 200, 'GET edit form returns 200')
        has(r, 'Edit Inventory Lot', 'Edit form contains "Edit Inventory Lot"')

        # ── 4 & 5: intake_date same-day preservation vs. day change ──────────
        section('intake_date — same day preserved, new day applied')

        p_date = make_product('LT0002', 'Date Test')

        same_day_lot = make_lot(p_date, quantity=10, intake_date='2026-06-01T14:32:11')
        r = post(c, f'/inventory/{same_day_lot}', {
            'quantity': '10', 'cost_price': '100000', 'shipping_cost': '0',
            'intake_date': '2026-06-01', 'notes': 'same-day',
        })
        got = lot(same_day_lot)['intake_date']
        check(got == '2026-06-01T14:32:11',
              f'Unchanged day keeps full timestamp (got {got!r})')

        diff_day_lot = make_lot(p_date, quantity=10, intake_date='2026-06-01T14:32:11')
        r = post(c, f'/inventory/{diff_day_lot}', {
            'quantity': '10', 'cost_price': '100000', 'shipping_cost': '0',
            'intake_date': '2026-06-20', 'notes': 'diff-day',
        })
        got = lot(diff_day_lot)['intake_date']
        check(got.startswith('2026-06-20'),
              f'Changed day applies new date (got {got!r})')

        # ── 6: full edit of USD lot normalizes to VND ────────────────────────
        section('Full edit of a USD lot normalizes currency/rate')

        p_usd = make_product('LT0003', 'USD Test')
        usd_lot = make_lot(p_usd, quantity=8, cost=360000, currency='USD',
                           exchange_rate=24000.0, intake_date='2026-06-05T09:00:00')
        r = post(c, f'/inventory/{usd_lot}', {
            'quantity': '8', 'cost_price': '375000', 'shipping_cost': '10000',
            'intake_date': '2026-06-05', 'notes': 'usd-edited',
        })
        row = lot(usd_lot)
        check(row['currency'] == 'VND' and row['exchange_rate'] == 1.0,
              f"USD lot normalized to VND/1.0 (got {row['currency']}/{row['exchange_rate']})")
        check(row['cost_price'] == 375000, f"Cost updated to 375000 (got {row['cost_price']})")

        # ── 7: used lot — metadata-only edit ─────────────────────────────────
        section('Used lot (has allocations) — metadata-only edit')

        p_used = make_product('LT0004', 'Used Test', cost=100000)
        used_lot = make_lot(p_used, quantity=10, cost=100000, intake_date='2026-05-01T09:00:00')

        r = post(c, '/orders/', {
            'customer_id': CUST_ID,
            'order_date': '2026-05-10',
            'items[0][product_id]': p_used,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '200000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        order_id = db_query("SELECT id FROM orders ORDER BY id DESC LIMIT 1")['id']
        post(c, f'/orders/{order_id}/status', {'status': 'completed'})
        check(lot(used_lot)['remaining_quantity'] == 8,
              'FIFO consumed 2 units from the lot (remaining=8)')
        check(db_count('SELECT COUNT(*) FROM order_allocations WHERE inventory_lot_id=?', (used_lot,)) == 1,
              'Allocation row written for the used lot')

        r = post(c, f'/inventory/{used_lot}', {
            'quantity': '999', 'cost_price': '1', 'shipping_cost': '1',
            'intake_date': '2026-05-01', 'expiry_date': '2026-12-31', 'notes': 'meta-only',
        })
        row = lot(used_lot)
        check(row['quantity'] == 10, f"Used lot quantity UNCHANGED (got {row['quantity']})")
        check(row['cost_price'] == 100000, f"Used lot cost UNCHANGED (got {row['cost_price']})")
        check(row['remaining_quantity'] == 8, f"Used lot remaining UNCHANGED (got {row['remaining_quantity']})")
        check(row['expiry_date'] == '2026-12-31' and row['notes'] == 'meta-only',
              'Expiry and notes updated on metadata path')

        # ── 8: used lot — delete refused ─────────────────────────────────────
        section('Used lot — delete refused')

        r = post(c, f'/inventory/{used_lot}/delete', {})
        has(r, 'write-off', 'Delete refusal flash suggests write-off alternative')
        check(lot(used_lot) is not None, 'Used lot still exists after refused delete')

        # ── 9: untouched lot — delete succeeds ───────────────────────────────
        section('Untouched lot — delete succeeds')

        p_del = make_product('LT0005', 'Delete Test')
        del_lot = make_lot(p_del, quantity=5)
        r = post(c, f'/inventory/{del_lot}/delete', {})
        has(r, 'Lot deleted', 'Untouched lot delete succeeds')
        check(lot(del_lot) is None, 'Deleted lot is gone from DB')

        # ── 10: written-off lot (remaining != quantity) — edit/delete refused ─
        section('Written-off lot — full edit refused, delete refused')

        p_wo = make_product('LT0006', 'WriteOff Test')
        wo_lot = make_lot(p_wo, quantity=5, cost=100000)
        r = post(c, f'/inventory/{wo_lot}/write-off', {'quantity': '2', 'reason': 'damaged_unsellable'})
        check(lot(wo_lot)['remaining_quantity'] == 3, 'Write-off left remaining=3 (!= quantity=5)')

        r = post(c, f'/inventory/{wo_lot}', {
            'quantity': '99', 'cost_price': '1', 'shipping_cost': '1',
            'intake_date': '2026-06-01', 'notes': 'wo-meta',
        })
        row = lot(wo_lot)
        check(row['quantity'] == 5 and row['cost_price'] == 100000,
              f"Written-off lot quantity/cost UNCHANGED (got {row['quantity']}/{row['cost_price']})")
        check(row['notes'] == 'wo-meta', 'Written-off lot notes updated (metadata path)')

        r = post(c, f'/inventory/{wo_lot}/delete', {})
        check(lot(wo_lot) is not None, 'Written-off lot still exists after refused delete')

        # ── 11: import USD → VND conversion (regression) ─────────────────────
        section('Import USD row converts cost/shipping to VND (regression)')

        rate = db_query('SELECT vnd_usd_rate FROM settings LIMIT 1')['vnd_usd_rate']
        make_product('LT0011', 'Import USD')
        usd_csv = (
            'product_code,quantity,cost_price,shipping_cost,currency\n'
            'LT0011,5,15,2,USD\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', usd_csv, 'usd.csv')
        has(r, 'Successfully imported', 'USD import row succeeds')
        row = db_query("SELECT i.cost_price, i.shipping_cost, i.exchange_rate FROM inventory i "
                       "JOIN products p ON i.product_id=p.id WHERE p.product_code='LT0011' "
                       "ORDER BY i.id DESC LIMIT 1")
        check(abs(row['cost_price'] - 15 * rate) < 1.0,
              f'cost_price == 15 * rate ({15*rate}, got {row["cost_price"]})')
        check(abs(row['shipping_cost'] - 2 * rate) < 1.0,
              f'shipping_cost == 2 * rate ({2*rate}, got {row["shipping_cost"]})')
        check(abs(row['exchange_rate'] - rate) < 1e-6,
              f'exchange_rate == rate ({rate}, got {row["exchange_rate"]})')

        # ── 12: import expiry_date (valid stored / invalid row rejected) ─────
        section('Import expiry_date — valid stored, invalid row rejected')

        make_product('LT0012', 'Import Expiry')
        exp_csv = (
            'product_code,quantity,cost_price,currency,expiry_date,notes\n'
            'LT0012,3,50000,VND,2027-01-31,imp-good\n'
            'LT0012,4,60000,VND,notadate,imp-bad\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', exp_csv, 'exp.csv')
        has(r, 'Successfully imported 1', 'Only the valid-expiry row imports')
        good = db_query("SELECT expiry_date FROM inventory WHERE notes='imp-good'")
        check(good is not None and good['expiry_date'] == '2027-01-31',
              f'Valid import expiry stored (got {good})')
        check(db_count("SELECT COUNT(*) FROM inventory WHERE notes='imp-bad'") == 0,
              'Row with invalid expiry_date was not inserted')

        # ── 13: import invalid currency rejected ─────────────────────────────
        section('Import invalid currency rejected')

        make_product('LT0013', 'Import EUR')
        eur_csv = (
            'product_code,quantity,cost_price,currency,notes\n'
            'LT0013,5,100,EUR,eur-row\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', eur_csv, 'eur.csv')
        has(r, 'Invalid currency', 'EUR row reports invalid currency')
        check(db_count("SELECT COUNT(*) FROM inventory WHERE notes='eur-row'") == 0,
              'EUR row was not inserted')

        # ── 14: export includes Expiry Date column ───────────────────────────
        section('Export inventory CSV carries Expiry Date')

        p_exp = make_product('LT0014', 'Export Expiry')
        make_lot(p_exp, quantity=7, expiry='2028-02-29', notes='export-expiry')
        r = c.get('/exports/inventory.csv')
        status(r, 200, 'Inventory CSV returns 200')
        rows = parse_csv_response(r)
        check(rows and 'Expiry Date' in rows[0], 'Inventory CSV header includes "Expiry Date"')
        exp_row = next((x for x in rows if x.get('Product Code') == 'LT0014'), None)
        check(exp_row is not None and exp_row['Expiry Date'] == '2028-02-29',
              f'Expiry Date round-trips in export (got {exp_row})')

        # ── 15: limited role blocked from lot edit/delete ────────────────────
        section('Limited role blocked from lot edit/delete')

        p_lim = make_product('LT0015', 'Limited Test')
        lim_lot = make_lot(p_lim, quantity=7, notes='limited-orig')

        with c.session_transaction() as sess:
            sess['role'] = 'limited'

        r = post(c, f'/inventory/{lim_lot}', {
            'quantity': '99', 'cost_price': '1', 'notes': 'hacked',
        })
        check(lot(lim_lot)['notes'] == 'limited-orig' and lot(lim_lot)['quantity'] == 7,
              'Limited POST update did not change the lot')

        r = post(c, f'/inventory/{lim_lot}/delete', {})
        check(lot(lim_lot) is not None, 'Limited POST delete did not remove the lot')

        r = c.get(f'/inventory/{lim_lot}/edit', follow_redirects=True)
        missing(r, 'Edit Inventory Lot', 'Limited GET edit does not render the edit form')

        with c.session_transaction() as sess:
            sess['role'] = 'admin'

        # ── 16: expiry warning rendering on the inventory page ───────────────
        section('Expiry warnings render on the inventory list')

        today = datetime.now()
        yesterday = (today - timedelta(days=1)).strftime('%Y-%m-%d')
        in15 = (today + timedelta(days=15)).strftime('%Y-%m-%d')
        far = (today + timedelta(days=400)).strftime('%Y-%m-%d')

        p_warn = make_product('LT0016', 'Warn Test')
        make_lot(p_warn, quantity=3, expiry=yesterday, notes='w-expired')
        make_lot(p_warn, quantity=3, expiry=in15, notes='w-soon')
        make_lot(p_warn, quantity=3, expiry=far, notes='w-far')

        r = c.get('/inventory/')
        status(r, 200, 'Inventory page returns 200')
        has(r, 'title="Expired"', 'Expired lot shows the "Expired" marker')
        has(r, 'Expires within 30 days', 'Near-expiry lot shows the 30-day warning')
        missing(r, far + ' ⚠', 'Far-future expiry shows no warning marker')

    return _PASS, _FAIL, _FAILURES


if __name__ == '__main__':
    passed, failed, failures = run()
    shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)

    print(f'\n{"─"*50}')
    if failures:
        print('\033[91mFAILED tests:\033[0m')
        for name, detail in failures:
            print(f'  ✗ {name}')
            if detail:
                print(f'    {detail}')
    total = passed + failed
    color = '\033[92m' if failed == 0 else '\033[91m'
    print(f'{color}{passed}/{total} lot-edit/expiry tests passed.\033[0m')
    sys.exit(0 if failed == 0 else 1)
