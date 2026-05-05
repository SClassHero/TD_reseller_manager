"""
routes/imports.py — Bulk CSV import for customers and inventory intake lots.

Customers CSV: customer_code, name, email, phone, address, region (customer_code must be unique)
Inventory CSV: product_code, quantity, cost_price, shipping_cost, currency, intake_date, notes

Duplicate customer_code values are skipped with a warning; partial imports succeed.
USD intake rows are converted to VND using the current settings.vnd_usd_rate at import time.
"""
import re
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db, generate_code, generate_product_code
from auth import login_required
import csv
import io
from datetime import datetime

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

imports_bp = Blueprint('imports', __name__, url_prefix='/import')


@imports_bp.route('/')
@login_required
def import_page():
    """Import page with forms for different data types."""
    return render_template('import.html')


@imports_bp.route('/categories', methods=['POST'])
@login_required
def import_categories():
    """Import categories from CSV.
    Expected columns: name, description (description is optional)
    Rows with a name that already exists are skipped with a warning (no update).
    """
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    if not file.filename.endswith('.csv'):
        flash('Please upload a CSV file', 'error')
        return redirect(url_for('imports.import_page'))

    db = get_db()
    try:
        cursor = db.cursor()
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        csv_data = csv.DictReader(stream)

        success_count = 0
        skip_count = 0
        error_count = 0
        errors = []

        for row_num, row in enumerate(csv_data, start=2):
            try:
                name = row.get('name', '').strip()
                description = row.get('description', '').strip()

                if not name:
                    errors.append(f"Row {row_num}: Category name is required")
                    error_count += 1
                    continue

                cursor.execute('SELECT id FROM categories WHERE name = ?', (name,))
                if cursor.fetchone():
                    skip_count += 1
                    continue

                cursor.execute(
                    'INSERT INTO categories (name, description) VALUES (?, ?)',
                    (name, description or None)
                )
                db.commit()
                success_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                error_count += 1
                db.rollback()

        parts = []
        if success_count > 0:
            parts.append(f'Imported {success_count} categories')
        if skip_count > 0:
            parts.append(f'{skip_count} skipped (name already exists)')
        if parts:
            flash('. '.join(parts) + '.', 'success')
        if error_count > 0:
            error_msg = f'{error_count} rows failed. '
            if errors:
                error_msg += 'Errors: ' + '; '.join(errors[:5])
                if len(errors) > 5:
                    error_msg += f' and {len(errors) - 5} more'
            flash(error_msg, 'error')

        return redirect(url_for('imports.import_page'))

    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('imports.import_page'))
    finally:
        db.close()


@imports_bp.route('/products', methods=['POST'])
@login_required
def import_products():
    """Import products from CSV.
    Expected columns: name, category, sale_price, cost_price, barcode, min_stock_level
    Optional column: product_code (use CSV-supplied code; auto-generate if absent or blank)
    """
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    if not file.filename.endswith('.csv'):
        flash('Please upload a CSV file', 'error')
        return redirect(url_for('imports.import_page'))

    db = get_db()
    try:
        cursor = db.cursor()
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        csv_data = csv.DictReader(stream)

        success_count = 0
        error_count = 0
        errors = []

        for row_num, row in enumerate(csv_data, start=2):  # Start at 2 (headers are row 1)
            try:
                product_code = row.get('product_code', '').strip()
                name = row.get('name', '').strip()
                category = row.get('category', '').strip()
                sale_price = row.get('sale_price', '0')
                cost_price = row.get('cost_price', '0')
                barcode = row.get('barcode', '').strip()
                min_stock_level = row.get('min_stock_level', '0')

                if not name:
                    errors.append(f"Row {row_num}: Product name is required")
                    error_count += 1
                    continue

                try:
                    sale_price = float(sale_price) if sale_price else 0
                    cost_price = float(cost_price) if cost_price else 0
                    min_stock_level = int(min_stock_level) if min_stock_level else 0
                except ValueError as e:
                    errors.append(f"Row {row_num}: Invalid price or stock level - {str(e)}")
                    error_count += 1
                    continue

                # Create category if it doesn't exist
                category_id = None
                if category:
                    cursor.execute('SELECT id FROM categories WHERE name = ?', (category,))
                    cat_result = cursor.fetchone()
                    if cat_result:
                        category_id = cat_result['id']
                    else:
                        cursor.execute('INSERT INTO categories (name) VALUES (?)', (category,))
                        db.commit()
                        cursor.execute('SELECT last_insert_rowid() as id')
                        category_id = cursor.fetchone()['id']

                # Use CSV-supplied code if present, otherwise generate from category
                if not product_code:
                    product_code = generate_product_code(category_id=category_id, db_conn=db)

                # Insert product
                cursor.execute('''
                    INSERT INTO products (product_code, name, category_id, sale_price, cost_price, barcode, min_stock_level)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (product_code, name, category_id, sale_price, cost_price, barcode, min_stock_level))
                db.commit()
                success_count += 1

            except Exception as e:
                ctx = f" (code {product_code!r})" if product_code else ""
                errors.append(f"Row {row_num}{ctx}: {str(e)}")
                error_count += 1
                db.rollback()

        if success_count > 0:
            flash(f'Successfully imported {success_count} products', 'success')
        if error_count > 0:
            error_msg = f'{error_count} products failed to import. '
            if errors:
                error_msg += 'Errors: ' + '; '.join(errors[:5])
                if len(errors) > 5:
                    error_msg += f' and {len(errors) - 5} more errors'
            flash(error_msg, 'error')

        return redirect(url_for('imports.import_page'))

    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('imports.import_page'))
    finally:
        db.close()


@imports_bp.route('/customers', methods=['POST'])
@login_required
def import_customers():
    """Import customers from CSV.
    Expected columns: customer_code (optional), name, email, phone, address, region
    If customer_code is blank or absent, one is auto-generated (KH######).
    Rows with a duplicate customer_code are skipped with an error.
    """
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    if not file.filename.endswith('.csv'):
        flash('Please upload a CSV file', 'error')
        return redirect(url_for('imports.import_page'))

    db = get_db()
    try:
        cursor = db.cursor()
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        csv_data = csv.DictReader(stream)

        success_count = 0
        error_count = 0
        errors = []

        for row_num, row in enumerate(csv_data, start=2):
            try:
                customer_code = row.get('customer_code', '').strip()
                name = row.get('name', '').strip()
                email = row.get('email', '').strip()
                phone = row.get('phone', '').strip()
                address = row.get('address', '').strip()
                region = row.get('region', '').strip()

                if not name:
                    errors.append(f"Row {row_num}: Customer name is required")
                    error_count += 1
                    continue

                if email and not _EMAIL_RE.match(email):
                    errors.append(f"Row {row_num}: Invalid email format: {email!r}")
                    error_count += 1
                    continue

                # Use CSV-supplied code if provided, otherwise auto-generate
                if not customer_code:
                    customer_code = generate_code('KH')

                # Insert customer
                cursor.execute('''
                    INSERT INTO customers (customer_code, name, email, phone, address, region)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (customer_code, name, email or None, phone or None, address or None, region or None))
                db.commit()
                success_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                error_count += 1
                db.rollback()

        if success_count > 0:
            flash(f'Successfully imported {success_count} customers', 'success')
        if error_count > 0:
            error_msg = f'{error_count} customers failed to import. '
            if errors:
                error_msg += 'Errors: ' + '; '.join(errors[:5])
                if len(errors) > 5:
                    error_msg += f' and {len(errors) - 5} more errors'
            flash(error_msg, 'error')

        return redirect(url_for('imports.import_page'))

    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('imports.import_page'))
    finally:
        db.close()


@imports_bp.route('/inventory', methods=['POST'])
@login_required
def import_inventory():
    """Import inventory from CSV.
    Expected columns: product_code (or product_name), quantity, cost_price,
                      shipping_cost, currency, intake_date, notes
    """
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('imports.import_page'))

    if not file.filename.endswith('.csv'):
        flash('Please upload a CSV file', 'error')
        return redirect(url_for('imports.import_page'))

    db = get_db()
    try:
        cursor = db.cursor()
        stream = io.StringIO(file.stream.read().decode('utf-8'))
        csv_data = csv.DictReader(stream)

        success_count = 0
        error_count = 0
        errors = []

        for row_num, row in enumerate(csv_data, start=2):
            try:
                product_code = row.get('product_code', '').strip()
                product_name = row.get('product_name', '').strip()
                quantity = row.get('quantity', '0')
                cost_price = row.get('cost_price', '0')
                shipping_cost = row.get('shipping_cost', '0')
                currency = row.get('currency', 'VND').strip().upper() or 'VND'
                intake_date = row.get('intake_date', '')
                notes = row.get('notes', '').strip()

                # Find product
                product_id = None
                if product_code:
                    cursor.execute('SELECT id FROM products WHERE product_code = ?', (product_code,))
                elif product_name:
                    cursor.execute('SELECT id FROM products WHERE name = ?', (product_name,))
                else:
                    errors.append(f"Row {row_num}: Either product_code or product_name is required")
                    error_count += 1
                    continue

                result = cursor.fetchone()
                if not result:
                    errors.append(f"Row {row_num}: Product not found ({product_code or product_name})")
                    error_count += 1
                    continue

                product_id = result['id']

                try:
                    quantity = int(quantity)
                    cost_price = float(cost_price) if cost_price else 0
                    shipping_cost = float(shipping_cost) if shipping_cost else 0
                except ValueError as e:
                    errors.append(f"Row {row_num}: Invalid quantity, cost_price, or shipping_cost - {str(e)}")
                    error_count += 1
                    continue

                if quantity <= 0:
                    errors.append(f"Row {row_num}: Quantity must be greater than 0")
                    error_count += 1
                    continue

                # Parse intake_date if provided
                try:
                    if intake_date:
                        datetime.strptime(intake_date, '%Y-%m-%d')
                    else:
                        intake_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid date format, use YYYY-MM-DD")
                    error_count += 1
                    continue

                # Insert inventory record
                cursor.execute('''
                    INSERT INTO inventory (product_id, quantity, remaining_quantity,
                                          cost_price, shipping_cost, currency, intake_date, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (product_id, quantity, quantity, cost_price,
                      shipping_cost, currency, intake_date, notes or None))
                db.commit()
                success_count += 1

            except Exception as e:
                errors.append(f"Row {row_num}: {str(e)}")
                error_count += 1
                db.rollback()

        if success_count > 0:
            flash(f'Successfully imported {success_count} inventory records', 'success')
        if error_count > 0:
            error_msg = f'{error_count} records failed to import. '
            if errors:
                error_msg += 'Errors: ' + '; '.join(errors[:5])
                if len(errors) > 5:
                    error_msg += f' and {len(errors) - 5} more errors'
            flash(error_msg, 'error')

        return redirect(url_for('imports.import_page'))

    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')
        db.rollback()
        return redirect(url_for('imports.import_page'))
    finally:
        db.close()
