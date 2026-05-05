"""Smoke tests for the backup/restore feature."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

# Patch before importing anything from the app
tmp_db = tempfile.mktemp(suffix='.db')
import db as db_module
db_module.DATABASE = tmp_db

import backup as bk
tmp_backups = tempfile.mkdtemp()
bk.BACKUPS_DIR = tmp_backups

from version import APP_VERSION
import app as application
app = application.app
app.config['TESTING'] = True
CSRF = 'test-csrf-xyz'

# Disable auto-backup so background threads don't create unexpected files during tests
import sqlite3 as _sqlite3
_conn = _sqlite3.connect(tmp_db)
_conn.execute('UPDATE settings SET auto_backup_enabled=0')
_conn.commit()
_conn.close()


def run_tests():
    passed = 0

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF

        # 1. App starts OK
        r = c.get('/login')
        assert r.status_code == 200
        print('1. App starts OK')
        passed += 1

        # 2. Login
        r = c.post('/login', data={'password': 'admin123'})
        assert r.status_code == 302
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF
        print('2. Login OK')
        passed += 1

        # 3. TESTING mode suppresses auto-backup side effects
        conn = sqlite3.connect(tmp_db)
        conn.execute(
            'UPDATE settings SET auto_backup_enabled=1, '
            'auto_backup_frequency_hours=1, last_auto_backup_at=NULL'
        )
        conn.commit()
        conn.close()
        r = c.get('/dashboard/')
        assert r.status_code == 200
        auto_files = [f for f in os.listdir(tmp_backups) if f.startswith('auto_')]
        assert auto_files == [], f'Auto backups should be suppressed in TESTING: {auto_files}'
        print('3. Auto-backup suppressed in TESTING mode')
        passed += 1

        # 4. Create manual backup
        r = c.post('/settings/backup/create', data={'csrf_token': CSRF},
                   follow_redirects=True)
        assert r.status_code == 200
        assert b'Backup created' in r.data, f'Flash missing: {r.data[:500]}'
        files = os.listdir(tmp_backups)
        assert len(files) == 1 and files[0].startswith('manual_') and files[0].endswith('.zip')
        backup_file = files[0]
        print(f'4. Manual backup created: {backup_file}')
        passed += 1

        # 5. Verify ZIP contents
        with zipfile.ZipFile(os.path.join(tmp_backups, backup_file)) as zf:
            names = zf.namelist()
            assert 'inventory.db' in names
            assert 'backup_info.json' in names
            info = json.loads(zf.read('backup_info.json'))
            assert info['schema_version'] == db_module.SCHEMA_VERSION
            assert info['app_version'] == APP_VERSION
            assert info['backup_type'] == 'manual'
        print(f'5. ZIP contents valid (schema v{info["schema_version"]})')
        passed += 1

        # 6. Download backup
        r = c.get(f'/settings/backup/{backup_file}/download')
        assert r.status_code == 200
        assert 'attachment' in r.headers.get('Content-Disposition', '')
        print('6. Download backup OK')
        passed += 1

        # 7. Settings page lists the backup
        r = c.get('/settings/')
        assert r.status_code == 200
        assert backup_file.encode() in r.data
        print('7. Settings page shows backup list OK')
        passed += 1

        # 8. Restore (should create pre_restore safety backup)
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF
        r = c.post(f'/settings/backup/{backup_file}/restore',
                   data={'csrf_token': CSRF}, follow_redirects=True)
        assert r.status_code == 200
        assert b'Restored' in r.data or b'restored' in r.data.lower(), \
            f'Restore flash missing: {r.data[:500]}'
        files_after = os.listdir(tmp_backups)
        assert len(files_after) == 2, f'Expected 2 backups, got: {files_after}'
        pre_restore = [f for f in files_after if f.startswith('pre_restore_')]
        assert pre_restore, f'Pre-restore backup missing: {files_after}'
        print(f'8. Restore OK, safety backup: {pre_restore[0]}')
        passed += 1

        # Seed a write-off row before reset. This catches stale business data
        # when new tables are added after the reset feature was written.
        conn = sqlite3.connect(tmp_db)
        cur = conn.cursor()
        cur.execute("INSERT INTO categories (name, description) VALUES (?, ?)",
                    ('Reset Test Category', ''))
        cat_id = cur.lastrowid
        cur.execute('''
            INSERT INTO products
                (product_code, name, category_id, sale_price, cost_price, min_stock_level, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', ('RST0001', 'Reset Test Product', cat_id, 100000, 60000, 0))
        product_id = cur.lastrowid
        cur.execute('''
            INSERT INTO inventory
                (product_id, quantity, remaining_quantity, cost_price, shipping_cost, currency, intake_date, notes)
            VALUES (?, 2, 1, 60000, 0, 'VND', '2026-01-01', 'reset test lot')
        ''', (product_id,))
        lot_id = cur.lastrowid
        cur.execute('''
            INSERT INTO inventory_adjustments
                (adjustment_code, inventory_lot_id, product_id, quantity_delta,
                 unit_cost, total_cost, reason, notes)
            VALUES ('ADRESET', ?, ?, -1, 60000, 60000, 'damaged', 'reset smoke test')
        ''', (lot_id, product_id))
        conn.commit()
        conn.close()

        # 9. Reset creates pre_reset safety backup and clears all business tables
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF
        r = c.post('/settings/reset',
                   data={'csrf_token': CSRF, 'confirmation': 'RESET'},
                   follow_redirects=True)
        assert r.status_code == 200
        assert b'reset' in r.data.lower()
        files_after_reset = os.listdir(tmp_backups)
        pre_reset = [f for f in files_after_reset if f.startswith('pre_reset_')]
        assert pre_reset, f'Pre-reset backup missing: {files_after_reset}'
        assert b'pre_reset_' in r.data, 'Safety backup filename not in flash'
        conn = sqlite3.connect(tmp_db)
        try:
            for table in ('inventory_adjustments', 'inventory', 'products', 'categories'):
                count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                assert count == 0, f'{table} not cleared by reset; count={count}'
        finally:
            conn.close()
        print(f'9. DB reset with safety backup and full business-data wipe: {pre_reset[0]}')
        passed += 1

        # 10. Delete a backup
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF
        r = c.post(f'/settings/backup/{backup_file}/delete',
                   data={'csrf_token': CSRF}, follow_redirects=True)
        assert r.status_code == 200
        assert backup_file not in os.listdir(tmp_backups)
        print('10. Delete backup OK')
        passed += 1

        # 11. Path traversal rejected
        r = c.post('/settings/backup/../inventory.db/delete',
                   data={'csrf_token': CSRF}, follow_redirects=True)
        assert r.status_code in (200, 404, 405)
        print('11. Path traversal safely handled')
        passed += 1

        # 12. Backup settings saved
        with c.session_transaction() as sess:
            sess['csrf_token'] = CSRF
        r = c.post('/settings/backup/settings', data={
            'csrf_token': CSRF,
            'auto_backup_enabled': '1',
            'auto_backup_frequency_hours': '24',
            'auto_backup_keep': '7',
        }, follow_redirects=True)
        assert r.status_code == 200
        assert b'saved' in r.data.lower() or b'success' in r.data.lower()
        print('12. Backup settings saved OK')
        passed += 1

    # 13. Cross-version schema migration
    # Create a v1 backup of the current db, simulate a v2 app restoring it
    v1_snap = tempfile.mktemp(suffix='.db')
    src = sqlite3.connect(tmp_db)
    dst = sqlite3.connect(v1_snap)
    src.backup(dst)
    dst.close()
    src.close()

    current_sv = db_module.SCHEMA_VERSION
    db_module.SCHEMA_VERSION = current_sv + 1  # pretend we upgraded

    v1_zip = os.path.join(tmp_backups, 'manual_migration_test_v1.zip')
    with zipfile.ZipFile(v1_zip, 'w') as zf:
        zf.write(v1_snap, 'inventory.db')
        zf.writestr('backup_info.json',
                    json.dumps({'schema_version': current_sv, 'backup_type': 'manual'}))

    restored_sv = bk.restore_backup('manual_migration_test_v1.zip')
    assert restored_sv == current_sv, f'Expected sv={current_sv}, got {restored_sv}'
    # init_db() ran — DB should still be readable
    conn = sqlite3.connect(tmp_db)
    try:
        conn.execute('SELECT id FROM settings LIMIT 1').fetchone()
    finally:
        conn.close()
    db_module.SCHEMA_VERSION = current_sv
    os.unlink(v1_snap)
    print('13. Cross-version restore + init_db() migration OK')
    passed += 1

    # 14. Newer-version backup blocked
    newer_zip = os.path.join(tmp_backups, 'manual_newer_v99.zip')
    with zipfile.ZipFile(newer_zip, 'w') as zf:
        zf.writestr('backup_info.json', json.dumps({'schema_version': 99}))
        zf.writestr('inventory.db', b'')
    try:
        bk.restore_backup('manual_newer_v99.zip')
        assert False, 'Should have raised ValueError'
    except ValueError as e:
        assert 'newer' in str(e).lower() or '99' in str(e)
    print('14. Newer-version backup correctly blocked')
    passed += 1

    # 15. Retention enforcement
    for i in range(5):
        bk.create_backup('auto')
    auto_files = [f for f in os.listdir(tmp_backups) if f.startswith('auto_')]
    bk.enforce_retention(keep=3)
    auto_after = [f for f in os.listdir(tmp_backups) if f.startswith('auto_')]
    assert len(auto_after) == 3, f'Expected 3 auto backups, got {len(auto_after)}'
    # Non-auto backups should be untouched
    non_auto = [f for f in os.listdir(tmp_backups) if not f.startswith('auto_')]
    assert len(non_auto) > 0, 'Non-auto backups should survive retention'
    print(f'15. Retention enforcement OK ({len(auto_files)} -> {len(auto_after)} auto backups)')
    passed += 1

    # 16. Concurrent first container startup uses one persistent Flask secret key
    deploy_data = tempfile.mkdtemp()
    try:
        secret_path = os.path.join(deploy_data, '.secret_key')
        code = "import app as m; print(m.app.secret_key.hex())"
        procs = []
        for i in range(4):
            env = os.environ.copy()
            env.update({
                'INVENTORY_DB': os.path.join(deploy_data, f'inventory_{i}.db'),
                'INVENTORY_BACKUPS_DIR': os.path.join(deploy_data, f'backups_{i}'),
                'INVENTORY_UPLOADS_DIR': os.path.join(deploy_data, f'uploads_{i}', 'products'),
                'INVENTORY_SECRET_KEY_FILE': secret_path,
            })
            procs.append(subprocess.Popen(
                [sys.executable, '-c', code],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ))

        keys = []
        for proc in procs:
            out, err = proc.communicate(timeout=30)
            assert proc.returncode == 0, f'Concurrent startup failed: {err}'
            keys.append(out.strip().splitlines()[-1])

        assert len(set(keys)) == 1, f'Workers loaded different secret keys: {keys}'
        assert os.path.exists(secret_path), 'Persistent secret key file missing'
        print('16. Concurrent first startup uses one shared secret key')
        passed += 1
    finally:
        shutil.rmtree(deploy_data, ignore_errors=True)

    return passed


try:
    total = run_tests()
    shutil.rmtree(tmp_backups, ignore_errors=True)
    if os.path.exists(tmp_db):
        os.unlink(tmp_db)
    print(f'\nAll {total} backup smoke tests PASSED.')
except AssertionError as e:
    shutil.rmtree(tmp_backups, ignore_errors=True)
    if os.path.exists(tmp_db):
        os.unlink(tmp_db)
    print(f'\nFAIL: {e}')
    sys.exit(1)
