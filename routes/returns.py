from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db, generate_code
from auth import login_required
from datetime import datetime

returns_bp = Blueprint('returns', __name__, url_prefix='/returns')


@returns_bp.route('/')
@login_required
def returns_list():
    db = get_db()
    try:
        cursor = db.cursor()

        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page

        # Search
        search = request.args.get('search', '').strip()

        # Build query
        query = '''
            SELECT r.id, r.return_code, r.original_order_id, r.return_date, r.return_reason,
                   o.order_code, c.name as customer_name, c.customer_code
            FROM returns r
            JOIN orders o ON r.original_order_id = o.id
            JOIN customers c ON o.customer_id = c.id
            WHERE 1=1
        '''
        params = []

        if search:
            query += ' AND (r.return_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        query += ' ORDER BY r.return_date DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])

        cursor.execute(query, params)
        returns = cursor.fetchall()

        # Get total count
        count_query = '''SELECT COUNT(*) as count FROM returns r
                       JOIN orders o ON r.original_order_id = o.id
                       JOIN customers c ON o.customer_id = c.id
                       WHERE 1=1'''
        count_params = []
        if search:
            count_query += ' AND (r.return_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        total_pages = (total + per_page - 1) // per_page

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/returns_table.html', returns=returns, page=page, total_pages=total_pages)

        return render_template('returns.html', returns=returns, page=page, total_pages=total_pages, search=search)
    finally:
        db.close()


@returns_bp.route('/new')
@login_required
def new_return():
    db = get_db()
    try:
        cursor = db.cursor()

        # Get completed orders only
        cursor.execute('''
            SELECT o.id, o.order_code, c.name as customer_name
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.order_status = 'completed'
            ORDER BY o.order_date DESC
        ''')
        orders = cursor.fetchall()

        return render_template('return_form.html', orders=orders, return_data=None, order_items=None)
    finally:
        db.close()


@returns_bp.route('/new/items/<int:order_id>')
@login_required
def get_order_items(order_id):
    """Get items from a specific order for return selection (HTMX endpoint)."""
    db = get_db()
    try:
        cursor = db.cursor()

        # Get order and customer info
        cursor.execute('''
            SELECT o.id, o.order_code, c.name as customer_name, c.customer_code
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
        ''', (order_id,))
        order = cursor.fetchone()

        if not order:
            return '', 404

        # Get order items
        cursor.execute('''
            SELECT oi.id, oi.product_id, p.product_code, p.name, oi.quantity, oi.unit_price, oi.line_total
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (order_id,))
        items = cursor.fetchall()

        return render_template('partials/return_items_form.html', order=order, items=items)
    finally:
        db.close()


@returns_bp.route('/', methods=['POST'])
@login_required
def create_return():
    db = get_db()
    try:
        order_id = request.form.get('order_id', type=int)
        return_reason = request.form.get('return_reason', '').strip()
        restore_inventory = request.form.get('restore_inventory') == 'on'

        if not order_id:
            flash('Order is required', 'error')
            return redirect(url_for('returns.new_return'))

        # Parse items from form: items[0][order_item_id], items[0][product_id], items[0][quantity], items[0][refund_amount]
        items = []
        index = 0
        while f'items[{index}][order_item_id]' in request.form:
            order_item_id = request.form.get(f'items[{index}][order_item_id]', type=int)
            product_id = request.form.get(f'items[{index}][product_id]', type=int)
            quantity = request.form.get(f'items[{index}][quantity]', type=int)
            refund_amount = request.form.get(f'items[{index}][refund_amount]', type=float)

            if order_item_id and product_id and quantity and refund_amount:
                items.append({
                    'order_item_id': order_item_id,
                    'product_id': product_id,
                    'quantity': quantity,
                    'refund_amount': refund_amount
                })
            index += 1

        if not items:
            flash('At least one item is required', 'error')
            return redirect(url_for('returns.new_return'))

        # Generate return code
        return_code = generate_code('TR')

        cursor = db.cursor()
        try:
            # Create return
            cursor.execute('''
                INSERT INTO returns (return_code, original_order_id, return_reason)
                VALUES (?, ?, ?)
            ''', (return_code, order_id, return_reason))
            db.commit()

            # Get return ID
            cursor.execute('SELECT last_insert_rowid() as id')
            return_id = cursor.fetchone()['id']

            # Create return items
            total_refund = 0
            for item in items:
                cursor.execute('''
                    INSERT INTO return_items (return_id, order_item_id, product_id, quantity, refund_amount)
                    VALUES (?, ?, ?, ?, ?)
                ''', (return_id, item['order_item_id'], item['product_id'], item['quantity'], item['refund_amount']))
                total_refund += item['refund_amount']

                # Optionally restore inventory
                if restore_inventory:
                    # Restore to most recent lot for this product
                    cursor.execute('''
                        UPDATE inventory
                        SET remaining_quantity = remaining_quantity + ?
                        WHERE id = (
                            SELECT id FROM inventory
                            WHERE product_id = ?
                            ORDER BY intake_date DESC
                            LIMIT 1
                        )
                    ''', (item['quantity'], item['product_id']))

            db.commit()
            flash(f'Return "{return_code}" created successfully with {len(items)} item(s)', 'success')
            return redirect(url_for('returns.return_detail', id=return_id))

        except Exception as e:
            db.rollback()
            flash(f'Error creating return: {str(e)}', 'error')
            return redirect(url_for('returns.new_return'))

    finally:
        db.close()


@returns_bp.route('/<int:id>')
@login_required
def return_detail(id):
    db = get_db()
    try:
        cursor = db.cursor()

        # Get return (include customer_id for template link)
        cursor.execute('''
            SELECT r.*, o.order_code, o.order_date, o.customer_id,
                   c.name as customer_name, c.customer_code
            FROM returns r
            JOIN orders o ON r.original_order_id = o.id
            JOIN customers c ON o.customer_id = c.id
            WHERE r.id = ?
        ''', (id,))
        return_data = cursor.fetchone()

        if not return_data:
            flash('Return not found', 'error')
            return redirect(url_for('returns.returns_list'))

        # Get return items
        cursor.execute('''
            SELECT ri.id, ri.order_item_id, ri.product_id, p.product_code, p.name,
                   ri.quantity, ri.refund_amount, oi.unit_price
            FROM return_items ri
            JOIN products p ON ri.product_id = p.id
            JOIN order_items oi ON ri.order_item_id = oi.id
            WHERE ri.return_id = ?
        ''', (id,))
        items = cursor.fetchall()

        # Calculate total refund
        total_refund = sum(item['refund_amount'] for item in items)

        return render_template('return_detail.html', return_data=return_data, items=items, total_refund=total_refund)
    finally:
        db.close()
