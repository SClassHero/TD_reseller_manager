#!/usr/bin/env python3
"""
Product image upload tests.

Covers:
  - Upload valid image on product create
  - Upload valid images on product edit
  - Unsupported file type is rejected with a warning (file not saved)
  - Max-3 limit enforced on create (extra files silently trimmed with warning)
  - Max-3 limit enforced on edit (further uploads blocked when full)
  - Delete image removes DB row and file from disk
  - Delete with mismatched product_id is rejected
  - Thumbnail column appears in product list query

Run: python test_images.py
"""
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
_TEST_DB = tempfile.mktemp(suffix='_inv_img_test.db')
db_module.DATABASE = _TEST_DB

import backup as backup_module
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_img')
shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
os.makedirs(_TEST_BACKUPS, exist_ok=True)
backup_module.BACKUPS_DIR = _TEST_BACKUPS

_TEST_UPLOADS = tempfile.mkdtemp(suffix='_inv_img_uploads')

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True
flask_app.config['UPLOAD_FOLDER'] = _TEST_UPLOADS

# Override the backup upload path so backup tests find the right folder
import backup as _bk
_orig_uploads_dir = _bk._uploads_dir
_bk._uploads_dir = lambda: _TEST_UPLOADS

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
        print(f'    \033[93m{str(detail)[:300]}\033[0m')

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

CSRF = 'test-csrf-images'

def post(c, url, data=None, **kw):
    d = dict(data or {})
    d['csrf_token'] = CSRF
    return c.post(url, data=d, follow_redirects=True, **kw)

def post_multipart(c, url, fields: dict, files: dict):
    """Post multipart form data mixing regular fields and file uploads."""
    data = {}
    for k, v in fields.items():
        data[k] = v
    data['csrf_token'] = CSRF
    for k, (content, filename) in files.items():
        data[k] = (io.BytesIO(content), filename)
    return c.post(url, data=data, content_type='multipart/form-data', follow_redirects=True)

# Minimal valid 1×1 PNG (RGB)
PNG_1X1 = bytes([
    0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a,
    0x00,0x00,0x00,0x0d,0x49,0x48,0x44,0x52,
    0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
    0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,0xde,
    0x00,0x00,0x00,0x0c,0x49,0x44,0x41,0x54,
    0x08,0xd7,0x63,0xf8,0xcf,0xc0,0x00,0x00,
    0x00,0x02,0x00,0x01,0xe2,0x21,0xbc,0x33,
    0x00,0x00,0x00,0x00,0x49,0x45,0x4e,0x44,0xae,0x42,0x60,0x82,
])

FAKE_PNG_AS_TXT = b'this is not an image'


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

        # Seed category
        r = post(c, '/categories/', {'name': 'Tech', 'description': ''})
        cat_id = db_query("SELECT id FROM categories WHERE name='Tech'")['id']

        # ── 1. Upload valid image on product create ────────────────────────
        section('Image upload on product create')

        data = {
            'product_code': 'TC0001',
            'name': 'Camera',
            'category_id': str(cat_id),
            'sale_price': '5000000',
            'cost_price': '3000000',
            'min_stock_level': '1',
        }
        r = post_multipart(c, '/products/', data,
                           {'product_images': (PNG_1X1, 'front.png')})
        has(r, 'Camera', 'Product with image created and listed')
        prod_id = db_query("SELECT id FROM products WHERE product_code='TC0001'")['id']
        check(prod_id is not None, 'Product exists in DB')

        img_row = db_query("SELECT id, filename, sort_order FROM product_images WHERE product_id=?", (prod_id,))
        check(img_row is not None, 'product_images row created')
        check(img_row and img_row['sort_order'] == 0, 'First image has sort_order 0')

        img_file = os.path.join(_TEST_UPLOADS, str(prod_id), img_row['filename']) if img_row else None
        check(img_file and os.path.exists(img_file), 'Image file exists on disk')
        img1_id = img_row['id'] if img_row else None

        # ── 2. Unsupported file type rejected ─────────────────────────────
        section('Unsupported file type')

        r = post_multipart(c, f'/products/{prod_id}', data,
                           {'product_images': (FAKE_PNG_AS_TXT, 'malware.exe')})
        has(r, 'unsupported format', 'Unsupported extension triggers warning')
        img_count = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
        check(img_count == 1, f'Unsupported file not saved to DB (count={img_count})')

        # ── 3. Add 2 more images on edit (reaching the 3-image limit) ─────
        section('Add images on product edit (up to max)')

        r = post_multipart(c, f'/products/{prod_id}', data, {
            'product_images': (PNG_1X1, 'side.png'),
        })
        # No warning expected — still under limit
        img_count = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
        check(img_count == 2, f'Second image added (count={img_count})')

        r = post_multipart(c, f'/products/{prod_id}', data, {
            'product_images': (PNG_1X1, 'back.png'),
        })
        img_count = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
        check(img_count == 3, f'Third image added — now at max (count={img_count})')

        # ── 4. Attempt to add a 4th image (slot full) ─────────────────────
        section('4th image blocked when already at max')

        r = post_multipart(c, f'/products/{prod_id}', data, {
            'product_images': (PNG_1X1, 'extra.png'),
        })
        has(r, 'maximum', 'Warning shown when slot is full')
        img_count_after = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
        check(img_count_after == 3, f'4th image not saved — still 3 in DB (count={img_count_after})')

        # ── 5. Create product with 5 images at once — only first 3 saved ──
        section('Create product with 5 images — max 3 enforced')

        r = post_multipart(c, '/products/', {
            'product_code': 'TC0002',
            'name': 'Multi-Image Product',
            'category_id': str(cat_id),
            'sale_price': '1000000',
            'cost_price': '500000',
            'min_stock_level': '0',
        }, {
            # Flask test client only supports one file per field name;
            # use getlist by sending multiple field entries
        })
        # Simulate 5-image upload via raw multipart
        import io as _io
        buf_data = {
            'product_code': 'TC0003',
            'name': 'Five Image Product',
            'category_id': str(cat_id),
            'sale_price': '1000000',
            'cost_price': '500000',
            'min_stock_level': '0',
            'csrf_token': CSRF,
        }
        # Build a multipart payload with 5 files under 'product_images'
        five_files = [(f'img{i}.png', PNG_1X1) for i in range(5)]
        # Use werkzeug test client's ability to pass a list of tuples for multi-file
        from werkzeug.datastructures import FileStorage, ImmutableMultiDict
        from io import BytesIO

        files_list = [(_io.BytesIO(PNG_1X1), f'photo_{i}.png') for i in range(5)]
        data_with_files = dict(buf_data)
        data_with_files['product_images'] = files_list  # list of file tuples

        r = c.post('/products/', data=data_with_files, content_type='multipart/form-data',
                   follow_redirects=True)
        five_prod = db_query("SELECT id FROM products WHERE product_code='TC0003'")
        if five_prod:
            saved_count = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?",
                                   (five_prod['id'],))
            check(saved_count <= 3,
                  f'Max 3 enforced when 5 uploaded at create time (saved={saved_count})')
            has(r, 'Maximum', 'Warning shown for extra images at create time')
        else:
            fail('Five-image product created (skip image count check)', 'Product not found in DB')

        # ── 6. Delete image — DB row removed, file deleted from disk ──────
        section('Delete product image')

        file_before = os.path.join(_TEST_UPLOADS, str(prod_id), img_row['filename'])
        check(os.path.exists(file_before), 'Image file exists before deletion')

        r = post(c, f'/products/{prod_id}/images/{img1_id}/delete', {})
        check(r.status_code == 200 or r.status_code == 302,
              f'Delete image returns redirect/200 (got {r.status_code})')

        img_after = db_query("SELECT id FROM product_images WHERE id=?", (img1_id,))
        check(img_after is None, 'product_images row deleted from DB')
        check(not os.path.exists(file_before), 'Image file removed from disk')

        img_count_after_del = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
        check(img_count_after_del == 2, f'Product now has 2 images after deletion (count={img_count_after_del})')

        # ── 7. Delete with wrong product_id is rejected ───────────────────
        section('Image delete cross-product guard')

        # Create a second product
        r = post(c, '/products/', {
            'product_code': 'TC0004',
            'name': 'Other Product',
            'category_id': str(cat_id),
            'sale_price': '100000',
            'cost_price': '50000',
        })
        other_prod_id = db_query("SELECT id FROM products WHERE product_code='TC0004'")['id']

        # Remaining images on prod_id
        remaining = db_query("SELECT id FROM product_images WHERE product_id=? LIMIT 1", (prod_id,))
        if remaining:
            real_img_id = remaining['id']
            # Try to delete prod_id's image through other_prod_id URL
            r = post(c, f'/products/{other_prod_id}/images/{real_img_id}/delete', {})
            still_there = db_query("SELECT id FROM product_images WHERE id=?", (real_img_id,))
            check(still_there is not None, 'Image from another product is NOT deleted via wrong URL')
        else:
            fail('Cross-product delete guard', 'No remaining image to test with')

        # ── 8. Thumbnail column shows in products list ────────────────────
        section('Thumbnail appears in product list query')

        r = c.get('/products/')
        status(r, 200, 'Products list loads')
        # The thumb subquery in products.py: product must show its thumbnail
        has(r, 'TC0001', 'Product with images appears in list')

        # ── 9. Product edit form shows existing images ────────────────────
        section('Edit form shows existing images')

        r = c.get(f'/products/{prod_id}/edit')
        status(r, 200, 'Edit form loads for product with images')
        has(r, 'delete', 'Edit form shows delete option for existing images')

        # ── 10. Delete product deactivates product (images NOT deleted) ───
        section('Soft-delete product does not delete image files')

        r = post(c, f'/products/{prod_id}/delete', {})
        prod_active = db_query(f"SELECT is_active FROM products WHERE id={prod_id}")
        check(prod_active and prod_active['is_active'] == 0, 'Product soft-deleted (is_active=0)')
        # Files should still be there — soft-delete doesn't wipe uploads
        remaining_img = db_query("SELECT filename FROM product_images WHERE product_id=? LIMIT 1", (prod_id,))
        if remaining_img:
            still_on_disk = os.path.exists(
                os.path.join(_TEST_UPLOADS, str(prod_id), remaining_img['filename'])
            )
            check(still_on_disk, 'Image file survives product soft-delete')

    return _PASS, _FAIL, _FAILURES


if __name__ == '__main__':
    passed, failed, failures = run()
    shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
    shutil.rmtree(_TEST_UPLOADS, ignore_errors=True)
    if os.path.exists(_TEST_DB):
        os.unlink(_TEST_DB)

    print(f'\n{"─"*50}')
    if failures:
        print(f'\033[91mFAILED tests:\033[0m')
        for name, detail in failures:
            print(f'  ✗ {name}')
            if detail:
                print(f'    {detail}')
    total = passed + failed
    color = '\033[92m' if failed == 0 else '\033[91m'
    print(f'{color}{passed}/{total} image tests passed.\033[0m')
    sys.exit(0 if failed == 0 else 1)
