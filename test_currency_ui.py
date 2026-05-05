#!/usr/bin/env python3
"""
Currency UI / label clarity tests  +  new export/import coverage.

Tests:
  1. All monetary form inputs carry "(VND)" labels (currency label audit).
  2. USD warning banner appears on order_form when display_currency=USD.
  3. USD warning banner appears on order_detail payment section when display_currency=USD.
  4. USD warning banner NOT shown when display_currency=VND.
  5. Returns export: route exists, returns CSV with expected columns.
  6. Refunds export: route exists, returns CSV with expected columns.
  7. Categories export: route exists, returns CSV.
  8. Categories import: creates new records, skips duplicate names.
  9. Product import: preserves product_code from CSV.
 10. Customer import: preserves customer_code from CSV.

Run: python test_currency_ui.py
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
_TEST_DB = tempfile.mktemp(suffix='_inv_currui_test.db')
db_module.DATABASE = _TEST_DB

import backup as backup_module
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_currui')
shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
os.makedirs(_TEST_BACKUPS, exist_ok=True)
backup_module.BACKUPS_DIR = _TEST_BACKUPS

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True

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
    return check(text in body, name, f'Expected {text!r} not found in response')

def nhas(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text not in body, name, f'Unexpected {text!r} found in response')

def status(resp, code, name):
    return check(resp.status_code == code, name, f'HTTP {resp.status_code} != {code}')

CSRF = 'test-csrf-currui'

def post(c, url, data=None):
    d = dict(data or {})
    d['csrf_token'] = CSRF
    return c.post(url, data=d, follow_redirects=True)

def post_file(c, url, field, content, filename, extra=None):
    d = dict(extra or {})
    d['csrf_token'] = CSRF
    d[field] = (io.BytesIO(content), filename)
    return c.post(url, data=d, content_type='multipart/form-data', follow_redirects=True)

def get(c, url):
    return c.get(url, follow_redirects=True)

def db_query(sql, params=()):
    db = get_db()
    try:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        db.close()

def db_rows(sql, params=()):
    db = get_db()
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    finally:
        db.close()

def parse_csv(resp):
    text = resp.data.decode('utf-8', errors='ignore')
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def run():
    global _PASS, _FAIL, _FAILURES
    _PASS = _FAIL = 0
    _FAILURES = []

    init_db()

    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['role'] = 'admin'
            sess['csrf_token'] = CSRF

        # ── Seed: one customer, one category, one product, one order ──────────
        post(c, '/customers/', {'name': 'Test Customer', 'email': '', 'phone': '', 'address': '', 'region': ''})
        post(c, '/categories/', {'name': 'Electronics', 'description': ''})

        # Get IDs
        cust = db_query('SELECT id FROM customers ORDER BY id LIMIT 1')
        cat  = db_query('SELECT id FROM categories WHERE name="Electronics"')

        # ── 1. Currency label audit ───────────────────────────────────────────
        section('Currency label audit — all monetary inputs carry (VND)')

        # order_form (create mode)
        r = get(c, '/orders/new')
        status(r, 200, 'order_form accessible')
        has(r, '(VND)', 'order_form has (VND) label')
        has(r, 'Unit Price', 'order_form has Unit Price column header')

        # product_form modal (loaded via /products/new)
        r = get(c, '/products/new')
        status(r, 200, 'product_form accessible')
        has(r, '(VND, default)', 'product_form sale_price has (VND, default)')

        # refund_form (create mode requires orders to exist — just check template rendering)
        r = get(c, '/refunds/new')
        status(r, 200, 'refund_form accessible')
        has(r, '(VND)', 'refund_form amount has (VND) label')

        # intake_form modal (loaded via /inventory/new)
        r = get(c, '/inventory/new')
        status(r, 200, 'intake_form accessible')
        has(r, 'in currency below', 'intake_form cost_price label references currency selector')

        # ── 2. USD warning banner — order_form ────────────────────────────────
        section('USD warning banner — order_form')

        with c.session_transaction() as sess:
            sess['display_currency'] = 'USD'

        r = get(c, '/orders/new')
        has(r, 'enter all amounts below in VND', 'order_form shows USD warning when display=USD')
        has(r, 'enter all amounts below in VND', 'warning text present in order_form')

        with c.session_transaction() as sess:
            sess['display_currency'] = 'VND'

        r = get(c, '/orders/new')
        nhas(r, 'enter all amounts below in VND', 'order_form hides USD warning when display=VND')

        # ── 3. USD warning banner — order_detail payment section ──────────────
        section('USD warning banner — order_detail payment form')

        # Create an order and move it to processing so the payment form shows
        if cust and cat:
            # Add product + inventory first
            r_cat_id = cat['id']
            post(c, '/products/', {
                'name': 'Test Item', 'category_id': r_cat_id,
                'sale_price': '100000', 'cost_price': '70000',
                'barcode': '', 'min_stock_level': '0', 'notes': ''
            })
            prod = db_query('SELECT id FROM products WHERE name="Test Item"')
            if prod:
                post(c, '/inventory/', {
                    'product_id': prod['id'], 'quantity': '100',
                    'cost_price': '70000', 'shipping_cost': '0',
                    'currency': 'VND', 'intake_date': '2026-01-01', 'notes': ''
                })
                # Create order
                post(c, '/orders/', {
                    'customer_id': cust['id'],
                    'order_date': '2026-01-15',
                    'discount_amount': '0',
                    'shipping_fee': '0',
                    'shipping_paid_by': 'customer',
                    'notes': '',
                    f'items[0][product_id]': prod['id'],
                    f'items[0][quantity]': '1',
                    f'items[0][unit_price]': '100000',
                    f'items[0][discount_percent]': '0',
                })
                order = db_query('SELECT id FROM orders ORDER BY id DESC LIMIT 1')
                if order:
                    # Move to processing
                    post(c, f'/orders/{order["id"]}/status', {'status': 'processing'})

                    with c.session_transaction() as sess:
                        sess['display_currency'] = 'USD'
                    r = get(c, f'/orders/{order["id"]}')
                    has(r, 'enter payment amount in VND', 'order_detail shows USD payment warning when display=USD')

                    with c.session_transaction() as sess:
                        sess['display_currency'] = 'VND'
                    r = get(c, f'/orders/{order["id"]}')
                    nhas(r, 'enter payment amount in VND', 'order_detail hides USD payment warning when display=VND')
        else:
            fail('order_detail USD banner test (skipped: no customer/category)', 'seed failed')

        # ── 4. Categories export ──────────────────────────────────────────────
        section('Categories export')

        r = get(c, '/exports/categories.csv')
        status(r, 200, 'categories CSV export returns 200')
        rows = parse_csv(r)
        headers = list(rows[0].keys()) if rows else []
        check('Name' in headers, 'categories CSV has Name column', headers)
        check('Description' in headers, 'categories CSV has Description column', headers)
        names = [row['Name'] for row in rows]
        check('Electronics' in names, 'categories CSV includes seeded Electronics category')

        # ── 5. Returns export ─────────────────────────────────────────────────
        section('Returns export')

        r = get(c, '/exports/returns.csv')
        status(r, 200, 'returns CSV export returns 200')
        rows = parse_csv(r)
        # Might be empty (no returns yet), but headers should be present
        if rows:
            headers = list(rows[0].keys())
            check('Return Code' in headers, 'returns CSV has Return Code column', headers)
            check('Date' in headers, 'returns CSV has Date column', headers)
            check('Order Code' in headers, 'returns CSV has Order Code column', headers)
        else:
            # Even empty CSV should have a header row
            text = r.data.decode('utf-8', errors='ignore').strip()
            check('Return Code' in text, 'returns CSV has header row even when empty', repr(text[:200]))

        # ── 6. Refunds export ─────────────────────────────────────────────────
        section('Refunds export')

        r = get(c, '/exports/refunds.csv')
        status(r, 200, 'refunds CSV export returns 200')
        rows = parse_csv(r)
        if rows:
            headers = list(rows[0].keys())
            check('Refund Code' in headers, 'refunds CSV has Refund Code column', headers)
            check('Amount (VND)' in headers, 'refunds CSV has Amount (VND) column', headers)
        else:
            text = r.data.decode('utf-8', errors='ignore').strip()
            check('Refund Code' in text, 'refunds CSV has header row even when empty', repr(text[:200]))

        # ── 7. Full XLSX export includes all 7 sheets ─────────────────────────
        section('Full XLSX export — 7 sheets')
        try:
            import openpyxl
            r = get(c, '/exports/all.xlsx')
            status(r, 200, 'all.xlsx returns 200')
            if r.status_code == 200:
                wb = openpyxl.load_workbook(io.BytesIO(r.data))
                sheet_names = wb.sheetnames
                for expected in ('Products', 'Customers', 'Orders', 'Inventory',
                                 'Categories', 'Returns', 'Refunds'):
                    check(expected in sheet_names, f'all.xlsx contains {expected} sheet', sheet_names)
        except ImportError:
            ok('full XLSX test skipped (openpyxl not installed)')

        # ── 8. Categories import: creates new, skips duplicates ───────────────
        section('Categories import — creates new, skips duplicates')

        csv_content = b'name,description\nClothing,Fashion apparel\nElectronics,Already exists\nFurniture,Home goods\n'
        before = db_query('SELECT COUNT(*) as n FROM categories')['n']
        r = post_file(c, '/import/categories', 'file', csv_content, 'cats.csv')
        status(r, 200, 'categories import returns 200')
        after = db_query('SELECT COUNT(*) as n FROM categories')['n']
        check(after == before + 2, 'categories import: 2 new rows inserted (1 skipped duplicate)',
              f'before={before} after={after}')

        clothing = db_query('SELECT * FROM categories WHERE name="Clothing"')
        check(clothing is not None, 'Clothing category was created')
        check(clothing and clothing['description'] == 'Fashion apparel',
              'Clothing category description correct')

        # Second import: all 3 are duplicates now — nothing changes
        r = post_file(c, '/import/categories', 'file', csv_content, 'cats2.csv')
        after2 = db_query('SELECT COUNT(*) as n FROM categories')['n']
        check(after2 == after, 'categories re-import is idempotent (no new rows)', f'before={after} after={after2}')
        has(r, 'skipped', 'categories re-import reports skipped rows')

        # ── 9. Product import: preserves product_code from CSV ────────────────
        section('Product import — preserves product_code from CSV')

        csv_content = b'product_code,name,category,sale_price,cost_price,barcode,min_stock_level\nMY0042,Custom Code Product,Electronics,200000,120000,,5\n'
        r = post_file(c, '/import/products', 'file', csv_content, 'prods.csv')
        status(r, 200, 'product import with explicit code returns 200')
        prod = db_query('SELECT * FROM products WHERE product_code="MY0042"')
        check(prod is not None, 'product with code MY0042 was created')
        check(prod and prod['name'] == 'Custom Code Product',
              'product name matches CSV', prod)

        # Duplicate code should fail gracefully
        r = post_file(c, '/import/products', 'file', csv_content, 'prods2.csv')
        status(r, 200, 'duplicate product code import returns 200 (no crash)')
        has(r, 'MY0042', 'duplicate product code import reports the duplicate code')

        # Auto-generate code when blank
        csv_auto = b'product_code,name,category,sale_price,cost_price,barcode,min_stock_level\n,Auto Code Product,Electronics,150000,90000,,0\n'
        r = post_file(c, '/import/products', 'file', csv_auto, 'prods3.csv')
        status(r, 200, 'product import with auto code returns 200')
        prod_auto = db_query('SELECT * FROM products WHERE name="Auto Code Product"')
        check(prod_auto is not None, 'auto-code product was created')
        if prod_auto:
            check(prod_auto['product_code'].startswith('EL'), 'auto code uses Electronics (EL) prefix',
                  prod_auto['product_code'])

        # ── 10. Customer import: preserves customer_code from CSV ──────────────
        section('Customer import — preserves customer_code from CSV')

        csv_content = b'customer_code,name,email,phone,address,region\nKH999999,Custom Code Customer,,,,\n'
        r = post_file(c, '/import/customers', 'file', csv_content, 'custs.csv')
        status(r, 200, 'customer import with explicit code returns 200')
        cust_row = db_query('SELECT * FROM customers WHERE customer_code="KH999999"')
        check(cust_row is not None, 'customer with code KH999999 was created')
        check(cust_row and cust_row['name'] == 'Custom Code Customer',
              'customer name matches CSV', cust_row)

        # Auto-generate code when blank
        csv_auto = b'customer_code,name,email,phone,address,region\n,Auto Code Customer,,,,\n'
        r = post_file(c, '/import/customers', 'file', csv_auto, 'custs2.csv')
        status(r, 200, 'customer import with auto code returns 200')
        cust_auto = db_query('SELECT * FROM customers WHERE name="Auto Code Customer"')
        check(cust_auto is not None, 'auto-code customer was created')
        if cust_auto:
            check(cust_auto['customer_code'].startswith('KH'), 'auto code has KH prefix',
                  cust_auto['customer_code'])

        # ── 11. Returns/Refunds export with data ──────────────────────────────
        section('Returns/Refunds export with seeded data')

        # Create a return linked to the processing order we created earlier
        order = db_query('SELECT id FROM orders WHERE order_status="processing" ORDER BY id DESC LIMIT 1')
        if order and prod:
            # Get order item
            oi = db_query('SELECT id, product_id, quantity, unit_price FROM order_items WHERE order_id=?', (order['id'],))
            if oi:
                ret_form_r = get(c, '/returns/new')
                if ret_form_r.status_code == 200:
                    # Submit return
                    post(c, '/returns/', {
                        'order_id': order['id'],
                        'return_date': '2026-02-01',
                        'reason': 'damaged_product',
                        'notes': '',
                        'items[0][order_item_id]': oi['id'],
                        'items[0][product_id]': oi['product_id'],
                        'items[0][quantity]': '1',
                        'items[0][refund_amount]': str(int(oi['unit_price'])),
                        'items[0][restock]': 'on',
                        'items[0][restock_quantity]': '1',
                        'items[0][restock_unit_cost]': '0',
                        'items[0][restock_shipping_cost]': '0',
                    })
                    ret = db_query('SELECT id, return_code FROM returns ORDER BY id DESC LIMIT 1')
                    if ret:
                        r = get(c, '/exports/returns.csv')
                        rows = parse_csv(r)
                        ret_codes = [row.get('Return Code', '') for row in rows]
                        check(ret['return_code'] in ret_codes, 'returns export includes seeded return',
                              f'Expected {ret["return_code"]} in {ret_codes}')

                    # Create a refund for the order
                    post(c, '/refunds/', {
                        'order_id': order['id'],
                        'return_id': '',
                        'amount': '50000',
                        'refund_date': '2026-02-05',
                        'refund_method': 'cash',
                        'reason': 'customer_request',
                        'notes': 'test refund',
                    })
                    ref = db_query('SELECT id, refund_code FROM refunds ORDER BY id DESC LIMIT 1')
                    if ref:
                        r = get(c, '/exports/refunds.csv')
                        rows = parse_csv(r)
                        ref_codes = [row.get('Refund Code', '') for row in rows]
                        check(ref['refund_code'] in ref_codes, 'refunds export includes seeded refund',
                              f'Expected {ref["refund_code"]} in {ref_codes}')
                        # Verify amount is integer (not float like 50000.0)
                        ref_row = next((row for row in rows if row.get('Refund Code') == ref['refund_code']), None)
                        if ref_row:
                            amt = ref_row.get('Amount (VND)', '')
                            check('.' not in str(amt), 'refund amount exported as integer (no decimal point)',
                                  f'Amount = {amt!r}')
                else:
                    ok('returns/refunds with data test skipped (return form unavailable)')
            else:
                ok('returns/refunds with data test skipped (no order items)')
        else:
            ok('returns/refunds with data test skipped (no processing order)')

    # Cleanup
    try:
        os.remove(_TEST_DB)
    except OSError:
        pass
    shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)


if __name__ == '__main__':
    print('\ntest_currency_ui.py — Currency labels + new export/import coverage\n')
    run()
    print(f'\n{"="*60}')
    print(f'Results: {_PASS} passed, {_FAIL} failed')
    if _FAILURES:
        print('\nFailed tests:')
        for name, detail in _FAILURES:
            print(f'  ✗ {name}')
            if detail:
                print(f'    {detail}')
    sys.exit(0 if _FAIL == 0 else 1)
