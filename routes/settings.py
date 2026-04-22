from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from db import get_db
from auth import login_required
import shutil
import os
from datetime import datetime

settings_bp = Blueprint('settings', __name__, url_prefix='/settings')


@settings_bp.route('/')
@login_required
def settings_page():
    """Show settings form with current values."""
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT * FROM settings LIMIT 1')
        settings = cursor.fetchone()

        if not settings:
            # Create default settings if none exist
            cursor.execute('''
                INSERT INTO settings (app_name, password_hash, default_currency, vnd_usd_rate)
                VALUES (?, ?, ?, ?)
            ''', ('Inventory Management', 'admin123', 'VND', 24000.0))
            db.commit()
            cursor.execute('SELECT * FROM settings LIMIT 1')
            settings = cursor.fetchone()

        return render_template('settings.html', settings=settings)
    finally:
        db.close()


@settings_bp.route('/', methods=['POST'])
@login_required
def update_settings():
    """Update app settings."""
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

        cursor = db.cursor()
        cursor.execute('''
            UPDATE settings
            SET app_name = ?, default_currency = ?, vnd_usd_rate = ?
            WHERE id = 1
        ''', (app_name, default_currency, vnd_usd_rate))
        db.commit()

        flash('Settings updated successfully', 'success')
        return redirect(url_for('settings.settings_page'))

    except Exception as e:
        flash(f'Error updating settings: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()


@settings_bp.route('/password', methods=['POST'])
@login_required
def change_password():
    """Change admin password."""
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

        cursor = db.cursor()
        cursor.execute('SELECT password_hash FROM settings LIMIT 1')
        settings = cursor.fetchone()

        if not settings or settings['password_hash'] != old_password:
            flash('Current password is incorrect', 'error')
            return redirect(url_for('settings.settings_page'))

        cursor.execute('''
            UPDATE settings
            SET password_hash = ?
            WHERE id = 1
        ''', (new_password,))
        db.commit()

        flash('Password changed successfully', 'success')
        return redirect(url_for('settings.settings_page'))

    except Exception as e:
        flash(f'Error changing password: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()


@settings_bp.route('/backup', methods=['POST'])
@login_required
def backup_database():
    """Download database backup."""
    try:
        # Create backup with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'inventory_backup_{timestamp}.db'
        backup_path = f'/tmp/{backup_filename}'

        # Copy database to backup location
        shutil.copy('inventory.db', backup_path)

        # Send the backup file
        return send_file(
            backup_path,
            as_attachment=True,
            download_name=backup_filename
        )

    except Exception as e:
        flash(f'Error creating backup: {str(e)}', 'error')
        return redirect(url_for('settings.settings_page'))


@settings_bp.route('/reset', methods=['POST'])
@login_required
def reset_database():
    """Reset database (with confirmation)."""
    db = get_db()
    try:
        confirmation = request.form.get('confirmation', '').strip()

        if confirmation != 'RESET':
            flash('Please type "RESET" to confirm database reset', 'error')
            return redirect(url_for('settings.settings_page'))

        cursor = db.cursor()

        # Delete all data (but keep schema)
        tables_to_clear = [
            'order_items',
            'return_items',
            'payments',
            'refunds',
            'returns',
            'orders',
            'inventory',
            'customers',
            'products',
            'categories'
        ]

        for table in tables_to_clear:
            cursor.execute(f'DELETE FROM {table}')

        # Reset auto-increment sequences for tables with codes
        # (SQLite doesn't have explicit sequences, so we clear the tables)
        cursor.execute('DELETE FROM sqlite_sequence')

        db.commit()

        flash('Database has been reset. All data has been deleted.', 'success')
        return redirect(url_for('settings.settings_page'))

    except Exception as e:
        flash(f'Error resetting database: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('settings.settings_page'))
    finally:
        db.close()
