#!/usr/bin/env python3
"""
Import / Export correctness tests.

Export tests verify actual column values and row counts — not just HTTP 200.
Import tests verify data lands correctly in the DB and cover error cases.

Bugs fixed before this test was written (all previously untested):
  1. import_inventory() — shipping_cost and currency columns were parsed but never
     inserted; the INSERT listed only 6 columns. Fixed to pass all 8.
  2. import_customers() — customer_code in CSV was silently discarded; a new KH######
     was always generated. Fixed: CSV-supplied code is used when present.
  3. import_products() — always used generate_code('SP') regardless of category.
     Fixed to call generate_product_code(category_id=...) so category prefix is used.

Run: python test_import_export.py
"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import db as db_module
_TEST_DB = tempfile.mktemp(suffix='_inv_impexp_test.db')
db_module.DATABASE = _TEST_DB

import backup as backup_module
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_impexp')
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
    return check(text in body, name, f'Expected {text!r} not found')

def status(resp, code, name):
    return check(resp.status_code == code, name, f'HTTP {resp.status_code} != {code}')

CSRF = 'test-csrf-impexp'

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

def parse_csv_response(resp):
    text = resp.data.decode('utf-8', errors='ignore')
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)

def parse_xlsx_response(resp):
    """Return list of {sheet_name: [rows]} parsed from an XLSX response."""
    try:
        import openpyxl
    except ImportError:
        return None
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    result = {}
    for name in wb.sheetnames:
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        result[name] = rows
    return result


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

        # ── Seed test data ─────────────────────────────────────────────────
        section('Seed data')

        r = post(c, '/categories/', {'name': 'Gadgets', 'description': 'Gadgets'})
        cat_id = db_query("SELECT id FROM categories WHERE name='Gadgets'")['id']
        check(cat_id is not None, 'Category seeded')

        r = post(c, '/products/', {
            'product_code': 'GA0001',
            'name': 'Smart Watch',
            'category_id': cat_id,
            'sale_price': '2000000',
            'cost_price': '1200000',
            'min_stock_level': '3',
            'barcode': 'BAR-SW-001',
        })
        prod_id = db_query("SELECT id FROM products WHERE product_code='GA0001'")['id']
        check(prod_id is not None, 'Product seeded')

        r = post(c, '/inventory/', {
            'product_id': prod_id,
            'quantity': '20',
            'cost_price': '1200000',
            'shipping_cost': '500000',
            'currency': 'VND',
            'intake_date': '2026-04-01',
            'notes': 'Initial stock',
        })
        lot_id = db_query("SELECT id FROM inventory WHERE product_id=?", (prod_id,))['id']
        check(lot_id is not None, 'Inventory lot seeded')

        r = post(c, '/customers/', {
            'customer_code': 'KH000001',
            'name': 'Test Customer',
            'email': 'tc@example.com',
            'phone': '0900000001',
            'region': 'HCM',
        })
        cust_id = db_query("SELECT id FROM customers WHERE customer_code='KH000001'")['id']
        check(cust_id is not None, 'Customer seeded')

        # Create + complete an order so exports have non-trivial data
        r = post(c, '/orders/', {
            'customer_id': cust_id,
            'order_date': '2026-04-15',
            'items[0][product_id]': prod_id,
            'items[0][quantity]': '2',
            'items[0][unit_price]': '2000000',
            'items[0][discount_percent]': '0',
            'discount_amount': '0',
            'shipping_fee': '30000',
            'shipping_paid_by': 'customer',
        })
        order = db_query("SELECT id FROM orders ORDER BY id DESC LIMIT 1")
        order_id = order['id']
        r = post(c, f'/orders/{order_id}/status', {'status': 'completed'})
        has(r, 'Completed', 'Order completed for export test data')

        # Add a payment
        r = post(c, f'/orders/{order_id}/payment', {
            'amount': '4030000',
            'payment_method': 'bank_transfer',
        })
        check(r.status_code == 200, 'Payment recorded for export test data')

        # ── Export: Products CSV ───────────────────────────────────────────
        section('Export: Products CSV correctness')

        r = c.get('/exports/products.csv')
        status(r, 200, 'Products CSV returns 200')
        check('text/csv' in r.content_type, 'Products CSV content-type correct')

        rows = parse_csv_response(r)
        check(len(rows) >= 1, f'Products CSV has at least 1 row (got {len(rows)})')

        ga_row = next((row for row in rows if row.get('Product Code') == 'GA0001'), None)
        check(ga_row is not None, 'GA0001 appears in Products CSV')
        if ga_row:
            check(ga_row['Name'] == 'Smart Watch', f'Name correct (got {ga_row["Name"]!r})')
            check(ga_row['Category'] == 'Gadgets', f'Category correct (got {ga_row["Category"]!r})')
            check(ga_row['Sale Price (VND)'] == '2000000',
                  f'Sale price correct (got {ga_row["Sale Price (VND)"]!r})')
            check(ga_row['Cost Price (VND)'] == '1200000',
                  f'Cost price correct (got {ga_row["Cost Price (VND)"]!r})')
            check(ga_row['Barcode'] == 'BAR-SW-001',
                  f'Barcode correct (got {ga_row["Barcode"]!r})')
            check(ga_row['Current Stock'] == '18',
                  f'Current stock = 18 (2 sold; got {ga_row["Current Stock"]!r})')
            check(ga_row['Active'] == 'Yes', f'Active = Yes (got {ga_row["Active"]!r})')

        # ── Export: Customers CSV ──────────────────────────────────────────
        section('Export: Customers CSV correctness')

        r = c.get('/exports/customers.csv')
        rows = parse_csv_response(r)
        kh_row = next((row for row in rows if row.get('Customer Code') == 'KH000001'), None)
        check(kh_row is not None, 'KH000001 in Customers CSV')
        if kh_row:
            check(kh_row['Name'] == 'Test Customer', f'Name correct (got {kh_row["Name"]!r})')
            check(kh_row['Email'] == 'tc@example.com', f'Email correct (got {kh_row["Email"]!r})')
            check(kh_row['Region'] == 'HCM', f'Region correct (got {kh_row["Region"]!r})')
            # Order total = 2×2,000,000 + 30,000 = 4,030,000
            check(kh_row['Total Spent (VND)'] == '4030000',
                  f'Total spent = 4,030,000 (got {kh_row["Total Spent (VND)"]!r})')
            # Fully paid — outstanding debt should be 0
            check(kh_row['Outstanding Debt (VND)'] == '0',
                  f'Debt = 0 when fully paid (got {kh_row["Outstanding Debt (VND)"]!r})')

        # ── Export: Orders CSV ─────────────────────────────────────────────
        section('Export: Orders CSV correctness')

        r = c.get('/exports/orders.csv')
        rows = parse_csv_response(r)
        check(len(rows) >= 1, f'Orders CSV has at least 1 row (got {len(rows)})')
        order_row = rows[0] if rows else None
        if order_row:
            check('Order Code' in order_row, 'Orders CSV has Order Code column')
            check('Status' in order_row, 'Orders CSV has Status column')
            check('Total Amount (VND)' in order_row, 'Orders CSV has Total Amount column')
            check(int(order_row['Total Amount (VND)']) == 4030000,
                  f'Order total = 4,030,000 (got {order_row["Total Amount (VND)"]!r})')
            check(int(order_row['Amount Paid (VND)']) == 4030000,
                  f'Amount paid = 4,030,000 (got {order_row["Amount Paid (VND)"]!r})')
            check(order_row['Status'] == 'completed',
                  f'Order status = completed (got {order_row["Status"]!r})')

        # ── Export: Inventory CSV ──────────────────────────────────────────
        section('Export: Inventory CSV correctness')

        r = c.get('/exports/inventory.csv')
        rows = parse_csv_response(r)
        inv_row = next((row for row in rows if row.get('Product Code') == 'GA0001'), None)
        check(inv_row is not None, 'GA0001 in Inventory CSV')
        if inv_row:
            check(inv_row['Quantity (Original)'] == '20',
                  f'Original qty = 20 (got {inv_row["Quantity (Original)"]!r})')
            check(inv_row['Remaining Quantity'] == '18',
                  f'Remaining qty = 18 (got {inv_row["Remaining Quantity"]!r})')
            check(inv_row['Cost Price (VND)'] == '1200000',
                  f'Cost price correct (got {inv_row["Cost Price (VND)"]!r})')
            check(inv_row['Shipping to Warehouse (VND)'] == '500000',
                  f'Shipping cost correct (got {inv_row["Shipping to Warehouse (VND)"]!r})')
            check(inv_row['Currency'] == 'VND',
                  f'Currency = VND (got {inv_row["Currency"]!r})')
            check(inv_row['Intake Date'].startswith('2026-04-01'),
                  f'Intake date correct (got {inv_row["Intake Date"]!r})')

        # ── Export: unknown type redirects with error ──────────────────────
        section('Export: unknown type handled gracefully')

        r = c.get('/exports/hacked.csv', follow_redirects=True)
        has(r, 'Unknown export type', 'Unknown CSV type shows error flash')

        r = c.get('/exports/hacked.xlsx', follow_redirects=True)
        has(r, 'Unknown export type', 'Unknown XLSX type shows error flash')

        # ── Export: XLSX all-sheets ────────────────────────────────────────
        section('Export: XLSX all-sheets')

        r = c.get('/exports/all.xlsx')
        status(r, 200, 'Export-all XLSX returns 200')
        sheets = parse_xlsx_response(r)
        if sheets is not None:
            check('Products' in sheets, 'XLSX has Products sheet')
            check('Customers' in sheets, 'XLSX has Customers sheet')
            check('Orders' in sheets, 'XLSX has Orders sheet')
            check('Inventory' in sheets, 'XLSX has Inventory sheet')
            # Each sheet has header row + at least one data row
            for sheet_name in ('Products', 'Customers', 'Orders', 'Inventory'):
                if sheet_name in sheets:
                    check(len(sheets[sheet_name]) >= 2,
                          f'{sheet_name} sheet has header + at least 1 data row '
                          f'(got {len(sheets[sheet_name])} rows)')
        else:
            ok('XLSX parse skipped (openpyxl not installed)')

        # ── Import: inventory with shipping_cost and currency ─────────────
        section('Import: inventory shipping_cost and currency are stored (bug fix)')

        inv_csv = (
            'product_code,quantity,cost_price,shipping_cost,currency,intake_date,notes\n'
            'GA0001,5,1100000,300000,VND,2026-04-20,Restock batch\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', inv_csv, 'inv.csv')
        has(r, 'Successfully imported', 'Inventory import with shipping_cost succeeds')

        new_lot = db_query(
            "SELECT cost_price, shipping_cost, currency, notes "
            "FROM inventory WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (prod_id,)
        )
        check(new_lot is not None, 'New lot created in DB')
        if new_lot:
            check(new_lot['cost_price'] == 1100000,
                  f'cost_price stored correctly (got {new_lot["cost_price"]})')
            check(new_lot['shipping_cost'] == 300000,
                  f'shipping_cost stored correctly (got {new_lot["shipping_cost"]})')
            check(new_lot['currency'] == 'VND',
                  f'currency stored correctly (got {new_lot["currency"]!r})')
            check(new_lot['notes'] == 'Restock batch',
                  f'notes stored correctly (got {new_lot["notes"]!r})')

        # ── Import: inventory missing product ─────────────────────────────
        section('Import: inventory row with unknown product_code is rejected')

        bad_inv = (
            'product_code,quantity,cost_price,intake_date\n'
            'NOTEXIST,10,500000,2026-04-01\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', bad_inv, 'bad_inv.csv')
        has(r, 'failed', 'Row with unknown product_code reported as error')
        has(r, 'Product not found', 'Error message mentions product not found')

        # ── Import: inventory zero quantity rejected ───────────────────────
        section('Import: inventory zero quantity is rejected')

        zero_csv = (
            'product_code,quantity,cost_price\n'
            f'GA0001,0,500000\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', zero_csv, 'zero.csv')
        has(r, 'failed', 'Zero-quantity inventory row is rejected')
        has(r, 'greater than 0', 'Error message says quantity must be > 0')

        # ── Import: inventory bad date format rejected ────────────────────
        section('Import: inventory invalid date format rejected')

        bad_date_csv = (
            'product_code,quantity,cost_price,intake_date\n'
            f'GA0001,3,500000,15/04/2026\n'
        ).encode()
        r = post_file(c, '/import/inventory', 'file', bad_date_csv, 'bad_date.csv')
        has(r, 'failed', 'Bad date format reported as error')
        has(r, 'Invalid date', 'Error message mentions invalid date format')

        # ── Import: inventory empty CSV (header only) ─────────────────────
        section('Import: inventory empty CSV (header only)')

        empty_csv = b'product_code,quantity,cost_price,intake_date\n'
        r = post_file(c, '/import/inventory', 'file', empty_csv, 'empty.csv')
        # Should not crash; no success, no error row
        check(r.status_code == 200, f'Empty inventory CSV returns 200 (got {r.status_code})')

        # ── Import: non-CSV file rejected ─────────────────────────────────
        section('Import: non-CSV file rejected')

        r = post_file(c, '/import/inventory', 'file', b'not a csv', 'file.txt')
        has(r, 'CSV', 'Non-CSV file upload is rejected with CSV error')

        r = post_file(c, '/import/products', 'file', b'not a csv', 'file.txt')
        has(r, 'CSV', 'Non-CSV products upload is rejected')

        r = post_file(c, '/import/customers', 'file', b'not a csv', 'file.txt')
        has(r, 'CSV', 'Non-CSV customers upload is rejected')

        # ── Import: customers respects customer_code in CSV (bug fix) ─────
        section('Import: customer_code in CSV is used (bug fix)')

        cust_csv = (
            'customer_code,name,email,phone,region\n'
            'KH999999,CSV Customer,csv@example.com,0911111111,HN\n'
        ).encode()
        r = post_file(c, '/import/customers', 'file', cust_csv, 'customers.csv')
        has(r, 'Successfully imported', 'Customer import with explicit code succeeds')
        imported_cust = db_query("SELECT customer_code, name FROM customers WHERE customer_code='KH999999'")
        check(imported_cust is not None,
              'Customer with CSV-supplied code KH999999 created')
        if imported_cust:
            check(imported_cust['name'] == 'CSV Customer',
                  f'Name correct (got {imported_cust["name"]!r})')

        # ── Import: duplicate customer_code skipped ───────────────────────
        section('Import: duplicate customer_code in CSV is rejected')

        dup_csv = (
            'customer_code,name,email\n'
            'KH999999,Dup Name,dup@example.com\n'
        ).encode()
        r = post_file(c, '/import/customers', 'file', dup_csv, 'dup_cust.csv')
        has(r, 'failed', 'Duplicate customer_code is reported as error')

        # ── Import: products use category prefix (bug fix) ────────────────
        section('Import: product uses category-based code prefix (bug fix)')

        prod_csv = (
            'name,category,sale_price,cost_price,barcode,min_stock_level\n'
            'Category Prefix Product,Gadgets,1500000,800000,,0\n'
        ).encode()
        r = post_file(c, '/import/products', 'file', prod_csv, 'prod.csv')
        has(r, 'Successfully imported', 'Product import with category succeeds')
        imported_prod = db_query("SELECT product_code FROM products WHERE name='Category Prefix Product'")
        check(imported_prod is not None, 'Imported product exists in DB')
        if imported_prod:
            code = imported_prod['product_code']
            check(code.startswith('GA'),
                  f'Product code uses Gadgets prefix "GA" (got {code!r})')

        # ── Import: product auto-creates new category ─────────────────────
        section('Import: product import auto-creates missing category')

        new_cat_csv = (
            'name,category,sale_price,cost_price\n'
            'Brand New Item,AutoCat,999000,500000\n'
        ).encode()
        r = post_file(c, '/import/products', 'file', new_cat_csv, 'new_cat.csv')
        has(r, 'Successfully imported', 'Product with new category imported')
        auto_cat = db_query("SELECT id FROM categories WHERE name='AutoCat'")
        check(auto_cat is not None, 'AutoCat category was auto-created')

        # ── Import: product missing name rejected ─────────────────────────
        section('Import: product with empty name is rejected')

        noname_csv = b'name,category,sale_price,cost_price\n,Gadgets,100000,50000\n'
        r = post_file(c, '/import/products', 'file', noname_csv, 'noname.csv')
        has(r, 'failed', 'Product with no name is reported as error')
        has(r, 'required', 'Error message says name is required')

        # ── Import: customers auto-generate code when CSV has no code ─────
        section('Import: customer without code in CSV gets auto-generated code')

        nocode_csv = (
            'name,email,phone\n'
            'No Code Customer,nocode@example.com,0922222222\n'
        ).encode()
        r = post_file(c, '/import/customers', 'file', nocode_csv, 'nocode.csv')
        has(r, 'Successfully imported', 'Customer without code in CSV imported')
        no_code_cust = db_query(
            "SELECT customer_code FROM customers WHERE name='No Code Customer'"
        )
        check(no_code_cust is not None, 'No-code customer exists in DB')
        if no_code_cust:
            check(no_code_cust['customer_code'].startswith('KH'),
                  f'Auto-generated code has KH prefix (got {no_code_cust["customer_code"]!r})')

        # ── Import: customers bad email rejected ───────────────────────────
        section('Import: customer with invalid email is rejected')

        bad_email_csv = (
            'name,email\n'
            'Bad Email Guy,notanemail\n'
        ).encode()
        r = post_file(c, '/import/customers', 'file', bad_email_csv, 'bad_email.csv')
        has(r, 'failed', 'Customer with invalid email is reported as error')
        has(r, 'Invalid email', 'Error message mentions invalid email')

        # ── Import: partial success — mix of good and bad rows ─────────────
        section('Import: partial CSV import succeeds for good rows, reports bad rows')

        mixed_csv = (
            'name,email,phone\n'
            'Good Customer 1,good1@example.com,0900000001\n'
            ',bad_email_no_name,0900000002\n'
            'Good Customer 2,good2@example.com,0900000003\n'
        ).encode()
        r = post_file(c, '/import/customers', 'file', mixed_csv, 'mixed.csv')
        has(r, 'Successfully imported 2', 'Two good rows imported in partial-success import')
        has(r, 'failed', 'One bad row reported as error')

        gc1 = db_query("SELECT id FROM customers WHERE name='Good Customer 1'")
        gc2 = db_query("SELECT id FROM customers WHERE name='Good Customer 2'")
        check(gc1 is not None and gc2 is not None,
              'Both good rows exist in DB after partial import')

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
    print(f'{color}{passed}/{total} import/export tests passed.\033[0m')
    sys.exit(0 if failed == 0 else 1)
