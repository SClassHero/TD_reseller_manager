"""
backup.py — Backup and restore for inventory_app_v5.

Backup format: ZIP archive named  {type}_{YYYYMMDD_HHMMSS}_v{schema_version}.zip

ZIP contents:
  inventory.db        — SQLite DB (copied via SQLite Online Backup API, WAL-safe)
  uploads/products/   — Product photos
  backup_info.json    — {schema_version, app_version, created_at, backup_type, photos_count}

Types:  auto | manual | pre_reset | pre_restore

Cross-version restore:
  backup_sv == current  → direct restore
  backup_sv <  current  → restore + init_db() applies all missing migrations automatically
  backup_sv >  current  → blocked; user must upgrade the app first

See SCHEMA_CHANGELOG.md for version history and rules.
"""
import json
import logging
import os
import shutil
import sqlite3
import threading
import zipfile
from datetime import datetime

_log = logging.getLogger(__name__)

# One lock shared across backup and restore so they never run concurrently.
_backup_lock = threading.Lock()

BACKUPS_DIR = os.environ.get(
    'INVENTORY_BACKUPS_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
)


def _db_path():
    """Return absolute path to inventory.db, respecting test overrides of db.DATABASE."""
    from db import DATABASE
    return DATABASE if os.path.isabs(DATABASE) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), DATABASE
    )


def _uploads_dir():
    return os.environ.get(
        'INVENTORY_UPLOADS_DIR',
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     'static', 'uploads', 'products')
    )


def create_backup(backup_type='auto'):
    """
    Create a ZIP backup of the DB and product photos.
    Thread-safe (acquires _backup_lock).
    Returns the backup filename on success. Raises on error.
    """
    from db import SCHEMA_VERSION
    from version import APP_VERSION

    os.makedirs(BACKUPS_DIR, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime('%Y%m%d_%H%M%S') + f'_{now.microsecond // 1000:03d}'
    filename = f'{backup_type}_{timestamp}_v{SCHEMA_VERSION}.zip'
    filepath = os.path.join(BACKUPS_DIR, filename)
    tmp_db = filepath + '.tmp.db'

    with _backup_lock:
        try:
            # Safe DB copy via SQLite Online Backup API (handles WAL correctly)
            src = sqlite3.connect(_db_path())
            dst = sqlite3.connect(tmp_db)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            udir = _uploads_dir()
            photos_count = sum(len(f) for _, _, f in os.walk(udir)) if os.path.exists(udir) else 0

            with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(tmp_db, 'inventory.db')

                if os.path.exists(udir):
                    for root, _, files in os.walk(udir):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            arcname = os.path.join(
                                'uploads', 'products',
                                os.path.relpath(fpath, udir)
                            )
                            zf.write(fpath, arcname)

                zf.writestr('backup_info.json', json.dumps({
                    'schema_version': SCHEMA_VERSION,
                    'app_version': APP_VERSION,
                    'created_at': datetime.now().isoformat(),
                    'backup_type': backup_type,
                    'photos_count': photos_count,
                }, indent=2))
        finally:
            if os.path.exists(tmp_db):
                os.unlink(tmp_db)

    _log.info('Backup created: %s', filename)
    return filename


def restore_backup(filename):
    """
    Restore from a ZIP backup in BACKUPS_DIR.
    Acquires _backup_lock (waits for any running backup first).
    Calls init_db() after restore when schema version differs.
    Returns the backup's schema_version (int).
    Raises FileNotFoundError or ValueError on problems.
    """
    from db import SCHEMA_VERSION, init_db

    filepath = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f'Backup not found: {filename}')

    db_path = _db_path()
    udir = _uploads_dir()

    with _backup_lock:
        with zipfile.ZipFile(filepath, 'r') as zf:
            # Read metadata (gracefully handle legacy backups without it)
            try:
                info = json.loads(zf.read('backup_info.json'))
                backup_sv = int(info.get('schema_version', 1))
            except Exception:
                backup_sv = 1

            if backup_sv > SCHEMA_VERSION:
                raise ValueError(
                    f'This backup is from schema v{backup_sv} (a newer app version). '
                    f'This app is on schema v{SCHEMA_VERSION}. '
                    f'Update the app files before restoring.'
                )

            # Extract DB to temp file
            tmp_path = db_path + '.restore_tmp'
            with open(tmp_path, 'wb') as f:
                f.write(zf.read('inventory.db'))

            # Restore photos (clear existing first to remove orphaned files)
            if os.path.exists(udir):
                shutil.rmtree(udir)
            os.makedirs(udir, exist_ok=True)

            photo_entries = [n for n in zf.namelist()
                             if n.startswith('uploads/products/') and not n.endswith('/')]
            for entry in photo_entries:
                rel = entry[len('uploads/products/'):]
                if not rel:
                    continue
                dest = os.path.join(udir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(entry) as src, open(dest, 'wb') as dst_f:
                    shutil.copyfileobj(src, dst_f)

        # Replace the live DB atomically
        try:
            os.replace(tmp_path, db_path)
        except OSError:
            if os.path.exists(db_path):
                os.unlink(db_path)
            os.rename(tmp_path, db_path)

    # Apply any schema migrations the backup is missing
    if backup_sv < SCHEMA_VERSION:
        init_db()
        _log.info('Schema migrated from v%d to v%d after restore', backup_sv, SCHEMA_VERSION)

    _log.info('Restore complete from %s (schema v%d)', filename, backup_sv)
    return backup_sv


def list_backups():
    """Return list of backup info dicts, sorted newest first."""
    if not os.path.exists(BACKUPS_DIR):
        return []
    result = []
    for fname in os.listdir(BACKUPS_DIR):
        if not fname.endswith('.zip'):
            continue
        fpath = os.path.join(BACKUPS_DIR, fname)
        try:
            stat = os.stat(fpath)
        except OSError:
            continue

        if fname.startswith('pre_reset_'):
            btype = 'pre-reset'
        elif fname.startswith('pre_restore_'):
            btype = 'pre-restore'
        elif fname.startswith('manual_'):
            btype = 'manual'
        elif fname.startswith('auto_'):
            btype = 'auto'
        else:
            btype = 'other'

        # Parse schema version from filename suffix _v{N}.zip
        sv = '?'
        base = fname[:-4]
        if '_v' in base:
            sv = base.rsplit('_v', 1)[-1]

        result.append({
            'filename': fname,
            'type': btype,
            'size_bytes': stat.st_size,
            'size_mb': round(stat.st_size / 1024 / 1024, 1),
            'modified_at': datetime.fromtimestamp(stat.st_mtime),
            'schema_version': sv,
        })

    return sorted(result, key=lambda x: x['modified_at'], reverse=True)


def enforce_retention(keep=12):
    """
    Delete oldest auto-backups when count exceeds `keep`.
    Never deletes manual, pre_reset, or pre_restore backups.
    """
    if not os.path.exists(BACKUPS_DIR):
        return
    auto_backups = sorted(
        f for f in os.listdir(BACKUPS_DIR)
        if f.startswith('auto_') and f.endswith('.zip')
    )
    while len(auto_backups) > keep:
        oldest = auto_backups.pop(0)
        try:
            os.unlink(os.path.join(BACKUPS_DIR, oldest))
            _log.info('Retention: removed old auto-backup %s', oldest)
        except OSError as exc:
            _log.warning('Could not remove old backup %s: %s', oldest, exc)
