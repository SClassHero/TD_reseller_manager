#!/usr/bin/env python3
"""
Edge case tests — batch 2.

Covers the remaining gaps from the original 14-item list not addressed in
test_edge_cases.py:
  10. Partial return on a multi-item order
  11. Overpayment (payments > order total)
  12. Recovery code flow (generate → use → rotate)
  13. FIFO with 3+ lots
  14. is_active = False effects on order/edit forms

Run:
    python test_edge_cases_2.py
"""
import os
import sys
import traceback

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import db as db_module
_TEST_DB = os.path.join(APP_DIR, f'_edge2_test_{os.getpid()}.db')
db_module.DATABASE = _TEST_DB

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True

from db import get_db, init_db

CSRF_TOKEN = 'edge2-csrf-token'
_PASS = 0
_FAIL = 0
_FAILURES = []


# ── Helpers ────────────────────────────────────────────────────────────────

def section(title):
    print(f'\n── {title} ──')

def ok(name):
    global _PASS
    _PASS += 1
    print(f'  OK   {name}')

def fail(name, detail=''):
    global _FAIL
    _FAIL += 1
    _FAILURES.append((name, detail))
    print(f'  FAIL {name}')
    if detail:
        print(f'       {str(detail)[:300]}')

def check(cond, name, detail=''):
    if cond: ok(name)
    else:    fail(name, detail)
    return cond

def eq(actual, expected, name):
    return check(actual == expected, name, f'expected {expected!r}, got {actual!r}')

def money_eq(actual, expected, name):
    a = round(float(actual or 0), 2)
    e = round(float(expected or 0), 2)
    return check(abs(a - e) < 0.01, name, f'expected {e:,.2f}, got {a:,.2f}')

def has(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text in body, name, f'{text!r} not found in response')

def hasnt(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text not in body, name, f'unexpected {text!r} found')

def http(resp, code, name):
    return check(resp.status_code == code, name, f'HTTP {resp.status_code} != {code}')

def post(c, url, data=None):
    d = dict(data or {})
    d['csrf_token'] = CSRF_TOKEN
    return c.post(url, data=d, follow_redirects=True)

def db_one(sql, params=()):
    db = get_db()
    try:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        db.close()

def db_val(sql, params=()):
    db = get_db()
    try:
        return db.execute(sql, params).fetchone()[0]
    finally:
        db.close()

def exec_db(sql, params=()):
    db = get_db()
    try:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


# ── Fixture helpers ────────────────────────────────────────────────────────

def mk_category(c, name):
    post(c, '/categories/', {'name': name, 'description': ''})
    return db_one('SELECT id FROM categories WHERE name = ?', (name,))['id']

def mk_product(c, code, name, cat_id, sale=200000, cost=100000):
    post(c, '/products/', {
        'product_code': code, 'name': name,
        'category_id': str(cat_id),
        'sale_price': str(sale), 'cost_price': str(cost),
        'barcode': code, 'min_stock_level': '0',
    })
    return db_one('SELECT id FROM products WHERE product_code = ?', (code,))['id']

def mk_customer(c, code, name):
    post(c, '/customers/', {
        'customer_code': code, 'name': name,
        'email': '', 'phone': '0900000000',
        'address': '1 Test St', 'region': 'HCM',
    })
    return db_one('SELECT id FROM customers WHERE customer_code = ?', (code,))['id']

def mk_intake(c, product_id, qty, cost, shipping=0, date='2026-01-01'):
    post(c, '/inventory/', {
        'product_id': str(product_id), 'quantity': str(qty),
        'cost_price': str(cost), 'shipping_cost': str(shipping),
        'currency': 'VND', 'intake_date': date, 'notes': '',
    })
    return db_one('SELECT id FROM inventory ORDER BY id DESC LIMIT 1')['id']

def mk_order(c, customer_id, items, discount=0, shipping=0, paid_by='customer'):
    data = {
        'customer_id': str(customer_id),
        'order_date': '2026-03-01',
        'discount_amount': str(discount),
        'shipping_fee': str(shipping),
        'shipping_paid_by': paid_by,
        'notes': '',
    }
    for i, it in enumerate(items):
        data[f'items[{i}][product_id]'] = str(it['product_id'])
        data[f'items[{i}][quantity]'] = str(it['quantity'])
        data[f'items[{i}][unit_price]'] = str(it['unit_price'])
        data[f'items[{i}][discount_percent]'] = str(it.get('discount_percent', 0))
    post(c, '/orders/', data)
    return db_one('SELECT id FROM orders ORDER BY id DESC LIMIT 1')['id']

def complete(c, order_id):
    return post(c, f'/orders/{order_id}/status', {'status': 'completed'})

def add_payment(c, order_id, amount):
    return post(c, f'/orders/{order_id}/payment', {
        'amount': str(amount), 'payment_method': 'cash', 'notes': '',
    })


# ── Test runner ────────────────────────────────────────────────────────────

def run():
    global _PASS, _FAIL, _FAILURES
    _PASS = _FAIL = 0
    _FAILURES = []

    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)
    init_db()
    exec_db('UPDATE settings SET auto_backup_enabled = 0 WHERE id = 1')

    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['role'] = 'admin'
            sess['csrf_token'] = CSRF_TOKEN

        cat = mk_category(c, 'Test')
        cust = mk_customer(c, 'KH-F001', 'Multi-Item Customer')

        # ── 10. Partial Return on a Multi-Item Order ───────────────────────
        section('10. Partial Return on a Multi-Item Order')

        prod_p = mk_product(c, 'MI0001', 'Multi-Item P', cat, sale=300000, cost=100000)
        prod_q = mk_product(c, 'MI0002', 'Multi-Item Q', cat, sale=500000, cost=200000)
        mk_intake(c, prod_p, 5, 100000, 0, '2026-01-01')
        mk_intake(c, prod_q, 5, 200000, 0, '2026-01-01')

        # Order: 3 of P + 2 of Q
        multi_order_id = mk_order(c, cust, [
            {'product_id': prod_p, 'quantity': 3, 'unit_price': 300000},
            {'product_id': prod_q, 'quantity': 2, 'unit_price': 500000},
        ])
        complete(c, multi_order_id)

        oi_p = db_one('SELECT id, quantity FROM order_items WHERE order_id = ? AND product_id = ?',
                      (multi_order_id, prod_p))
        oi_q = db_one('SELECT id, quantity FROM order_items WHERE order_id = ? AND product_id = ?',
                      (multi_order_id, prod_q))

        # Return only 2 of P (not Q)
        r = post(c, '/returns/', {
            'order_id': str(multi_order_id),
            'return_reason': 'partial return',
            'items[0][order_item_id]': str(oi_p['id']),
            'items[0][product_id]': str(prod_p),
            'items[0][quantity]': '2',
            'items[0][refund_amount]': '600000',
        })
        has(r, 'created successfully', 'Partial return (P only) accepted on multi-item order')
        ret1 = db_one('SELECT id FROM returns ORDER BY id DESC LIMIT 1')

        # Confirm return_items only references product P
        ri_p = db_val(
            'SELECT COUNT(*) FROM return_items WHERE return_id = ? AND product_id = ?',
            (ret1['id'], prod_p))
        ri_q = db_val(
            'SELECT COUNT(*) FROM return_items WHERE return_id = ? AND product_id = ?',
            (ret1['id'], prod_q))
        eq(ri_p, 1, 'Return item row created for P')
        eq(ri_q, 0, 'No return item row created for Q (not returned)')

        # Cumulative return count: P has 2 returned, Q has 0
        already_p = db_val(
            '''SELECT COALESCE(SUM(ri.quantity), 0)
               FROM return_items ri JOIN returns r ON ri.return_id = r.id
               WHERE r.original_order_id = ? AND ri.product_id = ?''',
            (multi_order_id, prod_p))
        already_q = db_val(
            '''SELECT COALESCE(SUM(ri.quantity), 0)
               FROM return_items ri JOIN returns r ON ri.return_id = r.id
               WHERE r.original_order_id = ? AND ri.product_id = ?''',
            (multi_order_id, prod_q))
        eq(already_p, 2, 'Cumulative returned quantity for P = 2')
        eq(already_q, 0, 'Cumulative returned quantity for Q = 0 (independent tracking)')

        # Returning 1 more of P (cumulative 3 = ordered qty) → should succeed
        r = post(c, '/returns/', {
            'order_id': str(multi_order_id),
            'return_reason': 'second partial return of P',
            'items[0][order_item_id]': str(oi_p['id']),
            'items[0][product_id]': str(prod_p),
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '300000',
        })
        has(r, 'created successfully', 'Second partial return of P (reaching ordered qty) accepted')

        # Returning even 1 more of P now exceeds ordered qty (3) → should be rejected
        r = post(c, '/returns/', {
            'order_id': str(multi_order_id),
            'return_reason': 'over-return of P',
            'items[0][order_item_id]': str(oi_p['id']),
            'items[0][product_id]': str(prod_p),
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '300000',
        })
        has(r, 'exceeds', 'Third return of P rejected because cumulative qty exceeds ordered qty')

        # Q still has full return allowance (0 returned of 2 ordered)
        r = post(c, '/returns/', {
            'order_id': str(multi_order_id),
            'return_reason': 'return Q independently',
            'items[0][order_item_id]': str(oi_q['id']),
            'items[0][product_id]': str(prod_q),
            'items[0][quantity]': '2',
            'items[0][refund_amount]': '1000000',
        })
        has(r, 'created successfully', 'Full return of Q accepted independently (P limit does not affect Q)')

        # ── 11. Overpayment ────────────────────────────────────────────────
        section('11. Overpayment (Payments > Order Total)')

        prod_ov = mk_product(c, 'OV0001', 'Overpayment Product', cat)
        mk_intake(c, prod_ov, 3, 80000, 0, '2026-01-01')
        ov_order_id = mk_order(c, cust, [
            {'product_id': prod_ov, 'quantity': 1, 'unit_price': 200000},
        ])
        complete(c, ov_order_id)
        order_total = db_one('SELECT total_amount FROM orders WHERE id = ?', (ov_order_id,))['total_amount']
        money_eq(order_total, 200000, 'Overpayment test order total = 200,000')

        # Pay the full amount → fully_paid
        add_payment(c, ov_order_id, 200000)
        eq(db_one('SELECT payment_status FROM orders WHERE id = ?', (ov_order_id,))['payment_status'],
           'fully_paid', 'Order is fully_paid after exact payment')

        # Attempt to add a second payment that would exceed the order total
        r = add_payment(c, ov_order_id, 50000)
        has(r, 'exceeding order total', 'Second payment blocked because it would exceed order total')
        payments_total = db_val(
            'SELECT COALESCE(SUM(amount), 0) FROM payments WHERE order_id = ?', (ov_order_id,))
        money_eq(payments_total, order_total, 'Total payments stay at order total after rejected overpayment')

        # Exact-match final payment on a partially-paid order is still allowed
        prod_ov2 = mk_product(c, 'OV0002', 'Overpayment Product 2', cat)
        mk_intake(c, prod_ov2, 2, 80000, 0, '2026-01-01')
        ov2_order_id = mk_order(c, cust, [
            {'product_id': prod_ov2, 'quantity': 1, 'unit_price': 300000},
        ])
        complete(c, ov2_order_id)
        add_payment(c, ov2_order_id, 200000)
        r = add_payment(c, ov2_order_id, 100000)  # 200k + 100k = exactly 300k
        eq(db_one('SELECT payment_status FROM orders WHERE id = ?', (ov2_order_id,))['payment_status'],
           'fully_paid', 'Exact-match final payment reaches fully_paid without overpaying')

        # ── 12. Recovery Code Flow ─────────────────────────────────────────
        section('12. Recovery Code Flow (Generate → Use → Rotate)')

        # 12a. Generate recovery code (must be logged in)
        r = post(c, '/settings/recovery-code', {})
        http(r, 200, 'Recovery code generation returns 200')
        has(r, '-', 'Recovery code reveal page shows formatted code with dashes')

        body = r.data.decode('utf-8', errors='ignore')
        # Extract the code from the page — it renders as XXXX-XXXX-XXXX-XXXX-XXXX
        import re
        code_match = re.search(r'([A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4})', body)
        check(code_match is not None, 'Recovery code is visible in the reveal page response',
              f'Pattern not found in: {body[:300]}')

        if not code_match:
            fail('Cannot continue recovery flow — code not extractable from page')
        else:
            recovery_code = code_match.group(1)

            # Verify hash stored in DB
            code_hash = db_one('SELECT recovery_code_hash FROM settings LIMIT 1')['recovery_code_hash']
            check(code_hash is not None, 'Recovery code hash stored in DB after generation')

            # 12b. Log out and use the recovery code to reset password.
            # The /login/recover endpoint is not exempt from CSRF, so we must
            # seed the CSRF token into the (now-unauthenticated) session before
            # each POST, exactly as the login page would inject it via Jinja.
            c.get('/logout')
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN

            # Recovery page loads without auth
            r = c.get('/login/recover', follow_redirects=True)
            http(r, 200, 'Recovery page loads while logged out')
            has(r, 'recovery', 'Recovery page renders recovery form')

            # Wrong code rejected
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN
            r = c.post('/login/recover', data={
                'csrf_token': CSRF_TOKEN,
                'recovery_code': 'AAAA-BBBB-CCCC-DDDD-EEEE',
                'new_password': 'recovered123',
                'confirm_password': 'recovered123',
            }, follow_redirects=True)
            has(r, 'incorrect', 'Wrong recovery code is rejected')

            # Password mismatch rejected
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN
            r = c.post('/login/recover', data={
                'csrf_token': CSRF_TOKEN,
                'recovery_code': recovery_code,
                'new_password': 'recovered123',
                'confirm_password': 'different456',
            }, follow_redirects=True)
            has(r, 'do not match', 'Password mismatch rejected on recovery form')

            # Too-short password rejected
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN
            r = c.post('/login/recover', data={
                'csrf_token': CSRF_TOKEN,
                'recovery_code': recovery_code,
                'new_password': 'abc',
                'confirm_password': 'abc',
            }, follow_redirects=True)
            has(r, 'at least 4', 'Too-short password rejected on recovery form')

            # Valid recovery: correct code + matching passwords
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN
            r = c.post('/login/recover', data={
                'csrf_token': CSRF_TOKEN,
                'recovery_code': recovery_code,
                'new_password': 'recovered123',
                'confirm_password': 'recovered123',
            }, follow_redirects=True)
            has(r, 'Password Reset Successful', 'Recovery succeeds with correct code and matching passwords')

            # 12c. New password works on login
            r = c.post('/login', data={'username': 'admin', 'password': 'recovered123'},
                       follow_redirects=True)
            has(r, 'Dashboard', 'New password accepted after recovery')

            # 12d. Old password (admin123) no longer works
            c.get('/logout')
            r = c.post('/login', data={'username': 'admin', 'password': 'admin123'},
                       follow_redirects=True)
            hasnt(r, 'Dashboard', 'Old password rejected after recovery reset')

            # 12e. The used recovery code is rotated — cannot be reused
            with c.session_transaction() as sess:
                sess['csrf_token'] = CSRF_TOKEN
            r = c.post('/login/recover', data={
                'csrf_token': CSRF_TOKEN,
                'recovery_code': recovery_code,
                'new_password': 'hacker999',
                'confirm_password': 'hacker999',
            }, follow_redirects=True)
            has(r, 'incorrect', 'Used recovery code is rejected after rotation')

            # Restore session for remaining tests
            with c.session_transaction() as sess:
                sess['authenticated'] = True
                sess['role'] = 'admin'
                sess['csrf_token'] = CSRF_TOKEN

            # Reset admin password back to admin123 for clean state
            exec_db(
                "UPDATE users SET password_hash = ? WHERE role = 'admin'",
                (__import__('werkzeug.security', fromlist=['generate_password_hash'])
                 .generate_password_hash('admin123'),)
            )

        # ── 13. FIFO with 3+ Lots ──────────────────────────────────────────
        section('13. FIFO with 3+ Lots')

        prod_3 = mk_product(c, 'FL0001', 'Three-Lot Product', cat, sale=500000, cost=100000)
        lot1 = mk_intake(c, prod_3, 2, 100000, 20000, '2026-01-01')  # effective 110k/unit (20k / 2)
        lot2 = mk_intake(c, prod_3, 3, 120000,  0,    '2026-02-01')  # 120k/unit
        lot3 = mk_intake(c, prod_3, 4, 130000, 40000, '2026-03-01')  # effective 140k/unit (40k / 4)

        # Order 7 units: exhausts lot1(2) + lot2(3) + 2 from lot3
        order_3lot = mk_order(c, cust, [
            {'product_id': prod_3, 'quantity': 7, 'unit_price': 500000},
        ])
        complete(c, order_3lot)

        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot1,))['remaining_quantity'],
           0, 'Lot1 (oldest) fully consumed first')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot2,))['remaining_quantity'],
           0, 'Lot2 fully consumed second')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot3,))['remaining_quantity'],
           2, 'Lot3 has 2 remaining after taking 2 from it')

        # Allocation records: one per lot consumed (3 rows for 3 lots)
        alloc_count = db_val(
            'SELECT COUNT(*) FROM order_allocations WHERE order_id = ?', (order_3lot,))
        eq(alloc_count, 3, '3 allocation records — one per FIFO lot consumed')

        # COGS: 2*110k + 3*120k + 2*140k = 220k + 360k + 280k = 860k
        total_cogs = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations WHERE order_id = ?',
            (order_3lot,))
        money_eq(total_cogs, 860000, 'COGS = 2*110k + 3*120k + 2*140k = 860,000')

        # Verify per-lot cost_price_at_sale values
        cogs_lot1 = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations '
            'WHERE order_id = ? AND inventory_lot_id = ?', (order_3lot, lot1))
        cogs_lot2 = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations '
            'WHERE order_id = ? AND inventory_lot_id = ?', (order_3lot, lot2))
        cogs_lot3 = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations '
            'WHERE order_id = ? AND inventory_lot_id = ?', (order_3lot, lot3))
        money_eq(cogs_lot1, 220000, 'Lot1 COGS contribution = 2 * 110k = 220,000')
        money_eq(cogs_lot2, 360000, 'Lot2 COGS contribution = 3 * 120k = 360,000')
        money_eq(cogs_lot3, 280000, 'Lot3 COGS contribution = 2 * 140k = 280,000')

        # Reopen: all 3 lots fully restored
        post(c, f'/orders/{order_3lot}/status', {'status': 'draft'})
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot1,))['remaining_quantity'],
           2, 'Lot1 restored to 2 after reopen')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot2,))['remaining_quantity'],
           3, 'Lot2 restored to 3 after reopen')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot3,))['remaining_quantity'],
           4, 'Lot3 restored to 4 after reopen')
        eq(db_val('SELECT COUNT(*) FROM order_allocations WHERE order_id = ?', (order_3lot,)),
           0, 'All allocation records removed on reopen')

        # ── 14. is_active = False Effects ─────────────────────────────────
        section('14. is_active = False Effects on Order Form')

        prod_active   = mk_product(c, 'IA0001', 'Active Product',      cat, sale=100000, cost=50000)
        prod_inactive = mk_product(c, 'IA0002', 'Inactive Product',    cat, sale=100000, cost=50000)
        cust_active   = mk_customer(c, 'KH-IA01', 'Active Customer')
        cust_inactive = mk_customer(c, 'KH-IA02', 'Inactive Customer')

        # Deactivate product and customer
        post(c, f'/products/{prod_inactive}/delete', {})
        post(c, f'/customers/{cust_inactive}/delete', {})

        inactive_prod_row  = db_one('SELECT is_active FROM products  WHERE id = ?', (prod_inactive,))
        inactive_cust_row  = db_one('SELECT is_active FROM customers WHERE id = ?', (cust_inactive,))
        eq(inactive_prod_row['is_active'], 0, 'Product marked inactive after delete')
        eq(inactive_cust_row['is_active'], 0, 'Customer marked inactive after delete')

        # New order form must not expose inactive product or customer
        r = c.get('/orders/new')
        http(r, 200, 'New order form loads')
        has(r,    'Active Product',    'Active product appears in new order form')
        hasnt(r,  'Inactive Product',  'Inactive product does not appear in new order form')
        has(r,    'Active Customer',   'Active customer appears in new order form')
        hasnt(r,  'Inactive Customer', 'Inactive customer does not appear in new order form')

        # Edit order form (must reopen an existing draft for the edit form)
        mk_intake(c, prod_active, 5, 50000, 0, '2026-01-01')
        draft_id = mk_order(c, cust_active, [
            {'product_id': prod_active, 'quantity': 1, 'unit_price': 100000},
        ])
        r = c.get(f'/orders/{draft_id}/edit')
        http(r, 200, 'Edit order form loads')
        has(r,   'Active Product',    'Active product appears in edit order form')
        hasnt(r, 'Inactive Product',  'Inactive product does not appear in edit order form')
        has(r,   'Active Customer',   'Active customer appears in edit order form')
        hasnt(r, 'Inactive Customer', 'Inactive customer does not appear in edit order form')

        # Deactivated product must not appear in the inventory intake product picker
        r = c.get('/inventory/new')
        http(r, 200, 'Inventory intake form loads')
        has(r,   'Active Product',   'Active product in intake form')
        hasnt(r, 'Inactive Product', 'Inactive product not in intake form')

    return _PASS, _FAIL, _FAILURES


def cleanup():
    for suffix in ('', '-wal', '-shm'):
        path = _TEST_DB + suffix
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == '__main__':
    try:
        passed, failed, failures = run()
        print(f'\n{"─" * 54}')
        print(f'Results: {passed} passed, {failed} failed')
        if failures:
            print('\nFailures:')
            for name, detail in failures:
                print(f'  - {name}')
                if detail:
                    print(f'    {detail[:200]}')
        else:
            print('All edge case tests (batch 2) passed.')
        raise SystemExit(0 if failed == 0 else 1)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
    finally:
        cleanup()
