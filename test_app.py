#!/usr/bin/env python3
"""
Integration test suite for inventory_app_v5.
Uses a temporary database — never touches inventory.db.
Run: python test_app.py
"""
import os, sys, io, csv, shutil, tempfile, traceback

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError for box/check chars)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# ── Patch DATABASE before any app code touches it ─────────────────────────
import db as db_module
_TEST_DB = tempfile.mktemp(suffix='_inv_test.db')
db_module.DATABASE = _TEST_DB

import backup as backup_module
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_app')
shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
os.makedirs(_TEST_BACKUPS, exist_ok=True)
backup_module.BACKUPS_DIR = _TEST_BACKUPS

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True

from db import init_db, get_db

# ── Output helpers ─────────────────────────────────────────────────────────
_PASS = _FAIL = 0
_FAILURES = []

def ok(name):
    global _PASS; _PASS += 1
    print(f'  \033[92m✓\033[0m {name}')

def fail(name, detail=''):
    global _FAIL; _FAIL += 1
    _FAILURES.append((name, detail))
    detail_str = str(detail)[:300] if detail else ''
    print(f'  \033[91m✗\033[0m {name}')
    if detail_str:
        print(f'    \033[93m{detail_str}\033[0m')

def section(title):
    print(f'\n\033[1m── {title} ──\033[0m')

def check(condition, name, detail=''):
    if condition:
        ok(name)
    else:
        fail(name, detail)
    return condition

def has(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text in body, name, f'Expected {text!r} not found')

def hasnt(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text not in body, name, f'Unexpected {text!r} found in response')

def status(resp, code, name):
    return check(resp.status_code == code, name, f'HTTP {resp.status_code} ≠ {code}')

# ── CSRF-aware POST ─────────────────────────────────────────────────────────
CSRF_TOKEN = 'test-csrf-token-abc123'

def post(c, url, data=None, files=None, **kw):
    d = dict(data or {})
    d['csrf_token'] = CSRF_TOKEN
    return c.post(url, data=d, follow_redirects=True, **kw)

def post_file(c, url, field, content, filename, extra=None):
    d = dict(extra or {})
    d['csrf_token'] = CSRF_TOKEN
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

# ──────────────────────────────────────────────────────────────────────────
# MAIN TEST RUNNER
# ──────────────────────────────────────────────────────────────────────────
def run():
    global _PASS, _FAIL, _FAILURES
    _PASS = _FAIL = 0
    _FAILURES = []

    # Fresh test DB
    init_db()

    with flask_app.test_client() as c:
        # Inject known CSRF token into session
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF_TOKEN

        # ── 1. Auth ────────────────────────────────────────────────────────
        section('Authentication')

        # Wrong password
        r = c.post('/login', data={'password': 'wrongpass'}, follow_redirects=True)
        has(r, 'Invalid password', 'Wrong password shows error message')

        # CSRF check — POST to authenticated endpoint without token should redirect
        with c.session_transaction() as sess:
            sess['authenticated'] = True  # simulate logged-in
        r = c.post('/switch-currency', data={}, follow_redirects=True)
        has(r, 'Invalid or expired request token', 'POST without CSRF token is rejected')

        # Login with correct password
        r = c.post('/login', data={'password': 'admin123'}, follow_redirects=True)
        has(r, 'Dashboard', 'Login with correct password succeeds')

        # Ensure session is authenticated
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['role'] = 'admin'
            sess['csrf_token'] = CSRF_TOKEN

        # Limited account can view only operational pages
        r = post(c, '/settings/limited-account', {
            'username': 'limited',
            'password': 'limited123',
            'confirm_password': 'limited123',
            'is_active': '1',
        })
        has(r, 'Limited access account saved', 'Limited account can be created by admin')
        limited = db_query("SELECT username, role, is_active FROM users WHERE username='limited'")
        check(limited and limited['role'] == 'limited' and limited['is_active'] == 1,
              'Limited user stored with limited role')

        c.get('/logout')
        r = c.post('/login', data={'username': 'limited', 'password': 'limited123'},
                   follow_redirects=True)
        has(r, 'Orders', 'Limited user lands on Orders')
        hasnt(r, 'Dashboard</a>', 'Limited nav hides Dashboard')
        hasnt(r, 'Reports</a>', 'Limited nav hides Reports')
        hasnt(r, 'Settings</a>', 'Limited nav hides Settings')

        for url, label in [
            ('/products/', 'Limited can view Products'),
            ('/inventory/', 'Limited can view Inventory'),
            ('/customers/', 'Limited can view Customers'),
            ('/orders/', 'Limited can view Orders'),
        ]:
            r = c.get(url)
            status(r, 200, label)

        for url, label in [
            ('/dashboard/', 'Limited cannot view Dashboard'),
            ('/reports/', 'Limited cannot view Reports'),
            ('/settings/', 'Limited cannot view Settings'),
            ('/exports/', 'Limited cannot view Exports'),
            ('/returns/', 'Limited cannot view Returns'),
            ('/refunds/', 'Limited cannot view Refunds'),
            ('/products/new', 'Limited cannot open product form'),
        ]:
            r = c.get(url, follow_redirects=True)
            has(r, 'Orders', label)

        before_products = db_count('SELECT COUNT(*) FROM products')
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF_TOKEN
        r = post(c, '/products/', {
            'product_code': 'NOPE0001',
            'name': 'Blocked Product',
            'sale_price': '1',
            'cost_price': '1',
        })
        has(r, 'cannot make changes', 'Limited POST is blocked')
        check(db_count('SELECT COUNT(*) FROM products') == before_products,
              'Limited POST does not create product')

        c.get('/logout')
        r = c.post('/login', data={'username': 'admin', 'password': 'admin123'},
                   follow_redirects=True)
        has(r, 'Dashboard', 'Admin can log back in after limited check')
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF_TOKEN

        # Rate limiting: trigger lockout
        with c.session_transaction() as sess:
            sess.pop('authenticated', None)
        for i in range(5):
            c.post('/login', data={'password': 'bad'}, follow_redirects=True)
        r = c.post('/login', data={'password': 'bad'}, follow_redirects=True)
        has(r, 'Too many failed', 'Login lockout triggers after 5 failures')

        # Re-authenticate for remaining tests
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['csrf_token'] = CSRF_TOKEN
            sess.pop('login_attempts', None)
            sess.pop('login_lockout_until', None)

        # ── 2. Dashboard ───────────────────────────────────────────────────
        section('Dashboard')
        r = c.get('/dashboard/')
        status(r, 200, 'Dashboard loads')
        has(r, 'Dashboard', 'Dashboard page renders')
        body = r.data.decode('utf-8', errors='ignore')
        header_title = body.split('<h1 class="app-title">', 1)[1].split('</h1>', 1)[0]
        check('Dashboard' in header_title and 'Inventory Manager' not in header_title,
              'Top header uses current page title instead of static app name')

        # ── 3. Categories ─────────────────────────────────────────────────
        section('Categories')

        r = c.get('/categories/')
        status(r, 200, 'Categories list loads')

        # Create
        r = post(c, '/categories/', {'name': 'Electronics', 'description': 'Electronic goods'})
        has(r, 'Electronics', 'Category created and visible in list')
        cat_id = db_query("SELECT id FROM categories WHERE name='Electronics'")['id']
        check(cat_id is not None, 'Category exists in database')

        # Duplicate name rejected
        r = post(c, '/categories/', {'name': 'Electronics', 'description': 'dup'})
        has(r, 'already exists', 'Duplicate category name is rejected')

        # Edit
        r = post(c, f'/categories/{cat_id}', {'name': 'Electronics', 'description': 'Updated desc'})
        updated = db_query(f"SELECT description FROM categories WHERE id={cat_id}")
        check(updated and updated['description'] == 'Updated desc', 'Category description updated')

        r = c.get('/categories/?sort=product_count&direction=desc&per_page=10')
        status(r, 200, 'Categories sortable list loads')
        has(r, 'Rows', 'Categories list has row-count control')
        has(r, 'Showing', 'Categories list shows visible result count')
        r = c.get('/categories/?sort=not_a_sort&direction=sideways&per_page=999&page=-2')
        status(r, 200, 'Categories list tolerates invalid browse controls')

        # ── 4. Products ────────────────────────────────────────────────────
        section('Products')

        r = c.get('/products/')
        status(r, 200, 'Products list loads')

        # Create product
        r = post(c, '/products/', {
            'product_code': 'EL0001',
            'name': 'Test Laptop',
            'category_id': cat_id,
            'sale_price': '15000000',
            'cost_price': '10000000',
            'min_stock_level': '2',
            'barcode': 'BAR001',
        })
        has(r, 'Test Laptop', 'Product created and visible')
        prod_id = db_query("SELECT id FROM products WHERE product_code='EL0001'")['id']
        check(prod_id is not None, 'Product exists in database')

        # Duplicate code rejected
        r = post(c, '/products/', {
            'product_code': 'EL0001', 'name': 'Dup', 'sale_price': '0', 'cost_price': '0'
        })
        has(r, 'already in use', 'Duplicate product code is rejected')

        # Generate code endpoint
        r = c.get(f'/products/generate-code?category_id={cat_id}')
        status(r, 200, 'Product code generation endpoint works')
        import json
        data = json.loads(r.data)
        check('code' in data and data['code'].startswith('EL'), 'Generated code has correct category prefix')

        # Edit product
        r = post(c, f'/products/{prod_id}', {
            'product_code': 'EL0001',
            'name': 'Test Laptop Pro',
            'category_id': cat_id,
            'sale_price': '16000000',
            'cost_price': '10000000',
            'min_stock_level': '2',
        })
        updated = db_query(f"SELECT name FROM products WHERE id={prod_id}")
        check(updated and updated['name'] == 'Test Laptop Pro', 'Product name updated')

        r = c.get('/products/?search=EL0001&sort=code&direction=desc&per_page=10')
        status(r, 200, 'Products sortable list loads')
        has(r, 'EL0001', 'Products list displays product code in sortable view')
        has(r, 'Rows', 'Products list has row-count control')
        has(r, 'Showing', 'Products list shows visible result count')
        r = c.get('/products/?sort=not_a_sort&direction=sideways&per_page=999&page=-5')
        status(r, 200, 'Products list tolerates invalid browse controls')

        # ── 5. Customers ───────────────────────────────────────────────────
        section('Customers')

        r = c.get('/customers/')
        status(r, 200, 'Customers list loads')

        # Create customer
        r = post(c, '/customers/', {
            'customer_code': 'KH000001',
            'name': 'Nguyen Van A',
            'email': 'nguyenvana@example.com',
            'phone': '0901234567',
            'region': 'HCM',
            'address': '123 Le Loi, HCM',
        })
        has(r, 'Nguyen Van A', 'Customer created and visible')
        cust_id = db_query("SELECT id FROM customers WHERE customer_code='KH000001'")['id']
        check(cust_id is not None, 'Customer exists in database')

        # Duplicate code rejected
        r = post(c, '/customers/', {
            'customer_code': 'KH000001', 'name': 'Dup Customer'
        })
        has(r, 'already in use', 'Duplicate customer code is rejected')

        # Customer detail page
        r = c.get(f'/customers/{cust_id}')
        status(r, 200, 'Customer detail page loads')
        has(r, 'Nguyen Van A', 'Customer name shown on detail page')

        # Edit customer
        r = post(c, f'/customers/{cust_id}', {
            'customer_code': 'KH000001',
            'name': 'Nguyen Van A (Updated)',
            'email': 'nguyenvana@example.com',
            'phone': '0901234567',
            'region': 'HN',
        })
        updated = db_query(f"SELECT name, region FROM customers WHERE id={cust_id}")
        check(updated and updated['region'] == 'HN', 'Customer region updated')

        # Generate code endpoint
        r = c.get('/customers/generate-code')
        status(r, 200, 'Customer code generation endpoint works')
        data = json.loads(r.data)
        check('code' in data and data['code'].startswith('KH'), 'Generated code has KH prefix')

        r = c.get('/customers/?search=Nguyen&sort=debt&direction=desc&per_page=10')
        status(r, 200, 'Customers sortable list loads')
        has(r, 'Outstanding Debt', 'Customers list can sort by live debt field')
        has(r, 'Rows', 'Customers list has row-count control')
        has(r, 'Showing', 'Customers list shows visible result count')
        r = c.get('/customers/?sort=not_a_sort&direction=sideways&per_page=999&page=-3')
        status(r, 200, 'Customers list tolerates invalid browse controls')

        # ── 6. Inventory Intake ────────────────────────────────────────────
        section('Inventory Intake')

        r = c.get('/inventory/')
        status(r, 200, 'Inventory list loads')

        # Create intake — 10 units at 10,000,000 VND each + 200,000 VND shipping
        r = post(c, '/inventory/', {
            'product_id': prod_id,
            'quantity': '10',
            'cost_price': '10000000',
            'shipping_cost': '200000',
            'currency': 'VND',
            'intake_date': '2026-04-01',
            'notes': 'Initial stock',
        })
        has(r, 'recorded', 'Inventory intake created')
        lot_id = db_query("SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1", (prod_id,))['id']
        lot = db_query(f"SELECT * FROM inventory WHERE id={lot_id}")
        check(lot and lot['quantity'] == 10, 'Correct quantity recorded in DB')
        check(lot and lot['shipping_cost'] == 200000, 'Shipping cost stored in DB')
        check(lot and lot['remaining_quantity'] == 10, 'Full quantity available initially')

        # Second intake batch — 5 units, newer date
        r = post(c, '/inventory/', {
            'product_id': prod_id,
            'quantity': '5',
            'cost_price': '11000000',
            'shipping_cost': '100000',
            'currency': 'VND',
            'intake_date': '2026-04-15',
            'notes': 'Second batch',
        })
        has(r, 'recorded', 'Second inventory intake created')
        lot2_id = db_query("SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1", (prod_id,))['id']

        r = c.get('/inventory/')
        has(r, 'Product name, code, or barcode', 'Inventory page has product search')
        has(r, 'inventory-product-card', 'Inventory page uses product summary cards')
        has(r, 'Available Units', 'Inventory page shows compact stock totals')
        has(r, 'active lots', 'Inventory page collapses lot detail under active-lot summary')
        has(r, 'Rows', 'Inventory list has row-count control')
        r = c.get('/inventory/?q=EL0001')
        has(r, 'Test Laptop Pro', 'Inventory search finds product by code')
        r = c.get('/inventory/?q=EL0001&sort=available&direction=desc&per_page=10')
        has(r, 'Showing', 'Inventory sortable list shows visible result count')
        r = c.get('/inventory/?sort=not_a_sort&direction=sideways&per_page=999&page=-4')
        status(r, 200, 'Inventory list tolerates invalid browse controls')
        r = c.get('/inventory/?q=no-match-for-inventory')
        has(r, 'No products match this inventory view', 'Inventory search shows empty state')

        # ── 7. Orders — Full Lifecycle ─────────────────────────────────────
        section('Orders — Full Lifecycle')

        r = c.get('/orders/')
        status(r, 200, 'Orders list loads')

        r = c.get('/orders/new')
        status(r, 200, 'New order form loads')

        # Create order (2 units of the product at 14,500,000 each, customer-paid shipping)
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-20',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '14500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '50000',
            'shipping_paid_by': 'customer',
            'notes': 'Test order',
        })
        has(r, 'HD000001', 'Order created with correct code')
        order_id = db_query("SELECT id FROM orders WHERE order_code='HD000001'")['id']
        order = db_query(f"SELECT * FROM orders WHERE id={order_id}")
        # subtotal = 2 * 14,500,000 = 29,000,000 ; total = 29,000,000 + 50,000 = 29,050,000
        check(order['order_status'] == 'draft', 'Order starts in draft status')
        check(order['total_amount'] == 29050000, f'Total amount correct (got {order["total_amount"]})')
        check(order['payment_status'] == 'not_paid', 'Payment status starts as not_paid')

        # Order detail
        r = c.get(f'/orders/{order_id}')
        status(r, 200, 'Order detail loads')
        has(r, 'HD000001', 'Order code shown on detail page')

        r = c.get('/orders/?search=HD000001&sort=total&direction=desc&per_page=10')
        status(r, 200, 'Orders sortable list loads')
        has(r, 'Payment', 'Orders list includes payment status column for browsing')
        has(r, 'Showing', 'Orders list shows visible result count')
        r = c.get('/orders/?sort=not_a_sort&direction=sideways&per_page=999&page=-5')
        status(r, 200, 'Orders list tolerates invalid browse controls')

        # Move to Processing
        r = post(c, f'/orders/{order_id}/status', {'status': 'processing'})
        has(r, 'Processing', 'Order moved to Processing')
        order = db_query(f"SELECT order_status FROM orders WHERE id={order_id}")
        check(order['order_status'] == 'processing', 'Order status is processing in DB')

        # Verify Processing reserves stock and writes FIFO allocations
        lot_check = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot_check['remaining_quantity'] == 8, 'Inventory deducted/reserved in Processing state')
        processing_alloc_count = db_count("SELECT COUNT(*) FROM order_allocations WHERE order_id=?", (order_id,))
        check(processing_alloc_count > 0, 'FIFO allocation records written when order moves to Processing')

        # Complete the order — status changes only because stock was already reserved in Processing
        r = post(c, f'/orders/{order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Order marked as Completed')
        order = db_query(f"SELECT order_status FROM orders WHERE id={order_id}")
        check(order['order_status'] == 'completed', 'Order status is completed in DB')

        # Verify FIFO deduction: 2 units taken from lot1 (oldest)
        lot1_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot1_after['remaining_quantity'] == 8, f'FIFO: 2 units deducted from oldest lot (remaining={lot1_after["remaining_quantity"]})')

        # Verify lot2 untouched (wasn't needed — only 2 units ordered from a 10-unit lot)
        lot2_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot2_id}")
        check(lot2_after['remaining_quantity'] == 5, 'FIFO: newer lot untouched')

        # Verify allocation records exist
        alloc_count = db_count("SELECT COUNT(*) FROM order_allocations WHERE order_id=?", (order_id,))
        check(alloc_count == processing_alloc_count, 'Completing an already-reserved order does not duplicate allocations')

        # Verify shipping cost folded into cost_price_at_sale
        # lot1: cost_price=10,000,000; shipping_cost=200,000; qty=10 → effective=10,020,000 per unit
        alloc = db_query("SELECT cost_price_at_sale FROM order_allocations WHERE order_id=? LIMIT 1", (order_id,))
        expected_effective = 10000000 + 200000 / 10  # = 10,020,000
        check(
            alloc and abs(alloc['cost_price_at_sale'] - expected_effective) < 1,
            f'Shipping cost amortised into COGS (expected {expected_effective}, got {alloc["cost_price_at_sale"] if alloc else "N/A"})'
        )

        # Verify customer total_spent updated
        cust = db_query(f"SELECT total_spent FROM customers WHERE id={cust_id}")
        check(cust['total_spent'] == 29050000, f'Customer total_spent updated on completion (got {cust["total_spent"]})')

        # ── 8. Payments ────────────────────────────────────────────────────
        section('Payments')

        # Add partial payment
        r = post(c, f'/orders/{order_id}/payment', {
            'amount': '15000000',
            'payment_method': 'bank_transfer',
            'notes': 'Partial pay',
        })
        has(r, 'successfully', 'Partial payment added')
        order = db_query(f"SELECT payment_status FROM orders WHERE id={order_id}")
        check(order['payment_status'] == 'partially_paid', 'Payment status: partially_paid after partial payment')

        # Get payment id
        pmt_id = db_query("SELECT id FROM payments WHERE order_id=? ORDER BY id DESC LIMIT 1", (order_id,))['id']

        # Edit payment
        r = post(c, f'/orders/{order_id}/payment/{pmt_id}/edit', {
            'amount': '20000000',
            'payment_method': 'cash',
            'notes': 'Updated',
        })
        has(r, 'successfully', 'Payment edited')
        pmt = db_query(f"SELECT amount FROM payments WHERE id={pmt_id}")
        check(pmt['amount'] == 20000000, 'Payment amount updated in DB')

        # Pay remainder to reach full payment
        r = post(c, f'/orders/{order_id}/payment', {
            'amount': '9050000',
            'payment_method': 'cash',
            'notes': 'Final',
        })
        order = db_query(f"SELECT payment_status FROM orders WHERE id={order_id}")
        check(order['payment_status'] == 'fully_paid', 'Payment status: fully_paid after paying balance')

        # Delete first payment
        r = post(c, f'/orders/{order_id}/payment/{pmt_id}/delete', {})
        order = db_query(f"SELECT payment_status FROM orders WHERE id={order_id}")
        check(order['payment_status'] == 'partially_paid', 'Payment status recalculated after payment deletion')

        # ── 9. Order Edit ──────────────────────────────────────────────────
        section('Order Edit')

        # Reopen to draft
        r = post(c, f'/orders/{order_id}/status', {'status': 'draft'})
        has(r, 'Draft', 'Order reopened to Draft')

        # Verify inventory restored exactly
        lot1_restored = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot1_restored['remaining_quantity'] == 10, f'Inventory fully restored on reopen (remaining={lot1_restored["remaining_quantity"]})')

        # Verify customer total_spent reversed
        cust = db_query(f"SELECT total_spent FROM customers WHERE id={cust_id}")
        check(cust['total_spent'] == 0, f'Customer total_spent reversed on reopen (got {cust["total_spent"]})')

        # Edit order (add discount)
        r = post(c, f'/orders/{order_id}/edit', {
            'customer_id': cust_id,
            'order_date': '2026-04-20',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '14500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '500000',
            'shipping_fee': '50000',
            'shipping_paid_by': 'customer',
            'notes': 'With discount',
        })
        order = db_query(f"SELECT total_amount, discount_amount FROM orders WHERE id={order_id}")
        # total = 29,000,000 - 500,000 + 50,000 = 28,550,000
        check(order['total_amount'] == 28550000, f'Order total recalculated after edit (got {order["total_amount"]})')
        check(order['discount_amount'] == 500000, 'Discount stored correctly')

        # Re-complete to restore FIFO state for subsequent tests
        r = post(c, f'/orders/{order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Order re-completed after edit')

        # ── 10. Second Order (tests FIFO spanning lots) ────────────────────
        section('FIFO Spanning Multiple Lots')

        # Create order for 11 units — should span lot1 (8 remaining) + lot2 (3 from 5)
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-21',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '11',
            'items[0][unit_price]': '14000000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        order2_id = db_query("SELECT id FROM orders WHERE order_code='HD000002'")['id']
        r = post(c, f'/orders/{order2_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Second order completed')

        lot1_after2 = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        lot2_after2 = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot2_id}")
        check(lot1_after2['remaining_quantity'] == 0, f'Lot1 fully consumed by second order (remaining={lot1_after2["remaining_quantity"]})')
        check(lot2_after2['remaining_quantity'] == 2, f'Lot2 partially consumed (remaining={lot2_after2["remaining_quantity"]})')

        alloc_count2 = db_count("SELECT COUNT(*) FROM order_allocations WHERE order_id=?", (order2_id,))
        check(alloc_count2 == 2, f'Allocation split across 2 lots (got {alloc_count2} records)')

        # ── 11. Returns ────────────────────────────────────────────────────
        section('Returns — with FIFO inventory restoration')

        r = c.get('/returns/')
        status(r, 200, 'Returns list loads')

        r = c.get('/returns/new')
        status(r, 200, 'New return form loads')

        # Get order items for return form
        r = c.get(f'/returns/new/items/{order_id}')
        status(r, 200, 'Return items endpoint loads for order')
        has(r, 'return-item-card', 'Return item picker uses mobile-friendly cards')
        has(r, 'Add sellable quantity back to inventory', 'Return item picker explains restock action')
        has(r, 'Optional resale cost', 'Return item picker hides rare cost fields behind details')
        has(r, 'Most returns should stay 0', 'Return item picker explains optional returned-goods cost')
        hasnt(r, '<th>Restock Unit Cost</th>', 'Return item picker no longer exposes restock cost as table column')

        # Fetch order item id
        oi = db_query("SELECT id FROM order_items WHERE order_id=?", (order_id,))
        oi_id = oi['id']

        # Create return for 1 unit of first order with returned-goods intake
        r = post(c, '/returns/', {
            'order_id': order_id,
            'return_reason': 'Defective unit',
            'restore_inventory': 'on',
            'items[0][order_item_id]': oi_id,
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '14500000',
            'items[0][restock_unit_cost]': '0',
            'items[0][restock_shipping_cost]': '0',
        })
        has(r, 'TR000001', 'Return created with correct code')

        # Returned goods are not restored to the original FIFO lot. They become a
        # new zero-cost returned-goods intake lot so original sale COGS stays put.
        lot1_after_return = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot1_after_return['remaining_quantity'] == 0, f'Original FIFO lot not restored by return (remaining={lot1_after_return["remaining_quantity"]})')
        return_lot = db_query("""
            SELECT i.quantity, i.remaining_quantity, i.cost_price,
                   ri.restock_action, ri.restock_quantity, ri.restock_inventory_lot_id
            FROM return_items ri
            JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.return_id = (SELECT id FROM returns WHERE return_code='TR000001')
        """)
        check(return_lot is not None, 'Return created a returned-goods intake lot')
        check(return_lot['quantity'] == 1, f'Returned-goods lot quantity is 1 (got {return_lot["quantity"]})')
        check(return_lot['remaining_quantity'] == 1, f'Returned-goods lot has 1 unit available (remaining={return_lot["remaining_quantity"]})')
        check(return_lot['cost_price'] == 0, f'Returned-goods lot defaults to zero cost (got {return_lot["cost_price"]})')
        check(return_lot['restock_action'] == 'return_intake', 'Return item records return_intake restock action')
        check(return_lot['restock_quantity'] == 1, 'Return item records full restock quantity')

        # Return detail
        ret_id = db_query("SELECT id FROM returns WHERE return_code='TR000001'")['id']
        r = c.get(f'/returns/{ret_id}')
        status(r, 200, 'Return detail page loads')
        has(r, 'TR000001 - Returns', 'Return detail top header includes return context')
        has(r, 'Returned item value', 'Return detail uses new returned-goods value wording')

        r = c.get('/returns/?search=TR000001&sort=code&direction=asc&per_page=10')
        status(r, 200, 'Returns sortable list loads')
        has(r, 'TR000001', 'Returns list finds return by code')
        has(r, 'Rows', 'Returns list has row-count control')
        has(r, 'Showing', 'Returns list shows visible result count')

        # ── 12. Refunds ────────────────────────────────────────────────────
        section('Refunds — CRUD + amount validation')

        r = c.get('/refunds/')
        status(r, 200, 'Refunds list loads')

        r = c.get('/refunds/new')
        status(r, 200, 'New refund form loads')

        # Valid refund
        r = post(c, '/refunds/', {
            'order_id': order_id,
            'amount': '500000',
            'refund_method': 'bank_transfer',
            'reason': 'customer_request',
            'notes': 'Goodwill refund',
        })
        has(r, 'RF000001', 'Refund created with correct code')
        refund_id = db_query("SELECT id FROM refunds WHERE refund_code='RF000001'")['id']

        r = c.get('/refunds/?search=RF000001&sort=amount&direction=desc&per_page=10')
        status(r, 200, 'Refunds sortable list loads')
        has(r, 'RF000001', 'Refunds list finds refund by code')
        has(r, 'Rows', 'Refunds list has row-count control')
        has(r, 'Showing', 'Refunds list shows visible result count')

        # Refund exceeding order total is rejected
        order_total = db_query(f"SELECT total_amount FROM orders WHERE id={order_id}")['total_amount']
        r = post(c, '/refunds/', {
            'order_id': order_id,
            'amount': str(order_total + 1),
            'refund_method': 'cash',
            'reason': 'other',
        })
        has(r, 'exceeds order total', 'Refund > order total is rejected')

        # Edit refund
        r = post(c, f'/refunds/{refund_id}/edit', {
            'order_id': order_id,
            'amount': '600000',
            'refund_method': 'cash',
            'reason': 'price_adjustment',
            'notes': 'Updated',
        })
        updated = db_query(f"SELECT amount FROM refunds WHERE id={refund_id}")
        check(updated and updated['amount'] == 600000, 'Refund amount updated')

        # Edit refund with amount > total rejected
        r = post(c, f'/refunds/{refund_id}/edit', {
            'order_id': order_id,
            'amount': str(order_total + 1),
            'refund_method': 'cash',
            'reason': 'other',
        })
        has(r, 'exceeds order total', 'Refund edit > order total is rejected')

        # Refund detail
        r = c.get(f'/refunds/{refund_id}')
        status(r, 200, 'Refund detail loads')

        # Delete refund
        r = post(c, f'/refunds/{refund_id}/delete', {})
        has(r, 'deleted', 'Refund deleted')
        count = db_count("SELECT COUNT(*) FROM refunds WHERE id=?", (refund_id,))
        check(count == 0, 'Refund removed from database')

        # ── 13. Backorder — Order Before Stock Arrives ────────────────────
        section('Backorder — Order Before Stock Arrives')

        # Create a product with ZERO inventory
        r = post(c, '/products/', {
            'product_code': 'BO0001',
            'name': 'Backordered Widget',
            'sale_price': '800000',
            'cost_price': '500000',
            'min_stock_level': '0',
        })
        bo_prod_id = db_query("SELECT id FROM products WHERE product_code='BO0001'")['id']
        check(bo_prod_id is not None, 'Backordered product created with no inventory')

        initial_stock = db_count(
            "SELECT COALESCE(SUM(remaining_quantity),0) FROM inventory WHERE product_id=?",
            (bo_prod_id,))
        check(initial_stock == 0, f'Product starts with zero inventory (got {initial_stock})')

        # ── Flow A: Correct backorder flow ──────────────────────────────
        # Step 1: Customer places order → stays in processing while we procure

        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-25',
            'items[0][product_id]': bo_prod_id,
            'items[0][quantity]': '3',
            'items[0][unit_price]': '800000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        bo_order = db_query(
            "SELECT id, total_amount FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (cust_id,))
        bo_order_id = bo_order['id']
        check(bo_order['total_amount'] == 2400000,
              f'Backorder order total = 2,400,000 (3×800k, got {bo_order["total_amount"]})')

        # Move to Processing — order is accepted but goods not yet here
        r = post(c, f'/orders/{bo_order_id}/status', {'status': 'processing'})
        has(r, 'Processing', 'Backorder order accepted (Processing)')

        # Step 2: We go source the goods — create inventory intake (goods arrived)
        r = post(c, '/inventory/', {
            'product_id': bo_prod_id,
            'quantity': '3',
            'cost_price': '520000',   # actual purchase price (may differ from sale_price default)
            'shipping_cost': '30000',  # cost to get goods to our warehouse
            'currency': 'VND',
            'intake_date': '2026-04-26',
            'notes': 'Sourced for backorder',
        })
        has(r, 'recorded', 'Backorder intake recorded after sourcing')
        bo_lot_id = db_query(
            "SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (bo_prod_id,))['id']
        bo_lot = db_query(f"SELECT * FROM inventory WHERE id={bo_lot_id}")
        check(bo_lot['remaining_quantity'] == 3, 'Goods arrived: 3 units in stock')

        # Step 3: Ship to customer — complete the order
        r = post(c, f'/orders/{bo_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Backorder order completed (shipped to customer)')

        # Verify FIFO correctly deducted the newly received lot
        bo_lot_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={bo_lot_id}")
        check(bo_lot_after['remaining_quantity'] == 0,
              f'All 3 sourced units consumed by order (remaining={bo_lot_after["remaining_quantity"]})')

        # Verify allocation records exist with correct COGS
        # effective cost = 520,000 + 30,000/3 = 530,000 per unit
        bo_alloc = db_query(
            "SELECT quantity_allocated, cost_price_at_sale FROM order_allocations WHERE order_id=? LIMIT 1",
            (bo_order_id,))
        check(bo_alloc is not None, 'FIFO allocation recorded for backorder order')
        check(bo_alloc['quantity_allocated'] == 3,
              f'3 units allocated from the sourced lot (got {bo_alloc["quantity_allocated"]})')
        expected_cogs_per_unit = 520000 + 30000 / 3   # = 530,000
        check(abs(bo_alloc['cost_price_at_sale'] - expected_cogs_per_unit) < 1,
              f'COGS = actual purchase cost + amortised shipping (expected {expected_cogs_per_unit:.0f}, '
              f'got {bo_alloc["cost_price_at_sale"]:.0f})')

        # ── Flow B: Emergency complete with zero stock (warns, no COGS) ──
        # Represents: seller completes the order before goods arrive (commits to delivery)

        r = post(c, '/products/', {
            'product_code': 'BO0002',
            'name': 'Zero Stock Item',
            'sale_price': '200000',
            'cost_price': '120000',
            'min_stock_level': '0',
        })
        zs_prod_id = db_query("SELECT id FROM products WHERE product_code='BO0002'")['id']

        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-25',
            'items[0][product_id]': zs_prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '200000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        zs_order = db_query(
            "SELECT id FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1", (cust_id,))
        zs_order_id = zs_order['id']

        # Complete immediately with no stock — app warns but allows
        r = post(c, f'/orders/{zs_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Zero-stock order completed (with warning)')
        has(r, 'warning', 'Low-stock warning shown when completing with no inventory')

        # Order is completed but has no allocation records (no stock to pull from)
        zs_alloc_count = db_count(
            "SELECT COUNT(*) FROM order_allocations WHERE order_id=?", (zs_order_id,))
        check(zs_alloc_count == 0,
              f'No allocation records when completing with zero stock (got {zs_alloc_count})')

        zs_order_db = db_query(f"SELECT order_status FROM orders WHERE id={zs_order_id}")
        check(zs_order_db['order_status'] == 'completed',
              'Order status = completed despite zero stock')

        # ── Flow C: Negative inventory as backorder marker ────────────────
        # "I've committed -N units to customers before receiving them"
        # App shows negative remaining_quantity highlighted amber in the UI

        r = post(c, '/products/', {
            'product_code': 'BO0003',
            'name': 'Pre-order Item',
            'sale_price': '1500000',
            'cost_price': '900000',
            'min_stock_level': '0',
        })
        pre_prod_id = db_query("SELECT id FROM products WHERE product_code='BO0003'")['id']

        # Create a NEGATIVE intake as a "committed backorder" marker
        r = post(c, '/inventory/', {
            'product_id': pre_prod_id,
            'quantity': '-5',
            'cost_price': '900000',
            'shipping_cost': '0',
            'currency': 'VND',
            'intake_date': '2026-04-20',
            'notes': 'Pre-committed to 5 customer orders',
        })
        has(r, 'recorded', 'Negative inventory entry recorded (backorder commitment)')
        neg_lot_id = db_query(
            "SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (pre_prod_id,))['id']
        neg_lot = db_query(f"SELECT quantity, remaining_quantity FROM inventory WHERE id={neg_lot_id}")
        check(neg_lot['quantity'] == -5, f'Negative lot quantity = -5 (got {neg_lot["quantity"]})')
        check(neg_lot['remaining_quantity'] == -5,
              f'Negative remaining_quantity = -5 (shows as backorder in UI)')

        # Goods arrive from supplier — create positive intake
        r = post(c, '/inventory/', {
            'product_id': pre_prod_id,
            'quantity': '5',
            'cost_price': '920000',   # actual price paid to supplier
            'shipping_cost': '50000',
            'currency': 'VND',
            'intake_date': '2026-04-26',
            'notes': 'Goods received from supplier',
        })
        has(r, 'recorded', 'Positive intake recorded when goods arrive from supplier')
        pos_lot_id = db_query(
            "SELECT id FROM inventory WHERE product_id=? AND quantity > 0 ORDER BY id DESC LIMIT 1",
            (pre_prod_id,))['id']
        pos_lot = db_query(f"SELECT quantity, remaining_quantity FROM inventory WHERE id={pos_lot_id}")
        check(pos_lot['remaining_quantity'] == 5, 'Positive lot: 5 units available for FIFO')
        check(neg_lot_id != pos_lot_id, 'Negative and positive lots are separate entries')

        # Customer order for 5 pre-order items → now we have stock to fulfill it
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-26',
            'items[0][product_id]': pre_prod_id,
            'items[0][quantity]': '5',
            'items[0][unit_price]': '1500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        pre_order = db_query(
            "SELECT id, total_amount FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (cust_id,))
        pre_order_id = pre_order['id']
        check(pre_order['total_amount'] == 7500000,
              f'Pre-order total = 7,500,000 (5×1.5M, got {pre_order["total_amount"]})')

        r = post(c, f'/orders/{pre_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Pre-order completed using received stock')

        # FIFO must use the POSITIVE lot (negative lot has remaining_quantity=-5, skipped by FIFO)
        pos_lot_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={pos_lot_id}")
        neg_lot_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={neg_lot_id}")
        check(pos_lot_after['remaining_quantity'] == 0,
              f'FIFO consumed all 5 from positive lot (remaining={pos_lot_after["remaining_quantity"]})')
        check(neg_lot_after['remaining_quantity'] == -5,
              f'Negative marker lot untouched by FIFO (remaining={neg_lot_after["remaining_quantity"]})')

        # COGS uses actual supplier cost from positive lot
        # effective = 920,000 + 50,000/5 = 930,000 per unit
        pre_alloc = db_query(
            "SELECT cost_price_at_sale FROM order_allocations WHERE order_id=? LIMIT 1",
            (pre_order_id,))
        expected_pre_cogs = 920000 + 50000 / 5   # = 930,000
        pre_cogs_got = f'{pre_alloc["cost_price_at_sale"]:.0f}' if pre_alloc else 'N/A'
        check(pre_alloc and abs(pre_alloc['cost_price_at_sale'] - expected_pre_cogs) < 1,
              f'Pre-order COGS = actual supplier cost (expected {expected_pre_cogs:.0f}, got {pre_cogs_got})')

        # ── 14. Seller-Pays-Shipping Order ────────────────────────────────
        section('Seller-Pays-Shipping Order')

        # Create Accessories category + USB Adapter product
        r = post(c, '/categories/', {'name': 'Accessories', 'description': 'Accessories'})
        acc_cat_id = db_query("SELECT id FROM categories WHERE name='Accessories'")['id']

        r = post(c, '/products/', {
            'product_code': 'AC0001',
            'name': 'USB Adapter',
            'category_id': acc_cat_id,
            'sale_price': '500000',
            'cost_price': '300000',
            'min_stock_level': '0',
        })
        usb_prod_id = db_query("SELECT id FROM products WHERE product_code='AC0001'")['id']
        check(usb_prod_id is not None, 'USB Adapter product created')

        # Intake 10 units at 300,000 VND each, 0 intake shipping cost
        r = post(c, '/inventory/', {
            'product_id': usb_prod_id,
            'quantity': '10',
            'cost_price': '300000',
            'shipping_cost': '0',
            'currency': 'VND',
            'intake_date': '2026-04-01',
        })
        usb_lot_id = db_query(
            "SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1", (usb_prod_id,))['id']

        # Order: 2 USB Adapters × 500,000 + 100,000 SELLER-PAID shipping
        # Customer owes 2×500,000 = 1,000,000 — shipping is OUR cost, not added to order total
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-22',
            'items[0][product_id]': usb_prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '100000',
            'shipping_paid_by': 'seller',
        })
        seller_order = db_query(
            "SELECT id, total_amount, shipping_paid_by FROM orders ORDER BY id DESC LIMIT 1")
        seller_order_id = seller_order['id']
        check(seller_order['total_amount'] == 1000000,
              f'Seller-shipping order: total excludes shipping (got {seller_order["total_amount"]})')
        check(seller_order['shipping_paid_by'] == 'seller',
              'shipping_paid_by = seller in DB')

        r = post(c, f'/orders/{seller_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Seller-shipping order completed')

        usb_lot_after = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={usb_lot_id}")
        check(usb_lot_after['remaining_quantity'] == 8,
              f'FIFO deducted 2 USB Adapters (remaining={usb_lot_after["remaining_quantity"]})')

        # Verify seller_shipping appears in dashboard costs (100,000 is a seller cost)
        seller_alloc = db_query(
            "SELECT cost_price_at_sale FROM order_allocations WHERE order_id=? LIMIT 1",
            (seller_order_id,))
        # USB Adapter: cost=300,000, intake shipping=0, so cost_price_at_sale=300,000
        check(seller_alloc and abs(seller_alloc['cost_price_at_sale'] - 300000) < 1,
              f'USB Adapter COGS = 300,000 per unit (got {seller_alloc["cost_price_at_sale"] if seller_alloc else "N/A"})')

        # ── 14. Return + Linked Refund (Less Shipping) ────────────────────
        section('Return + Linked Refund (Shipping Non-Refundable)')

        # New laptop order: 1 unit × 14,500,000 + 50,000 customer shipping = 14,550,000
        # Earlier returns created separate returned-goods lots; original FIFO lots stay unchanged.
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-23',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '1',
            'items[0][unit_price]': '14500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '50000',
            'shipping_paid_by': 'customer',
        })
        return_order = db_query(
            "SELECT id, total_amount FROM orders ORDER BY id DESC LIMIT 1")
        return_order_id = return_order['id']
        check(return_order['total_amount'] == 14550000,
              f'Return-scenario order total = 14,550,000 (got {return_order["total_amount"]})')

        r = post(c, f'/orders/{return_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Return-scenario order completed')

        # FIFO uses lot1 (1 remaining) → lot1 becomes 0
        lot1_after14 = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot1_after14['remaining_quantity'] == 0,
              f'lot1 consumed to 0 by new order (remaining={lot1_after14["remaining_quantity"]})')

        # Create return for 1 unit as a returned-goods intake
        oi_return = db_query("SELECT id FROM order_items WHERE order_id=?", (return_order_id,))
        r = post(c, '/returns/', {
            'order_id': return_order_id,
            'return_reason': 'Customer changed mind',
            'restore_inventory': 'on',
            'items[0][order_item_id]': oi_return['id'],
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '14500000',
            'items[0][restock_unit_cost]': '0',
            'items[0][restock_shipping_cost]': '0',
        })
        has(r, 'TR000002', 'Return TR000002 created')
        ret14_id = db_query("SELECT id FROM returns WHERE return_code='TR000002'")['id']

        # Original FIFO lots are not restored; a new returned-goods intake lot is created.
        lot1_after_ret14 = db_query(f"SELECT remaining_quantity FROM inventory WHERE id={lot_id}")
        check(lot1_after_ret14['remaining_quantity'] == 0,
              f'Original lot stays unchanged after return (remaining={lot1_after_ret14["remaining_quantity"]})')
        ret14_lot = db_query("""
            SELECT i.quantity, i.remaining_quantity, i.cost_price, ri.restock_quantity
            FROM return_items ri
            JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.return_id = ?
        """, (ret14_id,))
        check(ret14_lot is not None, 'Second return created a returned-goods intake lot')
        check(ret14_lot['quantity'] == 1, f'Second returned-goods lot quantity is 1 (got {ret14_lot["quantity"]})')
        check(ret14_lot['remaining_quantity'] == 1,
              f'Second returned-goods lot has 1 unit available (remaining={ret14_lot["remaining_quantity"]})')
        check(ret14_lot['cost_price'] == 0, f'Second returned-goods lot defaults to zero cost (got {ret14_lot["cost_price"]})')
        check(ret14_lot['restock_quantity'] == 1, 'Second return item records restock quantity')

        # Create refund LINKED to the return.
        # Shipping 50,000 is non-refundable. Refund = 14,500,000 (item only), not 14,550,000.
        r = post(c, '/refunds/', {
            'order_id': return_order_id,
            'return_id': ret14_id,
            'amount': '14500000',
            'refund_method': 'bank_transfer',
            'reason': 'returned_item',
            'notes': 'Shipping non-refundable per policy',
        })
        linked_refund = db_query(
            "SELECT id, amount, return_id FROM refunds ORDER BY id DESC LIMIT 1")
        check(linked_refund is not None, 'Linked refund created')
        check(linked_refund['amount'] == 14500000,
              f'Refund amount = 14,500,000 (not full 14,550,000 — shipping kept, got {linked_refund["amount"]})')
        check(linked_refund['return_id'] == ret14_id,
              f'Refund is linked to return TR000002 (return_id={linked_refund["return_id"]})')
        check(linked_refund['amount'] < return_order['total_amount'],
              'Refund < order total (50,000 shipping retained by seller)')

        linked_refund_id = linked_refund['id']
        r = c.get(f'/refunds/{linked_refund_id}')
        has(r, 'TR000002', 'Refund detail page shows linked return code')

        # ── 15. Customer Debt — Who Owes Money ────────────────────────────
        section('Customer Debt — Who Owes Money')

        # Create a new customer "Hoang Thi D"
        r = post(c, '/customers/', {
            'name': 'Hoang Thi D',
            'email': 'hoangtd@example.com',
            'phone': '0933333333',
            'region': 'HN',
        })
        debtor_id = db_query("SELECT id FROM customers WHERE name='Hoang Thi D'")['id']
        check(debtor_id is not None, 'Debtor customer created')

        # Order for Hoang Thi D: 1 USB Adapter × 500,000 = 500,000 total
        r = post(c, '/orders/', {
            'customer_id': debtor_id,
            'order_date': '2026-04-24',
            'items[0][product_id]': usb_prod_id,
            'items[0][quantity]': '1',
            'items[0][unit_price]': '500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        debt_order = db_query(
            "SELECT id, total_amount FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1",
            (debtor_id,))
        debt_order_id = debt_order['id']
        check(debt_order['total_amount'] == 500000,
              f'Debt order total = 500,000 (got {debt_order["total_amount"]})')

        # Complete the order — customer now owes 500,000
        r = post(c, f'/orders/{debt_order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Debt order completed')

        # Add partial payment of 200,000 — leaves 300,000 outstanding
        r = post(c, f'/orders/{debt_order_id}/payment', {
            'amount': '200000',
            'payment_method': 'cash',
            'notes': 'Partial',
        })
        debt_order_status = db_query(f"SELECT payment_status FROM orders WHERE id={debt_order_id}")
        check(debt_order_status['payment_status'] == 'partially_paid',
              'Debt order is partially_paid after 200,000 payment')

        # Verify live debt computation matches expected (500,000 - 200,000 = 300,000)
        expected_debt = 300000
        live_debt = db_query('''
            SELECT
                COALESCE((SELECT SUM(o.total_amount) FROM orders o
                          WHERE o.customer_id=? AND o.order_status IN ('processing', 'completed')), 0) -
                COALESCE((SELECT SUM(p.amount) FROM payments p
                          JOIN orders o ON p.order_id=o.id
                          WHERE o.customer_id=? AND o.order_status IN ('processing', 'completed')), 0)
            AS debt
        ''', (debtor_id, debtor_id))
        check(live_debt and abs(live_debt['debt'] - expected_debt) < 0.01,
              f'Outstanding debt computed correctly (expected 300,000, got {live_debt["debt"] if live_debt else "N/A"})')

        # Customer detail page shows live outstanding debt
        r = c.get(f'/customers/{debtor_id}')
        status(r, 200, 'Debtor customer detail loads')
        has(r, 'Outstanding Debt', 'Customer detail shows Outstanding Debt label')
        # The debt 300,000 must appear somewhere formatted on the page
        body = r.data.decode('utf-8', errors='ignore')
        check('300' in body and '000' in body,
              'Debt amount 300,000 appears on customer detail page')

        # Customer list shows Hoang Thi D with positive outstanding debt
        r = c.get('/customers/')
        status(r, 200, 'Customer list loads')
        has(r, 'Hoang Thi D', 'Debtor customer appears in list')

        # Verify a fully-paid customer shows 0 or negligible debt
        # Create a fully-paid customer and their order
        r = post(c, '/customers/', {
            'name': 'Nguyen Thi E',
            'email': 'nguyente@example.com',
            'phone': '0944444444',
            'region': 'HCM',
        })
        paid_cust_id = db_query("SELECT id FROM customers WHERE name='Nguyen Thi E'")['id']
        r = post(c, '/orders/', {
            'customer_id': paid_cust_id,
            'order_date': '2026-04-24',
            'items[0][product_id]': usb_prod_id,
            'items[0][quantity]': '1',
            'items[0][unit_price]': '500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
        })
        paid_order = db_query(
            "SELECT id FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 1", (paid_cust_id,))
        paid_order_id = paid_order['id']
        r = post(c, f'/orders/{paid_order_id}/status', {'status': 'completed'})
        r = post(c, f'/orders/{paid_order_id}/payment', {
            'amount': '500000', 'payment_method': 'cash', 'notes': 'Full payment',
        })
        paid_status = db_query(f"SELECT payment_status FROM orders WHERE id={paid_order_id}")
        check(paid_status['payment_status'] == 'fully_paid',
              'Fully-paid order shows fully_paid status')
        paid_debt = db_query('''
            SELECT
                COALESCE((SELECT SUM(o.total_amount) FROM orders o
                          WHERE o.customer_id=? AND o.order_status IN ('processing', 'completed')), 0) -
                COALESCE((SELECT SUM(p.amount) FROM payments p
                          JOIN orders o ON p.order_id=o.id
                          WHERE o.customer_id=? AND o.order_status IN ('processing', 'completed')), 0)
            AS debt
        ''', (paid_cust_id, paid_cust_id))
        check(paid_debt and abs(paid_debt['debt']) < 0.01,
              f'Fully-paid customer has zero outstanding debt (got {paid_debt["debt"] if paid_debt else "N/A"})')

        # ── 16. Dashboard — Exact Financial Calculations ──────────────────
        section('Dashboard — Exact Financial Calculations')

        from routes.dashboard import get_revenue_and_profit_data, get_period_date_range

        # Query expected values independently from DB (same formulas as dashboard)
        exp_gross = db_query("""
            SELECT COALESCE(SUM(total_amount), 0) AS v
            FROM orders WHERE order_status IN ('processing', 'completed')
        """)['v'] or 0.0

        exp_refunds = db_query("""
            SELECT COALESCE(SUM(r.amount), 0) AS v
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
        """)['v'] or 0.0

        exp_cogs = db_query("""
            SELECT COALESCE(SUM(oa.quantity_allocated * oa.cost_price_at_sale), 0) AS v
            FROM order_allocations oa
            JOIN orders o ON oa.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
        """)['v'] or 0.0

        exp_seller_ship = db_query("""
            SELECT COALESCE(SUM(shipping_fee), 0) AS v
            FROM orders
            WHERE order_status IN ('processing', 'completed') AND shipping_paid_by = 'seller'
        """)['v'] or 0.0

        exp_net = exp_gross - exp_refunds
        exp_profit = exp_net - exp_cogs - exp_seller_ship
        exp_margin = (exp_profit / exp_net * 100) if exp_net > 0 else 0.0

        # Basic sanity checks on the DB values before comparing with dashboard
        check(exp_gross > 0, f'Gross revenue > 0 (got {exp_gross:,.0f})')
        check(exp_refunds >= 0, f'Total refunds >= 0 (got {exp_refunds:,.0f})')
        check(exp_cogs > 0, f'COGS > 0 (got {exp_cogs:,.0f})')
        check(exp_seller_ship == 100000,
              f'Seller shipping = 100,000 (from HD000003, got {exp_seller_ship:,.0f})')

        # Call dashboard function directly (outside HTTP — no Flask context needed)
        _db = get_db()
        try:
            start_str, end_str = get_period_date_range('all')
            dash = get_revenue_and_profit_data(_db, start_str, end_str)
        finally:
            _db.close()

        # Dashboard function must agree with independent DB queries
        check(abs(dash['gross_revenue'] - exp_gross) < 0.01,
              f'Dashboard gross_revenue matches DB (expected {exp_gross:,.0f}, got {dash["gross_revenue"]:,.0f})')
        check(abs(dash['total_refunds'] - exp_refunds) < 0.01,
              f'Dashboard total_refunds matches DB (expected {exp_refunds:,.0f})')
        check(abs(dash['total_revenue'] - exp_net) < 0.01,
              f'Dashboard net_revenue matches DB (expected {exp_net:,.0f})')
        check(abs(dash['total_cogs'] - exp_cogs) < 0.01,
              f'Dashboard COGS matches DB (expected {exp_cogs:,.0f})')
        check(abs(dash['seller_shipping'] - exp_seller_ship) < 0.01,
              f'Dashboard seller_shipping matches DB (expected {exp_seller_ship:,.0f})')
        check(abs(dash['gross_profit'] - exp_profit) < 0.01,
              f'Dashboard gross_profit = net_revenue - COGS - seller_shipping (expected {exp_profit:,.0f})')

        # Accounting identities must hold
        check(abs(exp_net - (exp_gross - exp_refunds)) < 0.01,
              'Net Revenue = Gross Revenue - Refunds')
        check(abs(exp_profit - (exp_net - exp_cogs - exp_seller_ship)) < 0.01,
              'Gross Profit = Net Revenue - COGS - Seller Shipping')
        check(exp_profit < exp_net,
              'Gross Profit < Net Revenue (costs are positive)')

        # Margin formula
        if exp_net > 0:
            expected_margin = exp_profit / exp_net * 100
            check(abs(dash['profit_margin'] - expected_margin) < 0.01,
                  f'Profit margin formula correct ({expected_margin:.2f}%)')

        # Verify seller-shipping order specifically: HD000003 total excludes the 100k shipping
        # (confirmed earlier), so gross_revenue only counts the 1,000,000 sale — not 1,100,000.
        # This is implicitly verified since exp_gross matches dash['gross_revenue'].

        # Dashboard HTML also shows the numbers
        r = c.get('/dashboard/?period=all')
        status(r, 200, 'Dashboard loads with period=all')
        has(r, '₫', 'Dashboard shows VND amounts')

        # ── 17. Dashboard Metrics (smoke) ──────────────────────────────────
        section('Dashboard — Revenue & COGS')

        r = c.get('/dashboard/?period=all')
        status(r, 200, 'Dashboard loads with period=all')
        # Both orders are completed; verify page contains financial data
        has(r, '₫', 'Dashboard shows VND amounts')

        # ── 14b. Dashboard Improvements ────────────────────────────────────
        section('Dashboard — New Widgets')

        r = c.get('/dashboard/?period=all')
        status(r, 200, 'Dashboard loads for widget checks')
        # Outstanding collections section must appear (some orders have unpaid balance)
        has(r, 'Outstanding', 'Dashboard shows outstanding collections widget')
        # Top products section must appear
        has(r, 'Top Product', 'Dashboard shows top products section')
        # Payment status must appear in recent orders table (template renders as "Not Paid")
        has(r, 'Not Paid', 'Dashboard recent orders includes payment status')

        # ── 14. Currency Toggle ────────────────────────────────────────────
        section('Currency Toggle')

        r = post(c, '/switch-currency', {})
        # After toggle from VND to USD, dashboard should show USD
        r = c.get('/dashboard/?period=all')
        has(r, '$', 'Dashboard shows USD after toggle')

        # Toggle back
        r = post(c, '/switch-currency', {})
        r = c.get('/dashboard/')
        has(r, '₫', 'Dashboard shows VND after toggling back')

        # ── 15. Reports ────────────────────────────────────────────────────
        section('Reports')

        r = c.get('/reports/')
        status(r, 200, 'Reports index loads')

        r = c.get('/reports/sales')
        status(r, 200, 'Sales report loads')

        r = c.get('/reports/inventory')
        status(r, 200, 'Inventory report loads')
        has(r, 'Test Laptop Pro', 'Product appears in inventory report')

        r = c.get('/reports/profit')
        status(r, 200, 'Profit report loads')

        r = c.get('/reports/debt-aging')
        status(r, 200, 'Debt aging report loads')
        # There should be outstanding balances from test orders above
        has(r, 'Outstanding', 'Debt aging page has expected content')

        # Mobile table/card markup should be present anywhere a data table can render.
        section('Mobile Responsive Table Markup')

        for url, label in [
            ('/dashboard/?period=all', 'Dashboard'),
            ('/products/', 'Products list'),
            ('/customers/', 'Customers list'),
            ('/inventory/', 'Inventory list'),
            ('/orders/', 'Orders list'),
            ('/reports/sales', 'Sales report'),
            ('/reports/inventory', 'Inventory report'),
            ('/reports/profit', 'Profit report'),
            ('/reports/debt-aging', 'Debt aging report'),
        ]:
            r = c.get(url)
            status(r, 200, f'{label} loads for mobile markup check')
            has(r, 'mobile-card-table', f'{label} uses mobile card table markup')

        missing_mobile_tables = []
        templates_dir = os.path.join(APP_DIR, 'templates')
        for root, _, files in os.walk(templates_dir):
            for filename in files:
                if not filename.endswith('.html'):
                    continue
                path = os.path.join(root, filename)
                with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                    for line_no, line in enumerate(fh, 1):
                        if '<table class="table' in line and 'mobile-card-table' not in line:
                            rel = os.path.relpath(path, APP_DIR)
                            missing_mobile_tables.append(f'{rel}:{line_no}')
        check(not missing_mobile_tables, 'All app tables opt into mobile card layout',
              ', '.join(missing_mobile_tables[:10]))

        with open(os.path.join(APP_DIR, 'templates', 'base.html'), 'r', encoding='utf-8', errors='ignore') as fh:
            base_html = fh.read()
        check('applyMobileTableLabels' in base_html,
              'Base template auto-fills mobile table labels from table headers')

        section('Mobile Responsive Modal Markup')

        for url, label in [
            ('/products/new', 'Product modal'),
            ('/customers/new', 'Customer modal'),
            ('/categories/new', 'Category modal'),
            ('/inventory/new', 'Inventory intake modal'),
        ]:
            r = c.get(url)
            status(r, 200, f'{label} loads for mobile markup check')
            has(r, 'modal-body', f'{label} has scrollable modal body')
            has(r, 'modal-footer', f'{label} has fixed modal actions')

        modal_partials = [
            'category_form.html',
            'customer_form.html',
            'intake_form.html',
            'product_form.html',
        ]
        for filename in modal_partials:
            path = os.path.join(APP_DIR, 'templates', 'partials', filename)
            with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                html = fh.read()
            check('modal-body' in html and 'modal-footer' in html,
                  f'{filename} keeps form fields inside mobile modal layout')

        with open(os.path.join(APP_DIR, 'static', 'style.css'), 'r', encoding='utf-8', errors='ignore') as fh:
            css = fh.read()
        check('max-height: calc(100vh - 1rem)' in css and '.modal-code-row' in css,
              'Mobile modal CSS constrains viewport height and stacks code rows')

        # ── 15b. Export Endpoints ──────────────────────────────────────────
        section('Export Endpoints')

        r = c.get('/exports/')
        status(r, 200, 'Export index loads')

        r = c.get('/exports/products.csv')
        status(r, 200, 'Products CSV export returns 200')
        check('text/csv' in r.content_type, 'Products CSV has correct content type')
        check(len(r.data) > 0, 'Products CSV has content')

        r = c.get('/exports/customers.csv')
        status(r, 200, 'Customers CSV export returns 200')
        check(len(r.data) > 0, 'Customers CSV has content')

        r = c.get('/exports/orders.csv')
        status(r, 200, 'Orders CSV export returns 200')
        check(len(r.data) > 0, 'Orders CSV has content')

        r = c.get('/exports/inventory.csv')
        status(r, 200, 'Inventory CSV export returns 200')
        check(len(r.data) > 0, 'Inventory CSV has content')

        r = c.get('/exports/products.xlsx')
        status(r, 200, 'Products XLSX export returns 200')
        check('spreadsheet' in r.content_type or 'openxml' in r.content_type,
              'Products XLSX has spreadsheet content type')
        check(len(r.data) > 0, 'Products XLSX has content')

        r = c.get('/exports/all.xlsx')
        status(r, 200, 'Export-all XLSX returns 200')
        check(len(r.data) > 0, 'Export-all XLSX has content')

        # ── 15c. Return Quantity Validation ────────────────────────────────
        section('Return Quantity Validation')

        # Get the first completed order item id and product to test over-return
        oi_row = db_query(
            "SELECT oi.id, oi.quantity, o.id as order_id "
            "FROM order_items oi JOIN orders o ON oi.order_id = o.id "
            "WHERE o.order_status = 'completed' LIMIT 1"
        )
        if oi_row:
            too_many = oi_row['quantity'] + 999
            # Returns route requires product_id and refund_amount fields to populate items list
            oi_product = db_query(
                f"SELECT product_id FROM order_items WHERE id={oi_row['id']}"
            )
            r = post(c, '/returns/', {
                'order_id': str(oi_row['order_id']),
                'return_reason': 'Test over-return',
                'items[0][order_item_id]': str(oi_row['id']),
                'items[0][product_id]': str(oi_product['product_id']),
                'items[0][quantity]': str(too_many),
                'items[0][refund_amount]': '1',
            })
            has(r, 'exceeds the ordered quantity', 'Returning more than ordered is rejected')
        else:
            check(False, 'No completed order found for return validation test')

        # ── 16. CSV Import ─────────────────────────────────────────────────
        section('Return After-the-Fact and Partial Restock')

        r = post(c, '/products/', {
            'product_code': 'EL7777',
            'name': 'After Restock Widget',
            'category_id': cat_id,
            'sale_price': '200000',
            'cost_price': '100000',
            'min_stock_level': '1',
            'barcode': 'RESTOCK7777',
        })
        has(r, 'After Restock Widget', 'After-the-fact restock product created')
        restock_prod_id = db_query("SELECT id FROM products WHERE product_code='EL7777'")['id']

        r = post(c, '/inventory/', {
            'product_id': restock_prod_id,
            'quantity': '2',
            'cost_price': '100000',
            'shipping_cost': '0',
            'currency': 'VND',
            'intake_date': '2026-05-01',
            'notes': 'After-the-fact restock source lot',
        })
        has(r, 'recorded', 'After-the-fact restock source inventory created')

        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-05-02',
            'items[0][product_id]': restock_prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '200000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
            'notes': 'After-the-fact restock sale',
        })
        has(r, 'HD', 'After-the-fact restock order created')
        restock_order = db_query(
            "SELECT id FROM orders WHERE notes='After-the-fact restock sale' ORDER BY id DESC LIMIT 1"
        )
        r = post(c, f'/orders/{restock_order["id"]}/status', {'status': 'completed'})
        has(r, 'Completed', 'After-the-fact restock order completed')

        restock_order_item = db_query(
            "SELECT id FROM order_items WHERE order_id=? AND product_id=?",
            (restock_order['id'], restock_prod_id)
        )
        r = post(c, '/returns/', {
            'order_id': str(restock_order['id']),
            'return_reason': 'Partial restock quantity test',
            'items[0][order_item_id]': str(restock_order_item['id']),
            'items[0][product_id]': str(restock_prod_id),
            'items[0][quantity]': '2',
            'items[0][refund_amount]': '400000',
            'items[0][restock]': 'on',
            'items[0][restock_quantity]': '1',
            'items[0][restock_unit_cost]': '25000',
            'items[0][restock_shipping_cost]': '5000',
        })
        has(r, 'created successfully', 'Return can restock only part of the returned quantity')
        partial_return = db_query(
            "SELECT id, return_code FROM returns WHERE return_reason='Partial restock quantity test' ORDER BY id DESC LIMIT 1"
        )
        partial_lot = db_query('''
            SELECT i.quantity, i.remaining_quantity, i.cost_price, i.shipping_cost,
                   ri.quantity AS return_quantity, ri.restock_quantity, ri.restock_action
            FROM return_items ri
            JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.return_id = ?
        ''', (partial_return['id'],))
        check(partial_lot is not None, 'Partial restock created a linked inventory lot')
        check(partial_lot['return_quantity'] == 2, 'Return records total returned quantity of 2')
        check(partial_lot['restock_quantity'] == 1, 'Return records restocked quantity of 1')
        check(partial_lot['quantity'] == 1 and partial_lot['remaining_quantity'] == 1,
              'Inventory lot only adds the restocked quantity back to stock')
        check(partial_lot['cost_price'] == 25000, 'Partial restock lot stores unit restock cost')
        check(partial_lot['shipping_cost'] == 5000, 'Partial restock lot stores restock/refurb cost')

        r = post(c, '/returns/', {
            'order_id': str(restock_order['id']),
            'return_reason': 'Invalid over-restock test',
            'items[0][order_item_id]': str(restock_order_item['id']),
            'items[0][product_id]': str(restock_prod_id),
            'items[0][quantity]': '0',
            'items[0][refund_amount]': '0',
            'items[0][restock]': 'on',
            'items[0][restock_quantity]': '2',
        })
        has(r, 'At least one item is required', 'Zero return quantity is rejected before restock')

        r = post(c, '/products/', {
            'product_code': 'EL7778',
            'name': 'Missed Restock Widget',
            'category_id': cat_id,
            'sale_price': '300000',
            'cost_price': '120000',
            'min_stock_level': '1',
            'barcode': 'RESTOCK7778',
        })
        has(r, 'Missed Restock Widget', 'Missed-checkbox restock product created')
        missed_prod_id = db_query("SELECT id FROM products WHERE product_code='EL7778'")['id']
        r = post(c, '/inventory/', {
            'product_id': missed_prod_id,
            'quantity': '1',
            'cost_price': '120000',
            'shipping_cost': '0',
            'currency': 'VND',
            'intake_date': '2026-05-03',
            'notes': 'Missed restock source lot',
        })
        has(r, 'recorded', 'Missed-checkbox source inventory created')
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-05-03',
            'items[0][product_id]': missed_prod_id,
            'items[0][quantity]': '1',
            'items[0][unit_price]': '300000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
            'notes': 'Missed after-the-fact restock sale',
        })
        missed_order = db_query(
            "SELECT id FROM orders WHERE notes='Missed after-the-fact restock sale' ORDER BY id DESC LIMIT 1"
        )
        r = post(c, f'/orders/{missed_order["id"]}/status', {'status': 'completed'})
        has(r, 'Completed', 'Missed-checkbox order completed')
        missed_order_item = db_query(
            "SELECT id FROM order_items WHERE order_id=? AND product_id=?",
            (missed_order['id'], missed_prod_id)
        )
        r = post(c, '/returns/', {
            'order_id': str(missed_order['id']),
            'return_reason': 'Forgot restock checkbox',
            'items[0][order_item_id]': str(missed_order_item['id']),
            'items[0][product_id]': str(missed_prod_id),
            'items[0][quantity]': '1',
            'items[0][refund_amount]': '300000',
        })
        has(r, 'created successfully', 'Return can be created without immediate restock')
        missed_return = db_query(
            "SELECT id, return_code FROM returns WHERE return_reason='Forgot restock checkbox' ORDER BY id DESC LIMIT 1"
        )
        missed_item = db_query(
            "SELECT id, restock_action, restock_quantity, restock_inventory_lot_id FROM return_items WHERE return_id=?",
            (missed_return['id'],)
        )
        check(missed_item['restock_action'] == 'none', 'Missed-checkbox return item starts with restock_action none')
        check(missed_item['restock_quantity'] == 0, 'Missed-checkbox return item starts with restock quantity 0')
        check(missed_item['restock_inventory_lot_id'] is None, 'Missed-checkbox return item has no inventory lot')

        r = post(c, '/refunds/', {
            'order_id': str(missed_order['id']),
            'return_id': str(missed_return['id']),
            'amount': '300000',
            'refund_method': 'bank_transfer',
            'reason': 'customer_request',
            'notes': 'Refund before after-the-fact restock',
        })
        has(r, 'created successfully', 'Refund linked to missed-checkbox return created')

        from routes.dashboard import get_revenue_and_profit_data, get_period_date_range
        _db = get_db()
        try:
            start_str, end_str = get_period_date_range('all')
            before_after_fact_restock = get_revenue_and_profit_data(_db, start_str, end_str)
        finally:
            _db.close()

        r = c.get(f'/returns/{missed_return["id"]}')
        has(r, 'Restock Selected Items', 'Return detail exposes after-the-fact restock button')
        has(r, 'Not restocked', 'Return detail clearly marks missed-checkbox item as not restocked')
        has(r, 'Returned Item Value / Unit', 'After-the-fact restock uses returned item value wording')
        has(r, 'Extra Restock Cost', 'After-the-fact restock uses extra restock cost wording')
        has(r, 'Most returns should keep optional resale cost at 0', 'After-the-fact restock explains optional cost default')
        hasnt(r, 'Unit Restock Cost', 'After-the-fact restock no longer uses old unit restock label')
        hasnt(r, 'Restock/Refurb Cost', 'After-the-fact restock no longer uses old refurb label')

        r = post(c, f'/returns/{missed_return["id"]}/restock', {
            'return_item_id': str(missed_item['id']),
            f'restock_quantity_{missed_item["id"]}': '1',
            f'restock_unit_cost_{missed_item["id"]}': '50000',
            f'restock_shipping_cost_{missed_item["id"]}': '10000',
        })
        has(r, 'Restocked 1 return item', 'After-the-fact restock succeeds')
        restocked_lot = db_query('''
            SELECT i.quantity, i.remaining_quantity, i.cost_price, i.shipping_cost,
                   ri.restock_action, ri.restock_quantity, ri.restock_inventory_lot_id
            FROM return_items ri
            JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.id = ?
        ''', (missed_item['id'],))
        check(restocked_lot is not None, 'After-the-fact restock creates linked inventory lot')
        check(restocked_lot['quantity'] == 1 and restocked_lot['remaining_quantity'] == 1,
              'After-the-fact returned-goods lot has returned quantity available')
        check(restocked_lot['restock_quantity'] == 1, 'After-the-fact return item records restock quantity')
        check(restocked_lot['cost_price'] == 50000, 'After-the-fact returned-goods lot stores unit restock cost')
        check(restocked_lot['shipping_cost'] == 10000, 'After-the-fact returned-goods lot stores restock/refurb cost')
        check(restocked_lot['restock_action'] == 'return_intake', 'Return item records return_intake after restock')

        _db = get_db()
        try:
            start_str, end_str = get_period_date_range('all')
            after_after_fact_restock = get_revenue_and_profit_data(_db, start_str, end_str)
        finally:
            _db.close()
        for key in ('gross_revenue', 'total_refunds', 'total_revenue', 'total_cogs', 'seller_shipping', 'gross_profit'):
            check(
                abs(after_after_fact_restock[key] - before_after_fact_restock[key]) < 0.01,
                f'After-the-fact restock does not change dashboard {key}'
            )

        lot_count_before_duplicate = db_count(
            "SELECT COUNT(*) FROM inventory WHERE notes LIKE ?",
            (f'%{missed_return["return_code"]}%',)
        )
        r = post(c, f'/returns/{missed_return["id"]}/restock', {
            'return_item_id': str(missed_item['id']),
            f'restock_quantity_{missed_item["id"]}': '1',
            f'restock_unit_cost_{missed_item["id"]}': '1',
            f'restock_shipping_cost_{missed_item["id"]}': '1',
        })
        has(r, 'already have inventory lots', 'Duplicate after-the-fact restock is blocked')
        lot_count_after_duplicate = db_count(
            "SELECT COUNT(*) FROM inventory WHERE notes LIKE ?",
            (f'%{missed_return["return_code"]}%',)
        )
        check(lot_count_after_duplicate == lot_count_before_duplicate,
              'Duplicate after-the-fact restock does not create another inventory lot')

        before_writeoff = after_after_fact_restock
        returned_lot_id = restocked_lot['restock_inventory_lot_id']
        r = post(c, f'/inventory/{returned_lot_id}/write-off', {
            'return_item_id': str(missed_item['id']),
            'quantity': '1',
            'reason': 'damaged_unsellable',
            'notes': 'Found damaged after inspection',
        })
        has(r, 'Wrote off 1 unit', 'Returned-goods write-off succeeds from return detail lot')
        written_return_lot = db_query(
            "SELECT remaining_quantity FROM inventory WHERE id=?",
            (returned_lot_id,)
        )
        check(written_return_lot['remaining_quantity'] == 0, 'Returned-goods write-off reduces lot stock to zero')
        returned_adjustment = db_query('''
            SELECT adjustment_code, quantity_delta, unit_cost, total_cost, return_item_id, reason
            FROM inventory_adjustments
            WHERE inventory_lot_id = ?
            ORDER BY id DESC LIMIT 1
        ''', (returned_lot_id,))
        check(returned_adjustment is not None and returned_adjustment['adjustment_code'].startswith('AD'),
              'Returned-goods write-off creates AD adjustment code')
        check(returned_adjustment['quantity_delta'] == -1, 'Returned-goods write-off stores negative quantity delta')
        check(returned_adjustment['return_item_id'] == missed_item['id'], 'Returned-goods write-off links back to return item')
        check(abs(returned_adjustment['unit_cost'] - 60000) < 0.01,
              f'Returned-goods write-off unit cost includes restock cost + amortized refurb cost (got {returned_adjustment["unit_cost"]})')
        check(abs(returned_adjustment['total_cost'] - 60000) < 0.01,
              f'Returned-goods write-off total cost is exact (got {returned_adjustment["total_cost"]})')

        _db = get_db()
        try:
            start_str, end_str = get_period_date_range('all')
            after_writeoff = get_revenue_and_profit_data(_db, start_str, end_str)
        finally:
            _db.close()
        for key in ('gross_revenue', 'total_refunds', 'total_revenue', 'total_cogs', 'seller_shipping', 'gross_profit'):
            check(
                abs(after_writeoff[key] - before_writeoff[key]) < 0.01,
                f'Inventory write-off does not change dashboard {key}'
            )
        check(abs(after_writeoff['inventory_adjustments'] - before_writeoff['inventory_adjustments'] - 60000) < 0.01,
              'Inventory write-off increases separate adjustment cost')
        check(abs(after_writeoff['adjusted_profit'] - (before_writeoff['adjusted_profit'] - 60000)) < 0.01,
              'Inventory write-off reduces profit after adjustments')

        r = post(c, f'/inventory/{returned_lot_id}/write-off', {
            'return_item_id': str(missed_item['id']),
            'quantity': '1',
            'reason': 'damaged_unsellable',
        })
        has(r, 'no available stock', 'Cannot write off a returned-goods lot after all stock is gone')

        r = post(c, '/products/', {
            'product_code': 'EL7779',
            'name': 'General Writeoff Widget',
            'category_id': cat_id,
            'sale_price': '500000',
            'cost_price': '100000',
            'min_stock_level': '1',
            'barcode': 'WRITE7779',
        })
        has(r, 'General Writeoff Widget', 'General write-off product created')
        general_prod_id = db_query("SELECT id FROM products WHERE product_code='EL7779'")['id']
        r = post(c, '/inventory/', {
            'product_id': general_prod_id,
            'quantity': '3',
            'cost_price': '100000',
            'shipping_cost': '30000',
            'currency': 'VND',
            'intake_date': '2026-05-04',
            'notes': 'General write-off source lot',
        })
        has(r, 'recorded', 'General write-off source inventory created')
        general_lot_id = db_query(
            "SELECT id FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (general_prod_id,)
        )['id']
        r = post(c, f'/inventory/{general_lot_id}/write-off', {
            'quantity': '2',
            'reason': 'lost',
            'notes': 'Lost during warehouse count',
        })
        has(r, 'Wrote off 2 unit', 'General inventory write-off succeeds')
        general_lot = db_query(
            "SELECT remaining_quantity FROM inventory WHERE id=?",
            (general_lot_id,)
        )
        check(general_lot['remaining_quantity'] == 1, 'General write-off reduces available stock')
        general_adjustment = db_query('''
            SELECT quantity_delta, unit_cost, total_cost, return_item_id, reason
            FROM inventory_adjustments
            WHERE inventory_lot_id = ?
            ORDER BY id DESC LIMIT 1
        ''', (general_lot_id,))
        check(general_adjustment['quantity_delta'] == -2, 'General write-off stores negative quantity delta')
        check(general_adjustment['return_item_id'] is None, 'General write-off is not linked to a return item')
        check(abs(general_adjustment['unit_cost'] - 110000) < 0.01,
              f'General write-off unit cost includes amortized inbound shipping (got {general_adjustment["unit_cost"]})')
        check(abs(general_adjustment['total_cost'] - 220000) < 0.01,
              f'General write-off total cost is exact (got {general_adjustment["total_cost"]})')

        section('CSV Import')

        # Products CSV
        prod_csv = b'name,category,sale_price,cost_price,barcode,min_stock_level\nImported Laptop,Electronics,12000000,8000000,IMP001,3\n'
        r = post_file(c, '/import/products', 'file', prod_csv, 'products.csv')
        has(r, 'Successfully imported', 'Product CSV import succeeds')
        imp_prod = db_query("SELECT id FROM products WHERE name='Imported Laptop'")
        check(imp_prod is not None, 'Imported product exists in database')

        # Bad product CSV (no name)
        bad_csv = b'name,category,sale_price,cost_price\n,Electronics,1000,500\n'
        r = post_file(c, '/import/products', 'file', bad_csv, 'bad.csv')
        has(r, 'failed', 'Product with no name is reported as error')

        # Customers CSV
        cust_csv = b'name,email,phone,address,region\nImported Customer,imp@example.com,0909090909,456 Street,Hanoi\n'
        r = post_file(c, '/import/customers', 'file', cust_csv, 'customers.csv')
        has(r, 'Successfully imported', 'Customer CSV import succeeds')
        imp_cust = db_query("SELECT id FROM customers WHERE name='Imported Customer'")
        check(imp_cust is not None, 'Imported customer exists in database')

        # Bad email in customer CSV
        bad_cust_csv = b'name,email,phone,address,region\nBad Email Customer,notanemail,0909090909,789 Street,HCM\n'
        r = post_file(c, '/import/customers', 'file', bad_cust_csv, 'bad_cust.csv')
        has(r, 'Invalid email', 'Invalid email in CSV is reported as error')

        # Inventory CSV
        imp_prod_id = imp_prod['id']
        inv_csv = f'product_code,quantity,cost_price,intake_date,notes\nSP000001,20,8000000,2026-04-10,Import test\n'.encode()
        # Use imported product's code
        imp_code = db_query(f"SELECT product_code FROM products WHERE id={imp_prod_id}")['product_code']
        inv_csv2 = f'product_code,quantity,cost_price,intake_date,notes\n{imp_code},20,8000000,2026-04-10,Import test\n'.encode()
        r = post_file(c, '/import/inventory', 'file', inv_csv2, 'inventory.csv')
        has(r, 'Successfully imported', 'Inventory CSV import succeeds')

        # ── 17. Settings ───────────────────────────────────────────────────
        section('Settings')

        r = c.get('/settings/')
        status(r, 200, 'Settings page loads')

        # Update app name
        r = post(c, '/settings/', {
            'app_name': 'My Inventory v5',
            'default_currency': 'VND',
            'vnd_usd_rate': '25000',
        })
        has(r, 'successfully', 'Settings updated')
        s = db_query("SELECT app_name, vnd_usd_rate FROM settings LIMIT 1")
        check(s and s['app_name'] == 'My Inventory v5', 'App name persisted')
        check(s and s['vnd_usd_rate'] == 25000, 'Exchange rate persisted')

        # Change password to something new
        r = post(c, '/settings/password', {
            'old_password': 'admin123',
            'new_password': 'newpass456',
            'confirm_password': 'newpass456',
        })
        has(r, 'successfully', 'Password changed successfully')

        # Old password no longer works (clear session, try login)
        with c.session_transaction() as sess:
            sess.pop('authenticated', None)
        r = c.post('/login', data={'password': 'admin123'}, follow_redirects=True)
        hasnt(r, 'Dashboard', 'Old password rejected after change')

        # New password works
        r = c.post('/login', data={'password': 'newpass456'}, follow_redirects=True)
        has(r, 'Dashboard', 'New password accepted')

        # Restore CSRF for remaining tests
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF_TOKEN
            sess['authenticated'] = True

        # Wrong current password rejected
        r = post(c, '/settings/password', {
            'old_password': 'wrongpassword',
            'new_password': 'another456',
            'confirm_password': 'another456',
        })
        has(r, 'incorrect', 'Wrong current password rejected')

        # Password mismatch rejected
        r = post(c, '/settings/password', {
            'old_password': 'newpass456',
            'new_password': 'aaa',
            'confirm_password': 'bbb',
        })
        has(r, 'do not match', 'Password mismatch rejected')

        # Backup endpoint returns a file
        r = post(c, '/settings/backup/create', {})
        check(r.status_code == 200, 'Database backup download returns 200')
        check(len(r.data) > 0, 'Backup file has content')

        # ── 17b. Draft Order Delete ────────────────────────────────────────
        section('Draft Order Delete')

        draft_stock_before = db_query(
            "SELECT COALESCE(SUM(remaining_quantity), 0) AS v FROM inventory WHERE product_id=?",
            (prod_id,))['v']
        draft_revenue_before = db_query(
            "SELECT COALESCE(SUM(total_amount), 0) AS v FROM orders WHERE order_status IN ('processing', 'completed')")['v']
        draft_cogs_before = db_query(
            "SELECT COALESCE(SUM(quantity_allocated * cost_price_at_sale), 0) AS v FROM order_allocations")['v']

        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-05-10',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '1',
            'items[0][unit_price]': '14500000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '0',
            'shipping_paid_by': 'customer',
            'notes': 'draft delete test',
        })
        draft_order = db_query("SELECT id, order_status FROM orders WHERE notes='draft delete test'")
        check(draft_order and draft_order['order_status'] == 'draft', 'Draft delete test order starts as draft')
        draft_allocs = db_count("SELECT COUNT(*) FROM order_allocations WHERE order_id=?", (draft_order['id'],))
        check(draft_allocs == 0, 'Draft order creates no FIFO allocations')
        draft_stock_mid = db_query(
            "SELECT COALESCE(SUM(remaining_quantity), 0) AS v FROM inventory WHERE product_id=?",
            (prod_id,))['v']
        check(draft_stock_mid == draft_stock_before, 'Draft order does not affect stock before delete')

        r = post(c, f'/orders/{draft_order["id"]}/delete', {})
        has(r, 'deleted', 'Draft order can be deleted')
        deleted_draft = db_query("SELECT id FROM orders WHERE id=?", (draft_order['id'],))
        check(deleted_draft is None, 'Deleted draft order row is removed')
        draft_stock_after = db_query(
            "SELECT COALESCE(SUM(remaining_quantity), 0) AS v FROM inventory WHERE product_id=?",
            (prod_id,))['v']
        draft_revenue_after = db_query(
            "SELECT COALESCE(SUM(total_amount), 0) AS v FROM orders WHERE order_status IN ('processing', 'completed')")['v']
        draft_cogs_after = db_query(
            "SELECT COALESCE(SUM(quantity_allocated * cost_price_at_sale), 0) AS v FROM order_allocations")['v']
        check(draft_stock_after == draft_stock_before, 'Deleting draft order leaves stock unchanged')
        check(draft_revenue_after == draft_revenue_before, 'Deleting draft order leaves revenue unchanged')
        check(draft_cogs_after == draft_cogs_before, 'Deleting draft order leaves COGS unchanged')

        # ── 18. Product Delete ─────────────────────────────────────────────
        section('Product & Customer Soft-Delete / Delete')

        # Create a product to delete (one not used in any order)
        r = post(c, '/products/', {
            'product_code': 'EL9999',
            'name': 'Delete Me Product',
            'sale_price': '1000',
            'cost_price': '500',
            'min_stock_level': '0',
        })
        del_prod_id = db_query("SELECT id FROM products WHERE product_code='EL9999'")['id']
        r = post(c, f'/products/{del_prod_id}/delete', {})
        has(r, 'deleted', 'Product deleted')
        gone = db_query(f"SELECT id FROM products WHERE id={del_prod_id} AND is_active=1")
        check(gone is None, 'Deleted product no longer active')

        # Create a customer to delete
        r = post(c, '/customers/', {
            'customer_code': 'KH999999',
            'name': 'Delete Me Customer',
        })
        del_cust_id = db_query("SELECT id FROM customers WHERE customer_code='KH999999'")['id']
        r = post(c, f'/customers/{del_cust_id}/delete', {})
        has(r, 'deleted', 'Customer deleted')
        gone = db_query(f"SELECT id FROM customers WHERE id={del_cust_id} AND is_active=1")
        check(gone is None, 'Deleted customer no longer active')

        # ── 19. CSRF Validation ────────────────────────────────────────────
        section('CSRF Protection')

        # POST without token
        r = c.post('/categories/', data={'name': 'NoCsrf'}, follow_redirects=True)
        has(r, 'Invalid or expired request token', 'POST without CSRF token is blocked')

        # POST with wrong token
        r = c.post('/categories/', data={'name': 'BadCsrf', 'csrf_token': 'wrong-token'}, follow_redirects=True)
        has(r, 'Invalid or expired request token', 'POST with wrong CSRF token is blocked')

        # Correct token still works
        r = post(c, '/categories/', {'name': 'CsrfOkCat', 'description': ''})
        has(r, 'CsrfOkCat', 'POST with correct CSRF token succeeds')

        # ── 20. Logout ─────────────────────────────────────────────────────
        section('Logout')

        r = c.get('/logout', follow_redirects=True)
        has(r, 'Login', 'Logout redirects to login page')
        r = c.get('/dashboard/', follow_redirects=True)
        has(r, 'Login', 'Accessing dashboard after logout redirects to login')

    # ── Cleanup ────────────────────────────────────────────────────────────
    try:
        if os.path.exists(_TEST_DB):
            os.remove(_TEST_DB)
    except Exception:
        pass
    shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)

    # ── Results ────────────────────────────────────────────────────────────
    total = _PASS + _FAIL
    print(f'\n{"─" * 50}')
    print(f'\033[1mResults: {_PASS}/{total} passed\033[0m', end='')
    if _FAIL:
        print(f'  \033[91m({_FAIL} failed)\033[0m')
        print('\nFailed checks:')
        for name, detail in _FAILURES:
            print(f'  \033[91m✗\033[0m {name}')
            if detail:
                print(f'    {detail[:200]}')
    else:
        print(f'  \033[92m— all passed!\033[0m')
    print()
    return _FAIL == 0


if __name__ == '__main__':
    success = run()
    sys.exit(0 if success else 1)
