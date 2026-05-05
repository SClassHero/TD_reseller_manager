#!/usr/bin/env python3
"""
Edge case tests for inventory_app_v5.

Covers gaps in test_app.py and test_real_world_scenarios.py:
  1. Order cancellation — FIFO reversal, accounting reversal, guard on reopen
  2. Guards against editing/deleting non-draft orders via direct POST
  3. Cumulative refunds exceeding order total
  4. Payment and return on draft or cancelled orders
  5. Write-off boundary cases (quantity=0, negative-remaining lot)
  6. Multi-product FIFO order (two products in one order, each with multiple lots)
  7. Return with restock_quantity > return quantity
  8. discount_amount > subtotal (negative total)

Run:
    python test_edge_cases.py
"""
import os
import sys
import traceback

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import db as db_module
_TEST_DB = os.path.join(APP_DIR, f'_edge_test_{os.getpid()}.db')
db_module.DATABASE = _TEST_DB

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True

from db import get_db, init_db
from routes.dashboard import get_revenue_and_profit_data

CSRF_TOKEN = 'edge-csrf-token'
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
    if cond:
        ok(name)
    else:
        fail(name, detail)
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
    return check(text not in body, name, f'unexpected {text!r} found in response')

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

def dashboard_all():
    db = get_db()
    try:
        return get_revenue_and_profit_data(db, '2020-01-01 00:00:00', '2099-12-31 23:59:59')
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

def mk_order(c, customer_id, items, discount=0, shipping=0, paid_by='customer', date='2026-03-01'):
    data = {
        'customer_id': str(customer_id),
        'order_date': date,
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

def set_status(c, order_id, status):
    return post(c, f'/orders/{order_id}/status', {'status': status})

def add_payment(c, order_id, amount):
    return post(c, f'/orders/{order_id}/payment', {
        'amount': str(amount), 'payment_method': 'cash', 'notes': '',
    })

def mk_refund(c, order_id, amount, date='2026-04-01'):
    r = post(c, '/refunds/', {
        'order_id': str(order_id),
        'amount': str(amount),
        'refund_method': 'cash',
        'reason': 'customer_request',
        'notes': '',
        'refund_date': date,
    })
    return r


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
        cust = mk_customer(c, 'KH-E001', 'Edge Case Customer')

        # ── 1. Order Cancellation ──────────────────────────────────────────
        section('1. Order Cancellation')

        prod_cancel = mk_product(c, 'EC0001', 'Cancel Product', cat)
        lot_a = mk_intake(c, prod_cancel, 5, 80000, 50000, '2026-01-01')   # effective 90k/unit
        lot_b = mk_intake(c, prod_cancel, 5, 100000, 0, '2026-02-01')       # 100k/unit

        # Order 3 units — spans lot_a entirely consumed, lot_b partially
        cancel_order_id = mk_order(c, cust, [
            {'product_id': prod_cancel, 'quantity': 3, 'unit_price': 200000},
        ], shipping=30000, paid_by='customer')
        set_status(c, cancel_order_id, 'completed')

        stock_after_complete = db_val(
            'SELECT SUM(remaining_quantity) FROM inventory WHERE product_id = ?', (prod_cancel,))
        eq(stock_after_complete, 7, 'Stock = 7 after completing 3-unit order from 10 total')

        cust_spent_after = db_val('SELECT total_spent FROM customers WHERE id = ?', (cust,))
        # total = 3 * 200,000 + 30,000 = 630,000
        money_eq(cust_spent_after, 630000, 'customer.total_spent updated to 630,000 on completion')

        alloc_count = db_val('SELECT COUNT(*) FROM order_allocations WHERE order_id = ?', (cancel_order_id,))
        check(alloc_count > 0, 'Allocation records exist on a completed order')

        pre_cancel_dash = dashboard_all()

        # ── 1a. Cancel a completed order ──────────────────────────────────
        r = set_status(c, cancel_order_id, 'cancelled')
        has(r, 'Cancelled', 'Completed order can be cancelled')

        order_row = db_one('SELECT order_status FROM orders WHERE id = ?', (cancel_order_id,))
        eq(order_row['order_status'], 'cancelled', 'Order status = cancelled in DB')

        stock_after_cancel = db_val(
            'SELECT SUM(remaining_quantity) FROM inventory WHERE product_id = ?', (prod_cancel,))
        eq(stock_after_cancel, 10, 'Cancelling completed order restores all 3 units to correct lots')

        lot_a_remaining = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_a,))['remaining_quantity']
        lot_b_remaining = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_b,))['remaining_quantity']
        eq(lot_a_remaining, 5, 'Oldest lot fully restored after cancellation')
        eq(lot_b_remaining, 5, 'Second lot untouched (was not consumed) after cancellation')

        alloc_after_cancel = db_val('SELECT COUNT(*) FROM order_allocations WHERE order_id = ?', (cancel_order_id,))
        eq(alloc_after_cancel, 0, 'All allocation records removed after cancellation')

        cust_spent_after_cancel = db_val('SELECT total_spent FROM customers WHERE id = ?', (cust,))
        money_eq(cust_spent_after_cancel, 0, 'customer.total_spent reversed to 0 after cancellation')

        post_cancel_dash = dashboard_all()
        money_eq(post_cancel_dash['gross_revenue'], pre_cancel_dash['gross_revenue'] - 630000,
                 'Cancelled order removed from gross revenue')
        money_eq(post_cancel_dash['total_cogs'], pre_cancel_dash['total_cogs'] - 270000,
                 'Cancelled order COGS (3*90k) removed')

        # ── 1b. Cannot reopen a cancelled order ───────────────────────────
        r = set_status(c, cancel_order_id, 'draft')
        has(r, 'cannot be reopened', 'Cancelled order cannot be reopened to draft')
        still_cancelled = db_one('SELECT order_status FROM orders WHERE id = ?', (cancel_order_id,))
        eq(still_cancelled['order_status'], 'cancelled', 'Cancelled order stays cancelled after reopen attempt')

        r = set_status(c, cancel_order_id, 'processing')
        has(r, 'cannot be reopened', 'Cancelled order cannot be moved to processing')

        # ── 1c. Cancel a processing order ─────────────────────────────────
        prod_proc_cancel = mk_product(c, 'EC0002', 'Processing Cancel', cat)
        lot_pc = mk_intake(c, prod_proc_cancel, 4, 60000, 0, '2026-01-15')

        proc_order_id = mk_order(c, cust, [
            {'product_id': prod_proc_cancel, 'quantity': 2, 'unit_price': 120000},
        ])
        set_status(c, proc_order_id, 'processing')
        stock_mid = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_pc,))['remaining_quantity']
        eq(stock_mid, 2, 'Stock deducted on move to Processing')

        r = set_status(c, proc_order_id, 'cancelled')
        has(r, 'Cancelled', 'Processing order can be cancelled')
        stock_restored = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_pc,))['remaining_quantity']
        eq(stock_restored, 4, 'Stock restored after cancelling a processing order')

        # ── 1d. Cancel a draft order (no accounting to reverse) ───────────
        draft_order_id = mk_order(c, cust, [
            {'product_id': prod_cancel, 'quantity': 1, 'unit_price': 200000},
        ])
        stock_before_cancel_draft = db_val(
            'SELECT SUM(remaining_quantity) FROM inventory WHERE product_id = ?', (prod_cancel,))
        r = set_status(c, draft_order_id, 'cancelled')
        has(r, 'Cancelled', 'Draft order can be cancelled')
        stock_after_cancel_draft = db_val(
            'SELECT SUM(remaining_quantity) FROM inventory WHERE product_id = ?', (prod_cancel,))
        eq(stock_after_cancel_draft, stock_before_cancel_draft,
           'Cancelling a draft order does not change stock (no allocations to reverse)')

        # ── 2. Guards Against Non-Draft Edit/Delete ────────────────────────
        section('2. Guards Against Non-Draft Edit/Delete')

        prod_guard = mk_product(c, 'EC0010', 'Guard Product', cat)
        mk_intake(c, prod_guard, 5, 50000, 0, '2026-01-01')
        guard_order_id = mk_order(c, cust, [
            {'product_id': prod_guard, 'quantity': 1, 'unit_price': 100000},
        ])
        set_status(c, guard_order_id, 'processing')

        # Attempt DELETE on a processing order
        r = post(c, f'/orders/{guard_order_id}/delete', {})
        has(r, 'Only Draft orders can be deleted', 'DELETE on processing order is blocked with message')
        still_exists = db_one('SELECT id FROM orders WHERE id = ?', (guard_order_id,))
        check(still_exists is not None, 'Processing order NOT deleted after blocked delete attempt')

        set_status(c, guard_order_id, 'completed')

        # Attempt DELETE on a completed order
        r = post(c, f'/orders/{guard_order_id}/delete', {})
        has(r, 'Only Draft orders can be deleted', 'DELETE on completed order is blocked with message')
        still_exists = db_one('SELECT id FROM orders WHERE id = ?', (guard_order_id,))
        check(still_exists is not None, 'Completed order NOT deleted after blocked delete attempt')

        # Attempt POST edit on a completed order (not just the GET guard)
        prod_guard2 = mk_product(c, 'EC0011', 'Guard Product 2', cat)
        r = post(c, f'/orders/{guard_order_id}/edit', {
            'customer_id': str(cust),
            'order_date': '2026-03-01',
            'items[0][product_id]': str(prod_guard2),
            'items[0][quantity]': '1',
            'items[0][unit_price]': '999999',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        has(r, 'Only Draft orders can be edited', 'POST edit on completed order is blocked with message')
        unchanged = db_one('SELECT total_amount FROM orders WHERE id = ?', (guard_order_id,))
        money_eq(unchanged['total_amount'], 100000, 'Completed order total unchanged after blocked edit')

        # Reopen to test edit guard on processing
        set_status(c, guard_order_id, 'draft')
        set_status(c, guard_order_id, 'processing')
        r = post(c, f'/orders/{guard_order_id}/edit', {
            'customer_id': str(cust),
            'order_date': '2026-03-01',
            'items[0][product_id]': str(prod_guard2),
            'items[0][quantity]': '1',
            'items[0][unit_price]': '999999',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        has(r, 'Only Draft orders can be edited', 'POST edit on processing order is blocked with message')

        # ── 3. Cumulative Refunds Exceeding Order Total ────────────────────
        section('3. Cumulative Refunds Exceeding Order Total')

        prod_refund = mk_product(c, 'EC0020', 'Refund Product', cat)
        mk_intake(c, prod_refund, 5, 50000, 0, '2026-01-01')
        # Order total = 3 * 200,000 = 600,000
        refund_order_id = mk_order(c, cust, [
            {'product_id': prod_refund, 'quantity': 3, 'unit_price': 200000},
        ])
        set_status(c, refund_order_id, 'completed')
        order_total = db_one('SELECT total_amount FROM orders WHERE id = ?', (refund_order_id,))['total_amount']
        money_eq(order_total, 600000, 'Refund-test order total = 600,000')

        # First refund: 400,000 — individually valid
        r = mk_refund(c, refund_order_id, 400000, '2026-04-01')
        has(r, 'created successfully', 'First refund of 400,000 accepted')
        refund1_id = db_one('SELECT id FROM refunds WHERE order_id = ? ORDER BY id DESC LIMIT 1',
                            (refund_order_id,))['id']

        # Second refund: 300,000 — individually < 600,000 but cumulative 700,000 > 600,000
        r = mk_refund(c, refund_order_id, 300000, '2026-04-02')
        has(r, 'exceeds', 'Second refund rejected because cumulative total exceeds order total')
        refund_count = db_val('SELECT COUNT(*) FROM refunds WHERE order_id = ?', (refund_order_id,))
        eq(refund_count, 1, 'Only one refund row exists after rejection of cumulative over-refund')

        # Edit existing 400,000 refund to 700,000 — still individually within 600,000? No, 700k > 600k
        # That's already caught by single-refund check. Test editing to 500,000 instead:
        # Then try to add a second refund of 200,000 — cumulative = 700,000 > 600,000 → reject
        r = post(c, f'/refunds/{refund1_id}/edit', {
            'order_id': str(refund_order_id),
            'amount': '500000',
            'refund_method': 'cash',
            'reason': 'price_adjustment',
            'notes': 'Edit to 500k',
        })
        has(r, 'updated successfully', 'Edit refund to 500,000 accepted')

        r = mk_refund(c, refund_order_id, 200000, '2026-04-03')
        has(r, 'exceeds', 'Third refund (200k) rejected because 500k + 200k = 700k > 600k')
        refund_count2 = db_val('SELECT COUNT(*) FROM refunds WHERE order_id = ?', (refund_order_id,))
        eq(refund_count2, 1, 'Still only one refund row after second rejection')

        # Exact-match refund: 100,000 more to bring total to 600,000 → should succeed
        r = mk_refund(c, refund_order_id, 100000, '2026-04-04')
        has(r, 'created successfully', 'Exact-match second refund (500k + 100k = 600k) accepted')

        # ── 4. Payment and Return on Draft or Cancelled Orders ─────────────
        section('4. Payment/Return on Draft or Cancelled Orders')

        prod_inactive = mk_product(c, 'EC0030', 'Inactive Order Product', cat)
        mk_intake(c, prod_inactive, 5, 60000, 0, '2026-01-01')

        # 4a. Payment on a draft order
        draft_pay_id = mk_order(c, cust, [
            {'product_id': prod_inactive, 'quantity': 1, 'unit_price': 120000},
        ])
        r = add_payment(c, draft_pay_id, 120000)
        has(r, 'Processing or Completed', 'Payment blocked on draft order')
        payment_count = db_val('SELECT COUNT(*) FROM payments WHERE order_id = ?', (draft_pay_id,))
        eq(payment_count, 0, 'No payment row created for draft order')

        # 4b. Payment on a cancelled order
        set_status(c, draft_pay_id, 'processing')
        set_status(c, draft_pay_id, 'cancelled')
        r = add_payment(c, draft_pay_id, 120000)
        has(r, 'Processing or Completed', 'Payment blocked on cancelled order')
        payment_count2 = db_val('SELECT COUNT(*) FROM payments WHERE order_id = ?', (draft_pay_id,))
        eq(payment_count2, 0, 'No payment row created for cancelled order')

        # 4c. Return for a draft order
        draft_return_id = mk_order(c, cust, [
            {'product_id': prod_inactive, 'quantity': 1, 'unit_price': 120000},
        ])
        oi_id = db_one('SELECT id FROM order_items WHERE order_id = ?', (draft_return_id,))['id']
        r = post(c, '/returns/', {
            'order_id': str(draft_return_id),
            'return_reason': 'test',
            'items[0][order_item_id]': str(oi_id),
            'items[0][product_id]': str(prod_inactive),
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '120000',
        })
        has(r, 'Completed orders', 'Return blocked for draft order')
        return_count = db_val('SELECT COUNT(*) FROM returns WHERE original_order_id = ?', (draft_return_id,))
        eq(return_count, 0, 'No return row created for draft order')

        # 4d. Return for a processing order
        set_status(c, draft_return_id, 'processing')
        r = post(c, '/returns/', {
            'order_id': str(draft_return_id),
            'return_reason': 'test',
            'items[0][order_item_id]': str(oi_id),
            'items[0][product_id]': str(prod_inactive),
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '120000',
        })
        has(r, 'Completed orders', 'Return blocked for processing order')
        return_count2 = db_val('SELECT COUNT(*) FROM returns WHERE original_order_id = ?', (draft_return_id,))
        eq(return_count2, 0, 'No return row created for processing order')

        # 4e. Refund for a draft order (direct POST to /refunds/)
        draft_refund_order_id = mk_order(c, cust, [
            {'product_id': prod_inactive, 'quantity': 1, 'unit_price': 120000},
        ])
        r = mk_refund(c, draft_refund_order_id, 50000)
        has(r, 'Processing or Completed', 'Refund blocked for draft order')
        rf_count = db_val('SELECT COUNT(*) FROM refunds WHERE order_id = ?', (draft_refund_order_id,))
        eq(rf_count, 0, 'No refund row created for draft order')

        # ── 5. Write-Off Boundary Cases ────────────────────────────────────
        section('5. Write-Off Boundary Cases')

        prod_wo = mk_product(c, 'EC0040', 'Write-off Product', cat)
        lot_pos = mk_intake(c, prod_wo, 3, 70000, 30000, '2026-01-01')  # effective 80k/unit
        lot_neg = mk_intake(c, prod_wo, -2, 70000, 0, '2026-01-02')     # backorder marker

        # 5a. Write-off quantity = 0 → rejected
        r = post(c, f'/inventory/{lot_pos}/write-off', {
            'quantity': '0',
            'reason': 'damaged_unsellable',
            'notes': 'zero test',
        })
        has(r, 'greater than zero', 'Write-off with quantity=0 is rejected')
        still_3 = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_pos,))['remaining_quantity']
        eq(still_3, 3, 'Lot unchanged after rejected zero write-off')

        # 5b. Write-off from a negative-remaining lot → rejected
        r = post(c, f'/inventory/{lot_neg}/write-off', {
            'quantity': '1',
            'reason': 'damaged_unsellable',
            'notes': 'negative lot test',
        })
        has(r, 'no available stock', 'Write-off from negative backorder lot is rejected')
        adj_count_neg = db_val('SELECT COUNT(*) FROM inventory_adjustments WHERE inventory_lot_id = ?', (lot_neg,))
        eq(adj_count_neg, 0, 'No adjustment row created for write-off from negative lot')

        # 5c. Write-off exactly all remaining units → success (boundary case)
        r = post(c, f'/inventory/{lot_pos}/write-off', {
            'quantity': '3',
            'reason': 'lost',
            'notes': 'full lot write-off',
        })
        has(r, 'Wrote off 3 unit', 'Write-off of all remaining units succeeds')
        lot_pos_after = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_pos,))['remaining_quantity']
        eq(lot_pos_after, 0, 'Lot reduced to 0 after full write-off')

        adj = db_one('SELECT quantity_delta, unit_cost, total_cost FROM inventory_adjustments '
                     'WHERE inventory_lot_id = ? ORDER BY id DESC LIMIT 1', (lot_pos,))
        check(adj is not None, 'Adjustment row created for full write-off')
        eq(adj['quantity_delta'], -3, 'Write-off delta = -3')
        money_eq(adj['unit_cost'], 80000, 'Write-off unit cost = lot cost + amortized shipping (70k + 30k/3)')
        money_eq(adj['total_cost'], 240000, 'Write-off total cost = 3 * 80k = 240,000')

        # 5d. Write-off from now-zero lot → rejected
        r = post(c, f'/inventory/{lot_pos}/write-off', {
            'quantity': '1',
            'reason': 'damaged_unsellable',
        })
        has(r, 'no available stock', 'Write-off from zero-remaining lot is rejected')

        # 5e. Write-off quantity exceeds remaining → rejected with specific message
        prod_wo2 = mk_product(c, 'EC0041', 'Write-off Product 2', cat)
        lot_small = mk_intake(c, prod_wo2, 2, 50000, 0, '2026-01-01')
        r = post(c, f'/inventory/{lot_small}/write-off', {
            'quantity': '5',
            'reason': 'damaged_unsellable',
        })
        has(r, 'cannot exceed', 'Write-off exceeding remaining quantity is rejected')
        lot_small_unchanged = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_small,))['remaining_quantity']
        eq(lot_small_unchanged, 2, 'Lot unchanged after rejected over-limit write-off')

        # ── 6. Multi-Product FIFO Order ────────────────────────────────────
        section('6. Multi-Product FIFO Order')

        prod_a = mk_product(c, 'EC0050', 'Multi-FIFO A', cat, sale=300000, cost=100000)
        prod_b = mk_product(c, 'EC0051', 'Multi-FIFO B', cat, sale=500000, cost=200000)

        # Product A: 2 lots — old and new
        lot_a1 = mk_intake(c, prod_a, 3, 100000, 30000, '2026-01-01')  # effective 110k/unit
        lot_a2 = mk_intake(c, prod_a, 5, 120000, 0, '2026-02-01')       # 120k/unit
        # Product B: 2 lots
        lot_b1 = mk_intake(c, prod_b, 2, 200000, 40000, '2026-01-05')  # effective 220k/unit
        lot_b2 = mk_intake(c, prod_b, 4, 220000, 0, '2026-02-05')       # 220k/unit

        # Order: 4 of A (exhausts lot_a1 + 1 from lot_a2) + 3 of B (exhausts lot_b1 + 1 from lot_b2)
        multi_order_id = mk_order(c, cust, [
            {'product_id': prod_a, 'quantity': 4, 'unit_price': 300000},
            {'product_id': prod_b, 'quantity': 3, 'unit_price': 500000},
        ])
        set_status(c, multi_order_id, 'completed')

        # Verify Product A FIFO deductions
        lot_a1_after = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_a1,))['remaining_quantity']
        lot_a2_after = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_a2,))['remaining_quantity']
        eq(lot_a1_after, 0, 'Product A: oldest lot exhausted by multi-product order')
        eq(lot_a2_after, 4, 'Product A: newer lot has 1 unit taken (5 - 1 = 4 remaining)')

        # Verify Product B FIFO deductions
        lot_b1_after = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_b1,))['remaining_quantity']
        lot_b2_after = db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_b2,))['remaining_quantity']
        eq(lot_b1_after, 0, 'Product B: oldest lot exhausted by multi-product order')
        eq(lot_b2_after, 3, 'Product B: newer lot has 1 unit taken (4 - 1 = 3 remaining)')

        # Verify allocation records for both products
        alloc_a = db_val(
            'SELECT COUNT(*) FROM order_allocations WHERE order_id = ? AND product_id = ?',
            (multi_order_id, prod_a))
        alloc_b = db_val(
            'SELECT COUNT(*) FROM order_allocations WHERE order_id = ? AND product_id = ?',
            (multi_order_id, prod_b))
        eq(alloc_a, 2, 'Product A has 2 allocation records (spans 2 lots)')
        eq(alloc_b, 2, 'Product B has 2 allocation records (spans 2 lots)')

        # Verify COGS for Product A: 3*110k + 1*120k = 450k
        cogs_a = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations '
            'WHERE order_id = ? AND product_id = ?', (multi_order_id, prod_a))
        money_eq(cogs_a, 450000, 'Product A COGS = 3*110k + 1*120k = 450,000')

        # Verify COGS for Product B: 2*220k + 1*220k = 660k
        cogs_b = db_val(
            'SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations '
            'WHERE order_id = ? AND product_id = ?', (multi_order_id, prod_b))
        money_eq(cogs_b, 660000, 'Product B COGS = 2*220k + 1*220k = 660,000')

        # Reopen order → all stock fully restored for both products
        set_status(c, multi_order_id, 'draft')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_a1,))['remaining_quantity'],
           3, 'Product A lot_a1 fully restored after reopen')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_a2,))['remaining_quantity'],
           5, 'Product A lot_a2 fully restored after reopen')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_b1,))['remaining_quantity'],
           2, 'Product B lot_b1 fully restored after reopen')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (lot_b2,))['remaining_quantity'],
           4, 'Product B lot_b2 fully restored after reopen')
        alloc_after_reopen = db_val('SELECT COUNT(*) FROM order_allocations WHERE order_id = ?', (multi_order_id,))
        eq(alloc_after_reopen, 0, 'All allocations removed after reopening multi-product order')

        # ── 7. Return Restock Quantity > Return Quantity ───────────────────
        section('7. Return Restock Quantity > Return Quantity')

        prod_rstock = mk_product(c, 'EC0060', 'Restock Guard Product', cat)
        mk_intake(c, prod_rstock, 5, 80000, 0, '2026-01-01')
        restock_order_id = mk_order(c, cust, [
            {'product_id': prod_rstock, 'quantity': 2, 'unit_price': 160000},
        ])
        set_status(c, restock_order_id, 'completed')
        oi_rs = db_one('SELECT id FROM order_items WHERE order_id = ?', (restock_order_id,))

        # Try to return 2 but restock 3 — more than returned
        r = post(c, '/returns/', {
            'order_id': str(restock_order_id),
            'return_reason': 'customer return',
            'items[0][order_item_id]': str(oi_rs['id']),
            'items[0][product_id]': str(prod_rstock),
            'items[0][quantity]': '2',
            'items[0][refund_amount]': '320000',
            'items[0][restock]': 'on',
            'items[0][restock_quantity]': '3',
        })
        has(r, 'Restock quantity cannot exceed', 'Return blocked when restock_quantity > return quantity')
        return_count_rs = db_val('SELECT COUNT(*) FROM returns WHERE original_order_id = ?', (restock_order_id,))
        eq(return_count_rs, 0, 'No return row created when restock_quantity exceeds return quantity')

        # Return 2 and restock exactly 2 → should succeed
        r = post(c, '/returns/', {
            'order_id': str(restock_order_id),
            'return_reason': 'customer return',
            'items[0][order_item_id]': str(oi_rs['id']),
            'items[0][product_id]': str(prod_rstock),
            'items[0][quantity]': '2',
            'items[0][refund_amount]': '320000',
            'items[0][restock]': 'on',
            'items[0][restock_quantity]': '2',
        })
        has(r, 'created successfully', 'Return with restock_quantity = return quantity accepted')

        # ── 8. discount_amount > subtotal (negative total) ─────────────────
        section('8. discount_amount > subtotal (Negative Total)')

        prod_disc = mk_product(c, 'EC0070', 'Discount Edge Product', cat)
        mk_intake(c, prod_disc, 5, 50000, 0, '2026-01-01')
        # subtotal = 1 * 100,000; discount = 200,000 → total would be -100,000
        orders_before = db_val('SELECT COUNT(*) FROM orders')
        mk_order(c, cust, [
            {'product_id': prod_disc, 'quantity': 1, 'unit_price': 100000},
        ], discount=200000)
        orders_after = db_val('SELECT COUNT(*) FROM orders')

        eq(orders_after, orders_before, 'Order with discount > subtotal is rejected (no new order created)')
        # Confirm no order was committed with a negative total
        bad_order = db_one('SELECT total_amount FROM orders WHERE total_amount < 0')
        check(bad_order is None, 'No order with negative total_amount exists in DB')

        # Discount exactly equal to subtotal (edge: 0 total) → allowed
        mk_order(c, cust, [
            {'product_id': prod_disc, 'quantity': 1, 'unit_price': 100000},
        ], discount=100000)
        zero_order = db_one('SELECT total_amount FROM orders ORDER BY id DESC LIMIT 1')
        check(zero_order is not None, 'Order with discount = subtotal is accepted')
        money_eq(zero_order['total_amount'], 0, 'Order with discount = subtotal has total_amount = 0')

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
            print('All edge case tests passed.')
        raise SystemExit(0 if failed == 0 else 1)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
    finally:
        cleanup()
