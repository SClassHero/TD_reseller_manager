#!/usr/bin/env python3
"""
Backup / restore tests extended to cover product photo handling.

Covers:
  - Backup includes photo files in ZIP (uploads/products/ subtree)
  - backup_info.json.photos_count matches actual file count
  - Restore extracts photos to disk and preserves DB rows
  - Old backup without uploads/ entries restores cleanly (no crash, uploads dir empty)
  - Backup of empty uploads dir still succeeds (photos_count = 0)
  - Reset clears product_images table (images not in the no-image tables list check)

Run: python test_backup_images.py
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import db as db_module
_TEST_DB = tempfile.mktemp(suffix='_inv_bkimg_test.db')
db_module.DATABASE = _TEST_DB

import backup as bk
_TEST_BACKUPS = os.path.join(APP_DIR, '_test_backups_bkimg')
shutil.rmtree(_TEST_BACKUPS, ignore_errors=True)
os.makedirs(_TEST_BACKUPS, exist_ok=True)
bk.BACKUPS_DIR = _TEST_BACKUPS

_TEST_UPLOADS = tempfile.mkdtemp(suffix='_inv_bkimg_uploads')
_orig_uploads_fn = bk._uploads_dir
bk._uploads_dir = lambda: _TEST_UPLOADS

import app as flask_app_module
flask_app = flask_app_module.app
flask_app.config['TESTING'] = True
flask_app.config['UPLOAD_FOLDER'] = _TEST_UPLOADS

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

CSRF = 'test-csrf-bkimg'

def post(c, url, data=None):
    d = dict(data or {})
    d['csrf_token'] = CSRF
    return c.post(url, data=d, follow_redirects=True)

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

# Minimal 1×1 PNG
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


def _place_photo(product_id: int, filename: str, content: bytes = PNG_1X1) -> str:
    """Write a fake product photo file under the test uploads dir."""
    prod_dir = os.path.join(_TEST_UPLOADS, str(product_id))
    os.makedirs(prod_dir, exist_ok=True)
    path = os.path.join(prod_dir, filename)
    with open(path, 'wb') as f:
        f.write(content)
    return path


def run():
    global _PASS, _FAIL, _FAILURES
    _PASS = _FAIL = 0
    _FAILURES = []

    init_db()

    # Disable auto-backup
    conn = sqlite3.connect(_TEST_DB)
    conn.execute('UPDATE settings SET auto_backup_enabled=0')
    conn.commit()
    conn.close()

    # ── Seed a product with 2 photos directly in the DB ───────────────────
    section('Setup — seed product with photos in DB and on disk')

    conn = sqlite3.connect(_TEST_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO categories (name) VALUES ('Cam')")
    cat_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
    conn.execute("""
        INSERT INTO products (product_code, name, category_id, sale_price, cost_price,
                              min_stock_level, is_active)
        VALUES ('BK0001','Backup Camera',?,1000000,500000,0,1)
    """, (cat_id,))
    prod_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
    conn.execute("INSERT INTO product_images (product_id, filename, sort_order) VALUES (?,?,?)",
                 (prod_id, 'front.png', 0))
    conn.execute("INSERT INTO product_images (product_id, filename, sort_order) VALUES (?,?,?)",
                 (prod_id, 'back.png', 1))
    conn.commit()
    conn.close()

    front_path = _place_photo(prod_id, 'front.png')
    back_path  = _place_photo(prod_id, 'back.png')
    check(os.path.exists(front_path), 'front.png placed on disk')
    check(os.path.exists(back_path),  'back.png placed on disk')

    # ── 1. Backup includes photos ──────────────────────────────────────────
    section('Backup includes product photos')

    filename = bk.create_backup('manual')
    check(filename.endswith('.zip'), 'Backup ZIP created')

    zip_path = os.path.join(_TEST_BACKUPS, filename)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        info = json.loads(zf.read('backup_info.json'))

    photo_entries = [n for n in names
                     if n.startswith('uploads/products/') and not n.endswith('/')]
    check(len(photo_entries) == 2,
          f'ZIP contains 2 photo entries (found {len(photo_entries)}): {photo_entries}')
    check(any('front.png' in e for e in photo_entries), 'front.png is in ZIP')
    check(any('back.png' in e for e in photo_entries),  'back.png is in ZIP')

    # ── 2. photos_count matches actual file count ─────────────────────────
    section('backup_info.json photos_count is accurate')

    check(info.get('photos_count') == 2,
          f'photos_count == 2 (got {info.get("photos_count")})')

    # ── 3. Restore extracts photos to disk ───────────────────────────────
    section('Restore extracts photos to disk')

    # Delete photos on disk to simulate a fresh install restoring from backup
    shutil.rmtree(_TEST_UPLOADS)
    os.makedirs(_TEST_UPLOADS)
    check(not os.path.exists(front_path), 'Photos cleared before restore')

    bk.restore_backup(filename)

    check(os.path.exists(front_path), 'front.png restored on disk')
    check(os.path.exists(back_path),  'back.png restored on disk')

    with open(front_path, 'rb') as f:
        restored_bytes = f.read()
    check(restored_bytes == PNG_1X1, 'Restored photo content matches original')

    # ── 4. DB product_images rows intact after restore ────────────────────
    section('DB product_images rows survive restore')

    count = db_count("SELECT COUNT(*) FROM product_images WHERE product_id=?", (prod_id,))
    check(count == 2, f'product_images has 2 rows after restore (got {count})')

    # ── 5. Old backup without uploads/ is restored cleanly ───────────────
    section('Old backup without uploads/ entries restores cleanly')

    old_zip = os.path.join(_TEST_BACKUPS, 'manual_old_no_photos_test.zip')
    with zipfile.ZipFile(old_zip, 'w') as zf:
        # Write only inventory.db and backup_info.json (no uploads/)
        zf.write(_TEST_DB, 'inventory.db')
        zf.writestr('backup_info.json', json.dumps({
            'schema_version': db_module.SCHEMA_VERSION,
            'backup_type': 'manual',
        }))

    # Wipe uploads dir before restore
    shutil.rmtree(_TEST_UPLOADS)
    os.makedirs(_TEST_UPLOADS)

    try:
        bk.restore_backup('manual_old_no_photos_test.zip')
        ok('Old backup (no photos) restores without exception')
    except Exception as e:
        fail('Old backup (no photos) restores without exception', str(e))

    remaining_files = []
    for root, dirs, files in os.walk(_TEST_UPLOADS):
        remaining_files.extend(files)
    check(remaining_files == [],
          f'No orphan photos created when restoring a no-photo backup (found: {remaining_files})')

    # ── 6. Backup with no photos has photos_count = 0 ─────────────────────
    section('Backup with empty uploads dir has photos_count = 0')

    shutil.rmtree(_TEST_UPLOADS)
    os.makedirs(_TEST_UPLOADS)

    empty_fname = bk.create_backup('manual')
    with zipfile.ZipFile(os.path.join(_TEST_BACKUPS, empty_fname)) as zf:
        info2 = json.loads(zf.read('backup_info.json'))
    check(info2.get('photos_count') == 0,
          f'photos_count = 0 for backup with no images (got {info2.get("photos_count")})')

    # ── 7. DB Reset clears product_images table ───────────────────────────
    section('DB reset clears product_images table')

    # Seed a photo row directly
    conn = sqlite3.connect(_TEST_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO categories (name) VALUES ('Rst')")
    rc_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
    conn.execute(
        "INSERT INTO products (product_code, name, category_id, sale_price, cost_price, min_stock_level, is_active) "
        "VALUES ('RST001','Reset Prod',?,0,0,0,1)", (rc_id,)
    )
    rp_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()['id']
    conn.execute("INSERT INTO product_images (product_id, filename, sort_order) VALUES (?,?,?)",
                 (rp_id, 'rst.png', 0))
    conn.commit()
    conn.close()

    img_before = db_count("SELECT COUNT(*) FROM product_images")
    check(img_before > 0, f'product_images has rows before reset ({img_before})')

    with flask_app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
            sess['role'] = 'admin'
            sess['csrf_token'] = CSRF
        r = post(c, '/settings/reset', {'confirmation': 'RESET'})
        has(r, 'reset', 'DB reset flash message shown')

    img_after = db_count("SELECT COUNT(*) FROM product_images")
    check(img_after == 0, f'product_images cleared by DB reset (count={img_after})')

    return _PASS, _FAIL, _FAILURES


if __name__ == '__main__':
    passed, failed, failures = run()

    for d in [_TEST_BACKUPS, _TEST_UPLOADS]:
        shutil.rmtree(d, ignore_errors=True)
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
    print(f'{color}{passed}/{total} backup-image tests passed.\033[0m')
    sys.exit(0 if failed == 0 else 1)
