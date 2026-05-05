#!/usr/bin/env python3
"""
Password reset utility for inventory_app_v5.
Run this from the inventory_app_v5/ directory when you are locked out.

Usage:
    python reset_password.py
"""
import os
import sys
import getpass
import sqlite3

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('INVENTORY_DB', os.path.join(APP_DIR, 'inventory.db'))


def main():
    print('Inventory App — Password Reset')
    print('=' * 40)

    if not os.path.exists(DB_PATH):
        print(f'ERROR: Database not found at {DB_PATH}')
        print('Make sure you are running this from the inventory_app_v5/ directory.')
        sys.exit(1)

    # Use getpass so the new password is not echoed to the screen
    try:
        new_password = getpass.getpass('Enter new password: ')
        confirm    = getpass.getpass('Confirm new password: ')
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled.')
        sys.exit(0)

    if not new_password:
        print('ERROR: Password cannot be empty.')
        sys.exit(1)

    if new_password != confirm:
        print('ERROR: Passwords do not match.')
        sys.exit(1)

    if len(new_password) < 6:
        print('WARNING: Password is very short (less than 6 characters).')

    try:
        from werkzeug.security import generate_password_hash
    except ImportError:
        print('ERROR: werkzeug is not installed.')
        print('Run: pip install flask   (werkzeug comes with Flask)')
        sys.exit(1)

    hashed = generate_password_hash(new_password)

    try:
        db = sqlite3.connect(DB_PATH)
        cursor = db.cursor()
        cursor.execute('UPDATE settings SET password_hash = ? WHERE id = 1', (hashed,))
        if cursor.rowcount == 0:
            # Settings row doesn't exist yet — insert it
            cursor.execute(
                "INSERT INTO settings (password_hash) VALUES (?)", (hashed,)
            )
        db.commit()
        db.close()
    except sqlite3.Error as e:
        print(f'ERROR: Could not update database: {e}')
        sys.exit(1)

    print()
    print('Password reset successfully.')
    print('You can now log in with your new password.')


if __name__ == '__main__':
    main()
