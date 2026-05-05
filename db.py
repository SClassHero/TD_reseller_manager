"""
db.py — Database schema, migration helpers, and code generation.

Rules for future schema changes (critical — read before editing):
  1. New columns: always use _migrate_add_column(), never raw ALTER TABLE at module level.
  2. New tables: use CREATE TABLE IF NOT EXISTS.
  3. Structural changes: increment SCHEMA_VERSION and add an entry to SCHEMA_CHANGELOG.md.
  4. Never drop or rename columns without a data migration script.

SCHEMA_VERSION is embedded in backup filenames so old backups can be restored and
then upgraded automatically by init_db() applying all _migrate_add_column() calls.
"""
import sqlite3
import logging
import os
from pathlib import Path
from werkzeug.security import generate_password_hash

_log = logging.getLogger(__name__)

DATABASE = os.environ.get('INVENTORY_DB', 'inventory.db')

# Increment whenever the schema changes structurally (new table, new column, type change).
# Must match the version recorded in SCHEMA_CHANGELOG.md.
# Backup filenames and backup_info.json embed this value for cross-version restore.
SCHEMA_VERSION = 5

PREFIX_MAP = {
    'KH': ('customers', 'customer_code'),
    'HD': ('orders', 'order_code'),
    'SP': ('products', 'product_code'),
    'TR': ('returns', 'return_code'),
    'RF': ('refunds', 'refund_code'),
    'AD': ('inventory_adjustments', 'adjustment_code'),
}

MAX_PRODUCT_IMAGES = 3


def get_db():
    """Get database connection with Row factory."""
    db_path = DATABASE if os.path.isabs(DATABASE) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), DATABASE
    )
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass  # WAL not supported on all filesystems (e.g. some NAS FUSE mounts); safe to ignore
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    """Initialize database schema and seed default settings."""
    db = get_db()
    try:
        cursor = db.cursor()

        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_name TEXT DEFAULT 'Inventory Management',
                password_hash TEXT DEFAULT 'admin123',
                default_currency TEXT DEFAULT 'VND',
                vnd_usd_rate REAL DEFAULT 24000.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Users table. Admin password is also mirrored in settings.password_hash
        # for recovery/backup compatibility with earlier app versions.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'limited')),
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Categories table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Products table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category_id INTEGER,
                sale_price REAL,  -- reference only: pre-fills order form; NOT used in revenue calculations
                cost_price REAL,  -- reference only: pre-fills intake form; NOT used in FIFO COGS
                barcode TEXT,
                min_stock_level INTEGER DEFAULT 0,
                image_path TEXT,
                notes TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (category_id) REFERENCES categories(id)
            )
        ''')

        # Customers table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                address TEXT,
                region TEXT,
                total_spent REAL DEFAULT 0,      -- updated when order moves draft ↔ active sale
                outstanding_debt REAL DEFAULT 0, -- STALE CACHE: never use for display; always compute live
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Inventory table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,           -- original intake amount; negative = backorder/pre-order
                remaining_quantity INTEGER NOT NULL, -- live: decremented by FIFO, restored on order reopen
                cost_price REAL,                     -- fixed at intake; used per-unit in FIFO COGS
                currency TEXT DEFAULT 'VND',         -- source currency label (VND or USD)
                exchange_rate REAL DEFAULT 1.0,      -- rate used to convert USD cost to VND at intake time
                intake_date TIMESTAMP,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')

        # Orders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_code TEXT UNIQUE NOT NULL,
                customer_id INTEGER NOT NULL,
                order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                -- draft: no stock/accounting effect; editable/deletable
                -- processing/completed: active sale — FIFO stock reserved, counts in revenue/COGS/debt
                -- cancelled: terminal; cannot be reopened
                order_status TEXT CHECK (order_status IN ('draft', 'processing', 'completed', 'cancelled')) DEFAULT 'draft',
                payment_status TEXT CHECK (payment_status IN ('not_paid', 'partially_paid', 'fully_paid')) DEFAULT 'not_paid',
                subtotal REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,   -- must not exceed subtotal (enforced in routes/orders.py)
                shipping_fee REAL DEFAULT 0,
                shipping_paid_by TEXT DEFAULT 'customer' CHECK(shipping_paid_by IN ('customer','seller')),
                -- formula: subtotal - discount + shipping_fee (only when shipping_paid_by = 'customer')
                total_amount REAL DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        ''')

        # Order items table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                discount_percent REAL DEFAULT 0,
                line_total REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')

        # Order allocations table — tracks exactly which inventory lots were
        # consumed when an order is completed. Used to restore stock on reopen
        # and for accurate FIFO COGS calculation.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                inventory_lot_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity_allocated INTEGER NOT NULL,
                cost_price_at_sale REAL DEFAULT 0, -- AUTHORITATIVE COGS source: actual cost of the lot consumed
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
                FOREIGN KEY (inventory_lot_id) REFERENCES inventory(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')

        # Product images table (up to 3 per product)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        ''')

        # Payments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                payment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payment_method TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        ''')

        # Refunds table (return_id is optional — refunds can exist without a return)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refund_code TEXT UNIQUE NOT NULL,
                order_id INTEGER NOT NULL,
                return_id INTEGER,
                amount REAL NOT NULL,
                refund_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                refund_method TEXT,
                reason TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (return_id) REFERENCES returns(id)
            )
        ''')

        # Returns table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                return_code TEXT UNIQUE NOT NULL,
                original_order_id INTEGER NOT NULL,
                return_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                return_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (original_order_id) REFERENCES orders(id)
            )
        ''')

        # Return items table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS return_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id INTEGER NOT NULL,
                order_item_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                refund_amount REAL NOT NULL,
                restock_action TEXT DEFAULT 'none',
                restock_quantity INTEGER DEFAULT 0,
                restock_unit_cost REAL DEFAULT 0,
                restock_shipping_cost REAL DEFAULT 0,
                restock_inventory_lot_id INTEGER,
                FOREIGN KEY (return_id) REFERENCES returns(id) ON DELETE CASCADE,
                FOREIGN KEY (order_item_id) REFERENCES order_items(id),
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (restock_inventory_lot_id) REFERENCES inventory(id)
            )
        ''')

        # Inventory adjustments / write-offs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                adjustment_code TEXT UNIQUE NOT NULL,
                inventory_lot_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                return_item_id INTEGER,
                adjustment_type TEXT DEFAULT 'write_off',
                quantity_delta INTEGER NOT NULL,
                unit_cost REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                reason TEXT,
                notes TEXT,
                adjustment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (inventory_lot_id) REFERENCES inventory(id),
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (return_item_id) REFERENCES return_items(id)
            )
        ''')

        # ── Migrations for existing databases ──────────────────────────
        _ALLOWED_MIGRATE_TABLES = frozenset({
            'order_allocations', 'orders', 'refunds', 'inventory', 'products',
            'customers', 'categories', 'order_items', 'payments', 'returns',
            'return_items', 'inventory_adjustments', 'product_images', 'settings', 'users',
        })

        def _migrate_add_column(table, column, definition):
            """Safely add a column to an existing table if it doesn't already exist."""
            if table not in _ALLOWED_MIGRATE_TABLES:
                raise ValueError(f'Migration refused: unknown table {table!r}')
            try:
                cursor.execute(f'SELECT {column} FROM {table} LIMIT 1')
            except sqlite3.OperationalError:
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                except sqlite3.OperationalError as exc:
                    _log.warning('Migration skipped (%s.%s): %s', table, column, exc)

        _migrate_add_column('order_allocations', 'cost_price_at_sale', 'REAL DEFAULT 0')
        _migrate_add_column('orders', 'shipping_paid_by',
                            "TEXT DEFAULT 'customer' CHECK(shipping_paid_by IN ('customer','seller'))")
        _migrate_add_column('refunds', 'return_id', 'INTEGER REFERENCES returns(id)')
        _migrate_add_column('inventory', 'shipping_cost', 'REAL DEFAULT 0')  # inbound cost to warehouse; amortized per-unit into FIFO cost
        _migrate_add_column('products', 'product_code_custom', 'INTEGER DEFAULT 0')
        _migrate_add_column('settings', 'recovery_code_hash', 'TEXT')
        _migrate_add_column('settings', 'auto_backup_enabled', 'INTEGER DEFAULT 1')
        _migrate_add_column('settings', 'auto_backup_frequency_hours', 'INTEGER DEFAULT 168')
        _migrate_add_column('settings', 'auto_backup_keep', 'INTEGER DEFAULT 12')
        _migrate_add_column('settings', 'last_auto_backup_at', 'TEXT')
        _migrate_add_column('settings', 'schema_version', 'INTEGER DEFAULT 1')
        _migrate_add_column('return_items', 'restock_action', "TEXT DEFAULT 'none'")
        _migrate_add_column('return_items', 'restock_quantity', 'INTEGER DEFAULT 0')
        _migrate_add_column('return_items', 'restock_unit_cost', 'REAL DEFAULT 0')
        _migrate_add_column('return_items', 'restock_shipping_cost', 'REAL DEFAULT 0')
        _migrate_add_column('return_items', 'restock_inventory_lot_id', 'INTEGER REFERENCES inventory(id)')
        cursor.execute('''
            UPDATE return_items
            SET restock_quantity = quantity
            WHERE restock_inventory_lot_id IS NOT NULL
              AND COALESCE(restock_quantity, 0) = 0
        ''')

        # ── Indexes ──────────────────────────────────────────────────────
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_product_id ON inventory(product_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_order_alloc_order_id ON order_allocations(order_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_status_date ON orders(order_status, order_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_refunds_order_id ON refunds(order_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_adjustments_lot ON inventory_adjustments(inventory_lot_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_inventory_adjustments_product ON inventory_adjustments(product_id)')

        # Seed default settings if empty
        cursor.execute('SELECT COUNT(*) as count FROM settings')
        if cursor.fetchone()['count'] == 0:
            cursor.execute('''
                INSERT INTO settings (app_name, password_hash, default_currency, vnd_usd_rate)
                VALUES (?, ?, ?, ?)
            ''', ('Inventory Management', generate_password_hash('admin123'), 'VND', 24000.0))

        # Migrate any remaining plaintext passwords to hashed (one-time, safe to re-run)
        cursor.execute('SELECT id, password_hash FROM settings LIMIT 1')
        pw_row = cursor.fetchone()
        if pw_row and pw_row['password_hash']:
            stored = pw_row['password_hash']
            if not stored.startswith(('pbkdf2:', 'scrypt:', 'argon2:')):
                stored = generate_password_hash(stored)
                cursor.execute('UPDATE settings SET password_hash = ? WHERE id = ?',
                               (stored, pw_row['id']))

        # Seed the admin user from settings.password_hash for upgraded databases.
        cursor.execute('SELECT password_hash FROM settings LIMIT 1')
        pw_row = cursor.fetchone()
        admin_hash = pw_row['password_hash'] if pw_row else generate_password_hash('admin123')
        cursor.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        admin_user = cursor.fetchone()
        if not admin_user:
            try:
                cursor.execute(
                    'INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, ?)',
                    ('admin', admin_hash, 'admin', 1)
                )
            except sqlite3.IntegrityError:
                cursor.execute(
                    "UPDATE users SET role = 'admin', password_hash = ?, is_active = 1 WHERE username = 'admin'",
                    (admin_hash,)
                )

        cursor.execute('UPDATE settings SET schema_version = ? WHERE id = 1', (SCHEMA_VERSION,))

        # Ensure backups directory exists.
        backups_dir = os.environ.get(
            'INVENTORY_BACKUPS_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups')
        )
        os.makedirs(backups_dir, exist_ok=True)

        db.commit()
    finally:
        db.close()


def generate_code(prefix):
    """
    Generate auto-incrementing code with prefix.
    Example: prefix='KH' -> 'KH000001'
    """
    if prefix not in PREFIX_MAP:
        raise ValueError(f"Invalid prefix: {prefix}")

    table_name, column_name = PREFIX_MAP[prefix]
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute(f'SELECT MAX(CAST(SUBSTR({column_name}, LENGTH(?) + 1) AS INTEGER)) as max_num FROM {table_name}', (prefix,))
        row = cursor.fetchone()
        max_num = row['max_num'] if row['max_num'] is not None else 0
        new_num = max_num + 1
        return f"{prefix}{new_num:06d}"
    finally:
        db.close()


def generate_product_code(category_id=None, db_conn=None):
    """
    Generate a product code based on category name.
    Format: First 2 letters of category (uppercase) + 4-digit sequence.
    Example: Category 'Electronics' -> 'EL0001', 'EL0002', etc.
    Falls back to 'SP' prefix if no category given.
    Accepts an optional existing db connection to avoid nested connections.
    """
    close_db = False
    if db_conn is None:
        db_conn = get_db()
        close_db = True
    try:
        cursor = db_conn.cursor()
        if category_id:
            cursor.execute('SELECT name FROM categories WHERE id = ?', (category_id,))
            cat = cursor.fetchone()
            if cat and cat['name']:
                # Use first 2 letters, uppercase, strip non-alpha
                raw = ''.join(c for c in cat['name'] if c.isalpha())
                prefix = raw[:2].upper() if len(raw) >= 2 else raw.upper().ljust(2, 'X')
            else:
                prefix = 'SP'
        else:
            prefix = 'SP'

        # Find next sequence number for this prefix
        cursor.execute(
            "SELECT MAX(CAST(SUBSTR(product_code, ?) AS INTEGER)) as max_num "
            "FROM products WHERE product_code LIKE ?",
            (len(prefix) + 1, f'{prefix}%')
        )
        row = cursor.fetchone()
        max_num = row['max_num'] if row['max_num'] is not None else 0
        return f"{prefix}{(max_num + 1):04d}"
    finally:
        if close_db:
            db_conn.close()
