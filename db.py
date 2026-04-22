import sqlite3
import os
from pathlib import Path

DATABASE = 'inventory.db'

PREFIX_MAP = {
    'KH': ('customers', 'customer_code'),
    'HD': ('orders', 'order_code'),
    'SP': ('products', 'product_code'),
    'TR': ('returns', 'return_code'),
    'RF': ('refunds', 'refund_code'),
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
                sale_price REAL,
                cost_price REAL,
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
                total_spent REAL DEFAULT 0,
                outstanding_debt REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Inventory table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                remaining_quantity INTEGER NOT NULL,
                cost_price REAL,
                currency TEXT DEFAULT 'VND',
                exchange_rate REAL DEFAULT 1.0,
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
                order_status TEXT CHECK (order_status IN ('draft', 'processing', 'completed', 'cancelled')) DEFAULT 'draft',
                payment_status TEXT CHECK (payment_status IN ('not_paid', 'partially_paid', 'fully_paid')) DEFAULT 'not_paid',
                subtotal REAL DEFAULT 0,
                discount_amount REAL DEFAULT 0,
                shipping_fee REAL DEFAULT 0,
                shipping_paid_by TEXT DEFAULT 'customer' CHECK(shipping_paid_by IN ('customer','seller')),
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
                cost_price_at_sale REAL DEFAULT 0,
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

        # ── Migrations for existing databases ──────────────────────────
        def _migrate_add_column(table, column, definition):
            """Safely add a column if it doesn't exist."""
            try:
                cursor.execute(f'SELECT {column} FROM {table} LIMIT 1')
            except Exception:
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
                except Exception:
                    pass

        _migrate_add_column('order_allocations', 'cost_price_at_sale', 'REAL DEFAULT 0')
        _migrate_add_column('orders', 'shipping_paid_by',
                            "TEXT DEFAULT 'customer' CHECK(shipping_paid_by IN ('customer','seller'))")
        _migrate_add_column('refunds', 'return_id', 'INTEGER REFERENCES returns(id)')
        _migrate_add_column('inventory', 'shipping_cost', 'REAL DEFAULT 0')
        _migrate_add_column('products', 'product_code_custom', 'INTEGER DEFAULT 0')

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
                FOREIGN KEY (return_id) REFERENCES returns(id) ON DELETE CASCADE,
                FOREIGN KEY (order_item_id) REFERENCES order_items(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        ''')

        # Seed default settings if empty
        cursor.execute('SELECT COUNT(*) as count FROM settings')
        if cursor.fetchone()['count'] == 0:
            cursor.execute('''
                INSERT INTO settings (app_name, password_hash, default_currency, vnd_usd_rate)
                VALUES (?, ?, ?, ?)
            ''', ('Inventory Management', 'admin123', 'VND', 24000.0))

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
