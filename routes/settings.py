"""
routes/settings.py — App configuration, account management, backup/restore, and DB reset.

Password storage: admin password is mirrored in BOTH users.password_hash AND
settings.password_hash for backward compatibility with older backups.

Recovery code: 20-char base32 string stored as a Werkzeug hash in settings.recovery_code_hash.
On successful recovery: password is reset AND the code is rotated immediately so it
cannot be reused. The new code is shown once in the recovery_success page.

Auto-backup: configurable frequency (daily/weekly) stored in settings; triggered by
app.py's before_request hook in a background thread.
"""
import base64
import os
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from werkzeug.security import check_password_hash, generate_password_hash
from db import get_db
from auth import login_required
from backup import (
    create_backup, restore_backup, list_backups, enforce_retention, BACKUPS_DIR
)

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')

# Allowed frequencies to prevent arbitrary values being stored
_VALID_FREQ_HOURS = {24, 168}


@settings_bp.route('/')
@login_required
def settings_page():
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT * FROM settings LIMIT 1')
        settings = cursor.fetchone()

        if not settings:
            cursor.execute('''
                INSERT INTO settings (app_name, password_hash, default_currency, vnd_usd_rate)
                VALUES (?, ?, ?, ?)
            ''', ('Inventory Management', generate_password_hash('admin123'), 'VND', 24000.0))
            db.commit()
            cursor.execute('SELECT * FROM settings LIMIT 1')
            settings = cursor.fetchone()

        backups = list_backups()
        limited_user = cursor.execute(
            "SELECT id, username, is_active, created_at FROM users "
            "WHERE role = 'limited' ORDER BY id LIMIT 1"
        ).fetchone()
        return render_template('settings.html', settings=settings, backups=backups,
                               limited_user=limited_user)
    finally:
        db.close()


@settings_bp.route('/', methods=['POST'])
@login_required
def update_settings():
    db = get_db()
    try:
        app_name = request.form.get('app_name', '').strip()
        default_currency = request.form.get('default_currency', '').strip()
        vnd_usd_rate = request.form.get('vnd_usd_rate', '24000', type=float)

        if not app_name:
            flash('App name is required', 'error')
            return redirect(url_for('settings.settings_page'))
        if not default_currency:
            flash('Default currency is required', 'error')
            return redirect(url_for('settings.settings_page'))
        if vnd_usd_rate <= 0:
            flash('Exchange rate must be greater than 0', 'error')
            return redirect(url_for('settings.settings_page'))

        db.execute(
            'UPDATE settings SET app_name = ?, default_currency = ?, vnd_usd_rate = ? WHERE id = 1',
            (app_name, default_currency, vnd_usd_rate)
        )
        db.commit()
        flash('Settings updated successfully', 'success')
        return redirect(url_for('settings.settings_page'))
    except Exception as e:
        db.rollback()
        flash(f'Error updating settings: {str(e)}', 'error')
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()


@settings_bp.route('/password', methods=['POST'])
@login_required
def change_password():
    db = get_db()
    try:
        old_password = request.form.get('old_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not old_password or not new_password or not confirm_password:
            flash('All password fields are required', 'error')
            return redirect(url_for('settings.settings_page'))
        if new_password != confirm_password:
            flash('New passwords do not match', 'error')
            return redirect(url_for('settings.settings_page'))
        if len(new_password) < 4:
            flash('Password must be at least 4 characters', 'error')
            return redirect(url_for('settings.settings_page'))

        row = db.execute('SELECT password_hash FROM settings LIMIT 1').fetchone()
        if not row or not check_password_hash(row['password_hash'], old_password):
            flash('Current password is incorrect', 'error')
            return redirect(url_for('settings.settings_page'))

        new_hash = generate_password_hash(new_password)
        db.execute('UPDATE settings SET password_hash = ? WHERE id = 1', (new_hash,))
        db.execute("UPDATE users SET password_hash = ?, is_active = 1 WHERE role = 'admin'",
                   (new_hash,))
        db.commit()
        flash('Password changed successfully', 'success')
        return redirect(url_for('settings.settings_page'))
    except Exception as e:
        db.rollback()
        flash(f'Error changing password: {str(e)}', 'error')
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()


@settings_bp.route('/limited-account', methods=['POST'])
@login_required
def limited_account():
    username = (request.form.get('username') or 'limited').strip()
    password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    is_active = 1 if request.form.get('is_active') else 0

    if not username:
        flash('Limited account username is required.', 'error')
        return redirect(url_for('settings.settings_page'))
    if username.lower() == 'admin':
        flash('Limited account username cannot be "admin".', 'error')
        return redirect(url_for('settings.settings_page'))
    if password and password != confirm_password:
        flash('Limited account passwords do not match.', 'error')
        return redirect(url_for('settings.settings_page'))
    if password and len(password) < 4:
        flash('Limited account password must be at least 4 characters.', 'error')
        return redirect(url_for('settings.settings_page'))

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM users WHERE role = 'limited' ORDER BY id LIMIT 1"
        ).fetchone()

        if existing:
            if password:
                db.execute(
                    'UPDATE users SET username = ?, password_hash = ?, is_active = ? WHERE id = ?',
                    (username, generate_password_hash(password), is_active, existing['id'])
                )
            else:
                db.execute(
                    'UPDATE users SET username = ?, is_active = ? WHERE id = ?',
                    (username, is_active, existing['id'])
                )
        else:
            if not password:
                flash('Set a password to create the limited account.', 'error')
                return redirect(url_for('settings.settings_page'))
            db.execute(
                'INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, ?)',
                (username, generate_password_hash(password), 'limited', is_active)
            )

        db.commit()
        flash('Limited access account saved.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error saving limited account: {str(e)}', 'error')
    finally:
        db.close()

    return redirect(url_for('settings.settings_page'))


@settings_bp.route('/recovery-code', methods=['POST'])
@login_required
def generate_recovery_code():
    """Generate or regenerate the in-app recovery code and display it once."""
    db = get_db()
    try:
        raw = secrets.token_bytes(12)
        code = base64.b32encode(raw).decode()[:20]  # 20 chars, no padding
        code_fmt = '-'.join(code[i:i+4] for i in range(0, 20, 4))
        code_hash = generate_password_hash(code)

        db.execute('UPDATE settings SET recovery_code_hash = ? WHERE id = 1', (code_hash,))
        db.commit()
        return render_template('recovery_code_reveal.html', code=code_fmt)
    except Exception as e:
        flash(f'Error generating recovery code: {str(e)}', 'error')
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()


# ── Backup & Restore ─────────────────────────────────────────────────────────

def _safe_filename(filename):
    """Return True if filename is a safe backup ZIP name (no path traversal)."""
    return (filename.endswith('.zip')
            and '/' not in filename
            and '\\' not in filename
            and '..' not in filename)


@settings_bp.route('/backup/create', methods=['POST'])
@login_required
def backup_create():
    try:
        filename = create_backup('manual')
        flash(f'Backup created: {filename}', 'success')
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'error')
    return redirect(url_for('settings.settings_page'))


@settings_bp.route('/backup/<filename>/download')
@login_required
def backup_download(filename):
    if not _safe_filename(filename):
        flash('Invalid backup filename.', 'error')
        return redirect(url_for('settings.settings_page'))
    filepath = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(filepath):
        flash('Backup file not found.', 'error')
        return redirect(url_for('settings.settings_page'))
    return send_file(filepath, as_attachment=True, download_name=filename)


@settings_bp.route('/backup/<filename>/restore', methods=['POST'])
@login_required
def backup_restore(filename):
    if not _safe_filename(filename):
        flash('Invalid backup filename.', 'error')
        return redirect(url_for('settings.settings_page'))
    try:
        # Safety backup of current state before overwriting
        safety = create_backup('pre_restore')
        backup_sv = restore_backup(filename)
        from db import SCHEMA_VERSION
        if backup_sv < SCHEMA_VERSION:
            flash(
                f'Restored from schema v{backup_sv} backup — schema upgraded to v{SCHEMA_VERSION}. '
                f'Safety backup saved as: {safety}',
                'success'
            )
        else:
            flash(f'Restored successfully from {filename}. Safety backup: {safety}', 'success')
    except ValueError as e:
        flash(str(e), 'error')
    except Exception as e:
        flash(f'Restore failed: {str(e)}', 'error')
    return redirect(url_for('settings.settings_page'))


@settings_bp.route('/backup/<filename>/delete', methods=['POST'])
@login_required
def backup_delete(filename):
    if not _safe_filename(filename):
        flash('Invalid backup filename.', 'error')
        return redirect(url_for('settings.settings_page'))
    filepath = os.path.join(BACKUPS_DIR, filename)
    try:
        os.unlink(filepath)
        flash(f'Backup deleted: {filename}', 'success')
    except FileNotFoundError:
        flash('Backup file not found.', 'error')
    except Exception as e:
        flash(f'Error deleting backup: {str(e)}', 'error')
    return redirect(url_for('settings.settings_page'))


@settings_bp.route('/backup/settings', methods=['POST'])
@login_required
def backup_settings():
    enabled = 1 if request.form.get('auto_backup_enabled') else 0
    freq = request.form.get('auto_backup_frequency_hours', 168, type=int)
    keep = request.form.get('auto_backup_keep', 12, type=int)

    if freq not in _VALID_FREQ_HOURS:
        freq = 168
    keep = max(1, min(50, keep))

    db = get_db()
    try:
        db.execute(
            'UPDATE settings SET auto_backup_enabled=?, auto_backup_frequency_hours=?, '
            'auto_backup_keep=? WHERE id=1',
            (enabled, freq, keep)
        )
        db.commit()
        flash('Backup settings saved.', 'success')
    except Exception as e:
        db.rollback()
        flash(f'Error saving backup settings: {str(e)}', 'error')
    finally:
        db.close()
    return redirect(url_for('settings.settings_page'))


# ── Database Reset ────────────────────────────────────────────────────────────

@settings_bp.route('/reset', methods=['POST'])
@login_required
def reset_database():
    db = get_db()
    try:
        confirmation = request.form.get('confirmation', '').strip()
        if confirmation != 'RESET':
            flash('Please type "RESET" to confirm database reset', 'error')
            return redirect(url_for('settings.settings_page'))

        # Auto-backup before wiping — this is the safety net for accidental resets
        safety = None
        try:
            safety = create_backup('pre_reset')
        except Exception as backup_err:
            flash(f'Warning: could not create safety backup before reset ({backup_err}). Proceeding anyway.', 'warning')

        tables_to_clear = [
            'order_allocations', 'inventory_adjustments',
            'order_items', 'return_items', 'payments', 'refunds',
            'returns', 'orders', 'inventory', 'product_images',
            'customers', 'products', 'categories',
        ]
        cursor = db.cursor()
        for table in tables_to_clear:
            cursor.execute(f'DELETE FROM {table}')
        cursor.execute('DELETE FROM sqlite_sequence')
        db.commit()

        msg = 'Database reset — all business data deleted.'
        if safety:
            msg += f' Safety backup saved as: {safety}'
        flash(msg, 'success')
        return redirect(url_for('settings.settings_page'))
    except Exception as e:
        db.rollback()
        flash(f'Error resetting database: {str(e)}', 'error')
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()
