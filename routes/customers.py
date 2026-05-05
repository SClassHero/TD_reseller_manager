"""
routes/customers.py — Customer CRUD.

customers.outstanding_debt is a stale cached column — never use it for display.
The list and detail queries compute live debt via:
  SUM(orders.total_amount) - SUM(payments.amount)
  for orders WHERE order_status IN ('processing', 'completed')

customers.total_spent is incremented when an order first moves to an active-sale status
(processing/completed) and decremented when it is reopened to draft or cancelled.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from db import get_db, generate_code
from auth import login_required
from list_utils import (
    PER_PAGE_OPTIONS,
    pagination_window,
    parse_list_controls,
    resolve_pagination,
    sort_direction_sql,
)

customers_bp = Blueprint('customers', __name__, url_prefix='/customers')

CUSTOMER_SORTS = {
    'name': 'c.name COLLATE NOCASE',
    'code': 'c.customer_code COLLATE NOCASE',
    'region': "COALESCE(c.region, '') COLLATE NOCASE",
    'spent': 'COALESCE(c.total_spent, 0)',
    'debt': 'outstanding_debt',
    'created': 'c.created_at',
}


@customers_bp.route('/generate-code')
@login_required
def generate_customer_code_api():
    """Return the next available auto-generated customer code."""
    code = generate_code('KH')
    return jsonify({'code': code})


@customers_bp.route('/')
@login_required
def customers_list():
    db = get_db()
    try:
        cursor = db.cursor()

        controls = parse_list_controls(
            request.args,
            CUSTOMER_SORTS,
            default_sort='name',
            default_direction='asc',
            default_per_page=25,
        )
        search = request.args.get('search', '').strip()
        sort = controls['sort']
        direction = controls['direction']
        per_page = controls['per_page']

        count_query = 'SELECT COUNT(*) as count FROM customers c WHERE c.is_active = 1'
        count_params = []
        if search:
            count_query += ' AND (c.name LIKE ? OR c.customer_code LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR c.region LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'])

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        page, total_pages, offset = resolve_pagination(controls['page'], per_page, total)

        # Build query — compute outstanding_debt dynamically from orders & payments
        # so edits to payments are immediately reflected.
        query = '''
            SELECT c.id,
                   c.customer_code as code,
                   c.name, c.email, c.phone, c.region,
                   c.total_spent, c.is_active, c.created_at,
                   COALESCE((
                       SELECT SUM(o.total_amount)
                       FROM orders o
                       WHERE o.customer_id = c.id AND o.order_status IN ('processing', 'completed')
                   ), 0) -
                   COALESCE((
                       SELECT SUM(p.amount)
                       FROM payments p
                       JOIN orders o ON p.order_id = o.id
                       WHERE o.customer_id = c.id AND o.order_status IN ('processing', 'completed')
                   ), 0) as outstanding_debt
            FROM customers c
            WHERE c.is_active = 1
        '''
        params = []

        if search:
            query += ' AND (c.name LIKE ? OR c.customer_code LIKE ? OR c.email LIKE ? OR c.phone LIKE ? OR c.region LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'])

        order_expr = CUSTOMER_SORTS[sort]
        query += f' ORDER BY {order_expr} {sort_direction_sql(direction)}, c.id ASC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])

        cursor.execute(query, params)
        customers = cursor.fetchall()

        pagination_params = {
            'search': search,
            'sort': sort,
            'direction': direction,
            'per_page': per_page,
        }

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/customers_table.html', customers=customers, page=page, total_pages=total_pages)

        return render_template('customers.html', customers=customers, page=page, total_pages=total_pages,
                               total=total, per_page=per_page, per_page_options=PER_PAGE_OPTIONS,
                               page_numbers=pagination_window(page, total_pages),
                               pagination_params=pagination_params,
                               search=search, sort=sort, direction=direction)
    finally:
        db.close()


@customers_bp.route('/new')
@login_required
def new_customer():
    suggested_code = generate_code('KH')
    return render_template('partials/customer_form.html', customer=None, suggested_code=suggested_code)


@customers_bp.route('/<int:id>/edit')
@login_required
def edit_customer(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT * FROM customers WHERE id = ?', (id,))
        customer = cursor.fetchone()
        if not customer:
            flash('Customer not found', 'error')
            return redirect(url_for('customers.customers_list'))
        return render_template('partials/customer_form.html',
                               customer=customer,
                               suggested_code=customer['customer_code'])
    finally:
        db.close()


@customers_bp.route('/<int:id>')
@login_required
def customer_detail(id):
    db = get_db()
    try:
        cursor = db.cursor()

        cursor.execute('SELECT * FROM customers WHERE id = ?', (id,))
        customer = cursor.fetchone()
        if not customer:
            flash('Customer not found', 'error')
            return redirect(url_for('customers.customers_list'))

        # Compute outstanding debt live (same formula as customers_list)
        cursor.execute('''
            SELECT
                COALESCE((SELECT SUM(o.total_amount) FROM orders o
                          WHERE o.customer_id = ? AND o.order_status IN ('processing', 'completed')), 0) -
                COALESCE((SELECT SUM(p.amount) FROM payments p
                          JOIN orders o ON p.order_id = o.id
                          WHERE o.customer_id = ? AND o.order_status IN ('processing', 'completed')), 0)
            AS live_debt
        ''', (id, id))
        live_debt = cursor.fetchone()['live_debt'] or 0.0
        customer = dict(customer)
        customer['outstanding_debt'] = live_debt

        # Get order history
        cursor.execute('''
            SELECT id, order_code, order_date, order_status, total_amount
            FROM orders
            WHERE customer_id = ?
            ORDER BY order_date DESC
        ''', (id,))
        orders = cursor.fetchall()

        return render_template('customer_detail.html', customer=customer, orders=orders)
    finally:
        db.close()


@customers_bp.route('/', methods=['POST'])
@login_required
def create_customer():
    db = get_db()
    try:
        name          = request.form.get('name', '').strip()
        customer_code = request.form.get('customer_code', '').strip().upper()
        email         = request.form.get('email', '').strip()
        phone         = request.form.get('phone', '').strip()
        address       = request.form.get('address', '').strip()
        region        = request.form.get('region', '').strip()

        if not name:
            flash('Customer name is required', 'error')
            return redirect(url_for('customers.new_customer'))

        # Auto-generate if left blank
        if not customer_code:
            customer_code = generate_code('KH')

        cursor = db.cursor()
        try:
            cursor.execute('''
                INSERT INTO customers (customer_code, name, email, phone, address, region)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (customer_code, name, email, phone, address, region))
            db.commit()
            flash(f'Customer "{name}" created with code {customer_code}', 'success')
        except Exception as e:
            db.rollback()
            if 'UNIQUE' in str(e).upper():
                flash(f'Customer code "{customer_code}" is already in use. Please choose a different code.', 'error')
            else:
                flash(f'Error creating customer: {str(e)}', 'error')
            # Redirect to list so the flash message renders in base.html
            return redirect(url_for('customers.customers_list'))

        return redirect(url_for('customers.customers_list'))
    finally:
        db.close()


@customers_bp.route('/<int:id>', methods=['POST'])
@login_required
def update_customer(id):
    db = get_db()
    try:
        name          = request.form.get('name', '').strip()
        customer_code = request.form.get('customer_code', '').strip().upper()
        email         = request.form.get('email', '').strip()
        phone         = request.form.get('phone', '').strip()
        address       = request.form.get('address', '').strip()
        region        = request.form.get('region', '').strip()

        if not name:
            flash('Customer name is required', 'error')
            return redirect(url_for('customers.edit_customer', id=id))

        # If somehow blank (shouldn't happen on edit), keep the existing code
        if not customer_code:
            cursor_tmp = db.cursor()
            cursor_tmp.execute('SELECT customer_code FROM customers WHERE id = ?', (id,))
            row = cursor_tmp.fetchone()
            customer_code = row['customer_code'] if row else generate_code('KH')

        cursor = db.cursor()
        try:
            cursor.execute('''
                UPDATE customers
                SET customer_code = ?, name = ?, email = ?, phone = ?, address = ?, region = ?
                WHERE id = ?
            ''', (customer_code, name, email, phone, address, region, id))
            db.commit()
            flash('Customer updated successfully', 'success')
        except Exception as e:
            db.rollback()
            if 'UNIQUE' in str(e).upper():
                flash(f'Customer code "{customer_code}" is already in use. Please choose a different code.', 'error')
            else:
                flash(f'Error updating customer: {str(e)}', 'error')
            return redirect(url_for('customers.customers_list'))

        return redirect(url_for('customers.customers_list'))
    finally:
        db.close()


@customers_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_customer(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('UPDATE customers SET is_active = 0 WHERE id = ?', (id,))
        db.commit()
        flash('Customer deleted successfully', 'success')
        return redirect(url_for('customers.customers_list'))
    finally:
        db.close()
