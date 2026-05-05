#!/usr/bin/env python3
"""
Real-world scenario tests for inventory_app_v5.

These tests use a deterministic "small Vietnamese reseller" ledger with exact
expected accounting numbers. They complement test_app.py by checking focused
business scenarios instead of one long smoke journey.

Run:
    python test_real_world_scenarios.py
"""
import csv
import io
import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

# Patch the DB before importing app.py, which initializes the schema at import.
import db as db_module

_TEST_DB = os.path.join(APP_DIR, f'_scenario_test_{os.getpid()}.db')
db_module.DATABASE = _TEST_DB

import app as flask_app_module

flask_app = flask_app_module.app
flask_app.config['TESTING'] = True
_UPLOAD_DIR = os.path.join(APP_DIR, 'static', 'uploads', 'products', f'_scenario_uploads_{os.getpid()}')
os.makedirs(_UPLOAD_DIR, exist_ok=True)
flask_app.config['UPLOAD_FOLDER'] = _UPLOAD_DIR

from db import get_db, init_db
from routes.dashboard import get_revenue_and_profit_data
from routes.reports import _calc_cogs as reports_calc_cogs

CSRF_TOKEN = 'scenario-csrf-token'
_PASS = 0
_FAIL = 0
_FAILURES = []


def section(title):
    print(f'\n-- {title} --')


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
        print(f'       {detail}')


def check(condition, name, detail=''):
    if condition:
        ok(name)
    else:
        fail(name, detail)
    return condition


def eq(actual, expected, name):
    return check(actual == expected, name, f'expected {expected!r}, got {actual!r}')


def money_eq(actual, expected, name):
    actual = round(float(actual or 0), 2)
    expected = round(float(expected or 0), 2)
    return check(abs(actual - expected) < 0.01, name, f'expected {expected:,.2f}, got {actual:,.2f}')


def status(resp, expected, name):
    return check(resp.status_code == expected, name, f'HTTP {resp.status_code} != {expected}')


def body_has(resp, text, name):
    body = resp.data.decode('utf-8', errors='ignore')
    return check(text in body, name, f'{text!r} not found in response')


def post(c, url, data=None, **kw):
    payload = dict(data or {})
    payload['csrf_token'] = CSRF_TOKEN
    return c.post(url, data=payload, follow_redirects=True, **kw)


def db_one(sql, params=()):
    db = get_db()
    try:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def db_all(sql, params=()):
    db = get_db()
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    finally:
        db.close()


def db_value(sql, params=()):
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


def login(c):
    r = c.post('/login', data={'password': 'admin123'}, follow_redirects=True)
    body_has(r, 'Dashboard', 'Login succeeds for scenario suite')
    with c.session_transaction() as sess:
        sess['authenticated'] = True
        sess['csrf_token'] = CSRF_TOKEN


def disable_auto_backup():
    exec_db('UPDATE settings SET auto_backup_enabled = 0 WHERE id = 1')


def create_category(c, name, description=''):
    r = post(c, '/categories/', {'name': name, 'description': description})
    status(r, 200, f'Category created: {name}')
    row = db_one('SELECT id, name FROM categories WHERE name = ?', (name,))
    check(row is not None, f'Category exists in DB: {name}')
    return row['id']


def create_customer(c, code, name, region='HCM'):
    r = post(c, '/customers/', {
        'customer_code': code,
        'name': name,
        'email': '',
        'phone': '0900000000',
        'address': '123 Test Street',
        'region': region,
    })
    status(r, 200, f'Customer created: {code}')
    row = db_one('SELECT id, customer_code FROM customers WHERE customer_code = ?', (code,))
    check(row is not None, f'Customer exists in DB: {code}')
    return row['id']


def create_product(c, code, name, category_id, sale_price, cost_price, min_stock=0):
    r = post(c, '/products/', {
        'product_code': code,
        'name': name,
        'category_id': str(category_id),
        'sale_price': str(sale_price),
        'cost_price': str(cost_price),
        'barcode': code,
        'min_stock_level': str(min_stock),
        'notes': '',
    })
    status(r, 200, f'Product created: {code}')
    row = db_one('SELECT id, product_code FROM products WHERE product_code = ?', (code,))
    check(row is not None, f'Product exists in DB: {code}')
    return row['id']


def create_intake(c, product_id, qty, cost, shipping, date, currency='VND', note=''):
    r = post(c, '/inventory/', {
        'product_id': str(product_id),
        'quantity': str(qty),
        'cost_price': str(cost),
        'shipping_cost': str(shipping),
        'currency': currency,
        'intake_date': date,
        'notes': note,
    })
    status(r, 200, f'Intake recorded for product {product_id}, qty {qty}')
    return db_one('SELECT * FROM inventory ORDER BY id DESC LIMIT 1')['id']


def create_order(c, customer_id, order_date, items, discount=0, shipping=0, shipping_paid_by='customer', notes=''):
    data = {
        'customer_id': str(customer_id),
        'order_date': order_date,
        'discount_amount': str(discount),
        'shipping_fee': str(shipping),
        'shipping_paid_by': shipping_paid_by,
        'notes': notes,
    }
    for idx, item in enumerate(items):
        data[f'items[{idx}][product_id]'] = str(item['product_id'])
        data[f'items[{idx}][quantity]'] = str(item['quantity'])
        data[f'items[{idx}][unit_price]'] = str(item['unit_price'])
        data[f'items[{idx}][discount_percent]'] = str(item.get('discount_percent', 0))

    r = post(c, '/orders/', data)
    status(r, 200, 'Order created')
    order = db_one('SELECT * FROM orders ORDER BY id DESC LIMIT 1')
    check(order is not None, 'Order exists in DB after create')
    return order['id']


def complete_order(c, order_id):
    r = post(c, f'/orders/{order_id}/status', {'status': 'completed'})
    status(r, 200, f'Order {order_id} completed')
    return r


def add_payment(c, order_id, amount, method='cash'):
    r = post(c, f'/orders/{order_id}/payment', {
        'amount': str(amount),
        'payment_method': method,
        'notes': 'scenario payment',
    })
    status(r, 200, f'Payment added to order {order_id}')
    return r


def create_return(c, order_id, order_item_id, product_id, qty, refund_amount, restore=True):
    data = {
        'order_id': str(order_id),
        'return_reason': 'customer returned item',
        'items[0][order_item_id]': str(order_item_id),
        'items[0][product_id]': str(product_id),
        'items[0][quantity]': str(qty),
        'items[0][refund_amount]': str(refund_amount),
    }
    if restore:
        data['restore_inventory'] = 'on'
    r = post(c, '/returns/', data)
    status(r, 200, f'Return request submitted for order {order_id}')
    return r


def create_refund(c, order_id, return_id, amount, refund_date):
    r = post(c, '/refunds/', {
        'order_id': str(order_id),
        'return_id': str(return_id) if return_id else '',
        'amount': str(amount),
        'refund_date': refund_date,
        'refund_method': 'bank_transfer',
        'reason': 'customer_request',
        'notes': 'scenario refund',
    })
    status(r, 200, f'Refund recorded for order {order_id}')
    return db_one('SELECT * FROM refunds ORDER BY id DESC LIMIT 1')['id']


def dashboard_period(start, end):
    db = get_db()
    try:
        return get_revenue_and_profit_data(db, start, end)
    finally:
        db.close()


def run():
    global _PASS, _FAIL, _FAILURES
    _PASS = _FAIL = 0
    _FAILURES = []

    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)
    init_db()
    disable_auto_backup()

    with flask_app.test_client() as c:
        login(c)

        section('Scenario Data Setup')
        cat_clothing = create_category(c, 'Clothing', 'Wearables')
        cat_bags = create_category(c, 'Bags', 'Bags and purses')
        cat_cosmetics = create_category(c, 'Cosmetics', 'Beauty items')

        cust_a = create_customer(c, 'KH-A001', 'Nguyen Lan', 'HCM')
        cust_b = create_customer(c, 'KH-B001', 'Tran Mai', 'Hanoi')
        cust_c = create_customer(c, 'KH-C001', 'Pham Linh', 'Da Nang')

        shirt = create_product(c, 'AO0001', 'Linen Shirt', cat_clothing, 150000, 70000, min_stock=2)
        handbag = create_product(c, 'TUI0001', 'Handbag', cat_bags, 1000000, 500000, min_stock=1)
        sunscreen = create_product(c, 'MY0001', 'Sunscreen', cat_cosmetics, 300000, 130000, min_stock=3)
        legacy_product = create_product(c, 'LEG0001', 'Legacy Stock Item', cat_clothing, 200000, 120000, min_stock=1)

        shirt_lot_a = create_intake(c, shirt, 5, 70000, 50000, '2026-01-05', note='shirt lot A')
        shirt_lot_b = create_intake(c, shirt, 5, 90000, 0, '2026-02-01', note='shirt lot B')
        handbag_lot = create_intake(c, handbag, 2, 500000, 100000, '2026-01-10', note='handbag lot')
        sunscreen_neg = create_intake(c, sunscreen, -3, 130000, 0, '2026-02-10', note='preorder marker')

        legacy_order_id = exec_db('''
            INSERT INTO orders
                (order_code, customer_id, order_date, order_status, payment_status,
                 subtotal, total_amount, shipping_fee, shipping_paid_by)
            VALUES (?, ?, ?, 'completed', 'not_paid', ?, ?, 0, 'customer')
        ''', ('HD000001', cust_a, '1999-01-01 00:00:00', 400000, 400000))
        exec_db('''
            INSERT INTO order_items
                (order_id, product_id, quantity, unit_price, discount_percent, line_total)
            VALUES (?, ?, 2, 200000, 0, 400000)
        ''', (legacy_order_id, legacy_product))

        section('Golden Ledger: Orders, FIFO, Payments, Returns, Refunds')
        order_a = create_order(c, cust_a, '2026-03-10', [
            {'product_id': shirt, 'quantity': 6, 'unit_price': 150000, 'discount_percent': 10},
        ], shipping=30000, shipping_paid_by='customer', notes='6 shirts with customer shipping')
        complete_order(c, order_a)
        order_a_row = db_one('SELECT * FROM orders WHERE id = ?', (order_a,))
        money_eq(order_a_row['total_amount'], 840000, 'Order A total = 6*150k*90% + 30k shipping')
        money_eq(
            db_value('SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations WHERE order_id = ?', (order_a,)),
            490000,
            'Order A FIFO COGS = 5*80k + 1*90k'
        )
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (shirt_lot_a,))['remaining_quantity'], 0,
           'Oldest shirt lot fully consumed')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (shirt_lot_b,))['remaining_quantity'], 4,
           'Second shirt lot has 4 remaining')

        order_b = create_order(c, cust_b, '2026-03-12', [
            {'product_id': handbag, 'quantity': 1, 'unit_price': 1000000, 'discount_percent': 0},
        ], discount=100000, shipping=40000, shipping_paid_by='seller', notes='seller pays shipping')
        complete_order(c, order_b)
        add_payment(c, order_b, 400000, method='bank_transfer')
        order_b_row = db_one('SELECT * FROM orders WHERE id = ?', (order_b,))
        money_eq(order_b_row['total_amount'], 900000, 'Order B total excludes seller-paid shipping')
        eq(order_b_row['shipping_paid_by'], 'seller', 'Order B stores seller-paid shipping mode')
        money_eq(
            db_value('SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations WHERE order_id = ?', (order_b,)),
            550000,
            'Order B COGS includes amortized inbound shipping'
        )
        eq(db_one('SELECT payment_status FROM orders WHERE id = ?', (order_b,))['payment_status'], 'partially_paid',
           'Order B is partially paid after 400k payment')

        sunscreen_pos = create_intake(c, sunscreen, 3, 130000, 30000, '2026-03-14', note='sunscreen arrived')
        order_c = create_order(c, cust_c, '2026-03-15', [
            {'product_id': sunscreen, 'quantity': 3, 'unit_price': 300000, 'discount_percent': 0},
        ], notes='preorder fulfilled after intake')
        complete_order(c, order_c)
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (sunscreen_neg,))['remaining_quantity'], -3,
           'Negative preorder marker is untouched by FIFO')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (sunscreen_pos,))['remaining_quantity'], 0,
           'Positive sunscreen lot is consumed by FIFO')
        money_eq(
            db_value('SELECT SUM(quantity_allocated * cost_price_at_sale) FROM order_allocations WHERE order_id = ?', (order_c,)),
            420000,
            'Order C COGS = 3*(130k + 30k/3)'
        )

        shirt_item = db_one('SELECT id FROM order_items WHERE order_id = ? AND product_id = ?', (order_a, shirt))
        create_return(c, order_a, shirt_item['id'], shirt, 2, 270000, restore=False)
        return_row = db_one('SELECT * FROM returns ORDER BY id DESC LIMIT 1')
        check(return_row is not None, 'Return row exists after shirt return')
        create_refund(c, order_a, return_row['id'], 270000, '2026-04-05')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (shirt_lot_b,))['remaining_quantity'], 4,
           'Return does not restore the original newer FIFO lot')
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (shirt_lot_a,))['remaining_quantity'], 0,
           'Return does not restore the original oldest FIFO lot')
        return_item = db_one('''
            SELECT id, restock_action, restock_inventory_lot_id
            FROM return_items
            WHERE return_id = ?
        ''', (return_row['id'],))
        check(return_item is not None, 'Return item exists before after-the-fact restock')
        eq(return_item['restock_action'], 'none', 'Return item starts as not restocked when checkbox was missed')
        check(return_item['restock_inventory_lot_id'] is None, 'No inventory lot exists before after-the-fact restock')

        before_restock = dashboard_period('2026-01-01 00:00:00', '2026-12-31 23:59:59')
        r = post(c, f'/returns/{return_row["id"]}/restock', {
            'return_item_id': str(return_item['id']),
            f'restock_quantity_{return_item["id"]}': '2',
            f'restock_unit_cost_{return_item["id"]}': '0',
            f'restock_shipping_cost_{return_item["id"]}': '0',
        })
        status(r, 200, 'After-the-fact return restock request succeeds')
        return_lot = db_one('''
            SELECT i.id, i.quantity, i.remaining_quantity, i.cost_price,
                   ri.restock_action, ri.restock_quantity
            FROM return_items ri
            JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.return_id = ?
        ''', (return_row['id'],))
        check(return_lot is not None, 'After-the-fact restock creates a separate returned-goods intake lot')
        eq(return_lot['quantity'], 2, 'Returned-goods lot quantity equals the restocked quantity')
        eq(return_lot['remaining_quantity'], 2, 'Returned-goods lot has the returned quantity available')
        money_eq(return_lot['cost_price'], 0, 'Returned-goods lot defaults to zero cost')
        eq(return_lot['restock_action'], 'return_intake', 'Return item records return_intake action')
        eq(return_lot['restock_quantity'], 2, 'Return item records restock quantity')
        after_restock = dashboard_period('2026-01-01 00:00:00', '2026-12-31 23:59:59')
        for key in ('gross_revenue', 'total_refunds', 'total_revenue', 'total_cogs', 'seller_shipping', 'gross_profit'):
            money_eq(after_restock[key], before_restock[key], f'After-the-fact restock does not change dashboard {key}')

        before_writeoff = dashboard_period('2026-01-01 00:00:00', '2026-12-31 23:59:59')
        r = post(c, f'/inventory/{shirt_lot_b}/write-off', {
            'quantity': '1',
            'reason': 'damaged_unsellable',
            'notes': 'found damaged during stock count',
        })
        status(r, 200, 'General inventory write-off request succeeds')
        adjustment = db_one('''
            SELECT id, adjustment_code, inventory_lot_id, product_id, return_item_id,
                   quantity_delta, unit_cost, total_cost
            FROM inventory_adjustments
            ORDER BY id DESC
            LIMIT 1
        ''')
        check(adjustment is not None, 'Inventory write-off creates adjustment row')
        exec_db("UPDATE inventory_adjustments SET adjustment_date = '2026-05-01 09:00:00' WHERE id = ?", (adjustment['id'],))
        eq(db_one('SELECT remaining_quantity FROM inventory WHERE id = ?', (shirt_lot_b,))['remaining_quantity'], 3,
           'Inventory write-off reduces only the selected lot stock')
        check(adjustment['adjustment_code'].startswith('AD'), 'Inventory write-off uses AD adjustment code')
        eq(adjustment['inventory_lot_id'], shirt_lot_b, 'Inventory write-off records source lot')
        eq(adjustment['product_id'], shirt, 'Inventory write-off records product')
        check(adjustment['return_item_id'] is None, 'General inventory write-off is not linked to a return item')
        eq(adjustment['quantity_delta'], -1, 'Inventory write-off stores negative quantity delta')
        money_eq(adjustment['unit_cost'], 90000, 'Inventory write-off unit cost uses lot cost')
        money_eq(adjustment['total_cost'], 90000, 'Inventory write-off total cost is exact')
        after_writeoff = dashboard_period('2026-01-01 00:00:00', '2026-12-31 23:59:59')
        for key in ('gross_revenue', 'total_refunds', 'total_revenue', 'total_cogs', 'seller_shipping', 'gross_profit'):
            money_eq(after_writeoff[key], before_writeoff[key], f'Inventory write-off does not change dashboard {key}')
        money_eq(after_writeoff['inventory_adjustments'], before_writeoff['inventory_adjustments'] + 90000,
                 'Inventory write-off increases separate adjustment cost')
        money_eq(after_writeoff['adjusted_profit'], after_writeoff['gross_profit'] - after_writeoff['inventory_adjustments'],
                 'Inventory write-off reduces profit after adjustments')

        section('Dashboard and Report Accounting')
        all_data = dashboard_period('2026-01-01 00:00:00', '2026-12-31 23:59:59')
        money_eq(all_data['gross_revenue'], 2640000, 'All-year gross revenue is exact')
        money_eq(all_data['total_refunds'], 270000, 'All-year refunds are exact')
        money_eq(all_data['total_revenue'], 2370000, 'All-year net revenue = gross - refunds')
        money_eq(all_data['total_cogs'], 1460000, 'All-year FIFO COGS remains on original sales')
        money_eq(all_data['seller_shipping'], 40000, 'All-year seller shipping is exact')
        money_eq(all_data['gross_profit'], 870000, 'All-year gross profit = net revenue - COGS - seller shipping')
        money_eq(all_data['inventory_adjustments'], 90000, 'All-year inventory write-offs are exact and separate')
        money_eq(all_data['adjusted_profit'], 780000, 'All-year profit after adjustments subtracts write-offs')
        money_eq(round(all_data['profit_margin'], 1), 36.7, 'All-year profit margin is 36.7%')

        march_data = dashboard_period('2026-03-01 00:00:00', '2026-03-31 23:59:59')
        money_eq(march_data['gross_revenue'], 2640000, 'March gross revenue includes March active sale orders')
        money_eq(march_data['total_refunds'], 0, 'March does not include April refund')
        april_data = dashboard_period('2026-04-01 00:00:00', '2026-04-30 23:59:59')
        money_eq(april_data['gross_revenue'], 0, 'April has no active order revenue in this ledger')
        money_eq(april_data['total_refunds'], 270000, 'April refund is counted by refund date')

        debt = db_value('''
            SELECT COALESCE(SUM(o.total_amount), 0) - COALESCE((
                SELECT SUM(p.amount)
                FROM payments p JOIN orders o2 ON p.order_id = o2.id
                WHERE o2.customer_id = ? AND o2.order_status IN ('processing', 'completed')
            ), 0)
            FROM orders o
            WHERE o.customer_id = ? AND o.order_status IN ('processing', 'completed')
        ''', (cust_b, cust_b))
        money_eq(debt, 500000, 'Customer B outstanding debt is computed live')

        section('Mixed Legacy and FIFO COGS')
        exec_db('UPDATE orders SET order_date = ? WHERE id = ?',
                ('2026-03-20 00:00:00', legacy_order_id))
        mixed_data = dashboard_period('2026-03-01 00:00:00', '2026-03-31 23:59:59')
        money_eq(mixed_data['total_cogs'], 1700000,
                 'Dashboard COGS includes FIFO orders plus legacy no-allocation order')
        db = get_db()
        try:
            report_cogs = reports_calc_cogs(db.cursor(), '2026-03-01 00:00:00', '2026-03-31 23:59:59')
        finally:
            db.close()
        money_eq(report_cogs, 1700000, 'Reports COGS includes mixed FIFO and legacy orders')

        section('Cumulative Return Guard')
        before_returns = db_value('SELECT COUNT(*) FROM returns WHERE original_order_id = ?', (order_a,))
        r = create_return(c, order_a, shirt_item['id'], shirt, 5, 675000, restore=False)
        after_returns = db_value('SELECT COUNT(*) FROM returns WHERE original_order_id = ?', (order_a,))
        check(after_returns == before_returns,
              'Second return cannot exceed original ordered quantity cumulatively',
              f'return count changed from {before_returns} to {after_returns}; response starts {r.data[:160]!r}')

        section('USD Intake Conversion')
        exec_db('UPDATE settings SET vnd_usd_rate = 25000 WHERE id = 1')
        usd_product = create_product(c, 'USD0001', 'USD Cost Test Item', cat_cosmetics, 700000, 0, min_stock=1)
        usd_lot = create_intake(c, usd_product, 2, 10, 5, '2026-03-22', currency='USD', note='paid supplier in USD')
        usd_row = db_one('SELECT cost_price, shipping_cost, exchange_rate, currency FROM inventory WHERE id = ?', (usd_lot,))
        eq(usd_row['currency'], 'USD', 'USD intake preserves source currency label')
        money_eq(usd_row['exchange_rate'], 25000, 'USD intake stores exchange rate used')
        money_eq(usd_row['cost_price'], 250000, 'USD unit cost is stored internally as VND')
        money_eq(usd_row['shipping_cost'], 125000, 'USD inbound shipping is stored internally as VND')

        section('Product Images')
        img_product_id = create_product(c, 'IMG0001', 'Photo Product', cat_clothing, 100000, 50000, min_stock=1)

        def add_product_photo(filename, content):
            data = {
                'csrf_token': CSRF_TOKEN,
                'product_code': 'IMG0001',
                'name': 'Photo Product',
                'category_id': str(cat_clothing),
                'sale_price': '100000',
                'cost_price': '50000',
                'barcode': 'IMG0001',
                'min_stock_level': '1',
                'notes': '',
                'product_images': (io.BytesIO(content), filename, 'image/jpeg'),
            }
            resp = c.post(f'/products/{img_product_id}', data=data, follow_redirects=True)
            status(resp, 200, f'Product photo submitted: {filename}')
            body = resp.data.decode('utf-8', errors='ignore')
            check('Product updated successfully' in body,
                  f'Product update accepted for photo: {filename}',
                  body[:300].replace('\n', ' '))

        add_product_photo('one.jpg', b'fake image 1')
        add_product_photo('two.png', b'fake image 2')
        add_product_photo('three.webp', b'fake image 3')
        add_product_photo('four.gif', b'fake image 4')

        img_product = db_one("SELECT id FROM products WHERE product_code = 'IMG0001'")
        img_count = db_value('SELECT COUNT(*) FROM product_images WHERE product_id = ?', (img_product['id'],))
        eq(img_count, 3, 'Product image uploads are capped at three files')
        img_row = db_one('SELECT id, filename FROM product_images WHERE product_id = ? ORDER BY id LIMIT 1',
                         (img_product['id'],))
        img_path = os.path.join(_UPLOAD_DIR, str(img_product['id']), img_row['filename'])
        check(os.path.exists(img_path), 'Uploaded image file exists on disk')
        served_img = c.get(f"/product-uploads/{img_product['id']}/{img_row['filename']}")
        status(served_img, 200, 'Uploaded product image is served from configured upload folder')
        served_img.close()
        post(c, f'/products/{img_product["id"]}/images/{img_row["id"]}/delete', {})
        eq(db_value('SELECT COUNT(*) FROM product_images WHERE id = ?', (img_row['id'],)), 0,
           'Deleted product image row is removed')
        check(not os.path.exists(img_path), 'Deleted product image file is removed from disk')

        section('UTF-8 Import and Export')
        vn_category = '\u00c1o d\u00e0i'
        vn_product = '\u00c1o d\u00e0i l\u1ee5a Hu\u1ebf'
        vn_csv = (
            'name,category,sale_price,cost_price,barcode,min_stock_level\n'
            f'{vn_product},{vn_category},450000,220000,VN-AD-001,2\n'
        ).encode('utf-8')
        upload = {
            'csrf_token': CSRF_TOKEN,
            'file': (io.BytesIO(vn_csv), 'products.csv'),
        }
        r = c.post('/import/products', data=upload, content_type='multipart/form-data', follow_redirects=True)
        body_has(r, 'Successfully imported', 'UTF-8 product CSV import succeeds')
        check(db_one('SELECT id FROM products WHERE name = ?', (vn_product,)) is not None,
              'Vietnamese product name round-trips into DB')
        r = c.get('/exports/products.csv')
        status(r, 200, 'Products CSV export returns OK')
        exported = r.data.decode('utf-8-sig', errors='replace')
        rows = list(csv.DictReader(io.StringIO(exported)))
        check(any(row.get('Name') == vn_product for row in rows),
              'Vietnamese product name appears in exported CSV')

    return _PASS, _FAIL, _FAILURES


def cleanup():
    for suffix in ('', '-wal', '-shm'):
        path = _TEST_DB + suffix
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
    if os.path.exists(_UPLOAD_DIR):
        shutil.rmtree(_UPLOAD_DIR, ignore_errors=True)


if __name__ == '__main__':
    try:
        passed, failed, failures = run()
        print('\n' + '-' * 54)
        print(f'Results: {passed} passed, {failed} failed')
        if failures:
            print('\nFailures:')
            for name, detail in failures:
                print(f'- {name}: {detail}')
            raise SystemExit(1)
        print('All real-world scenario tests passed.')
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
    finally:
        cleanup()
