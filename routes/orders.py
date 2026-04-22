from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db, generate_code
from auth import login_required
from datetime import datetime

orders_bp = Blueprint('orders', __name__, url_prefix='/orders')


# ─────────────────────────────────────────────
#  INVENTORY HELPERS
# ─────────────────────────────────────────────

def _parse_items_from_form(form):
    """
    Parse order items from a submitted form using indexed keys:
      items[0][product_id], items[0][quantity], items[0][unit_price], items[0][discount_percent]
    Returns a list of dicts, or empty list if none found.
    """
    items = []
    index = 0
    while f'items[{index}][product_id]' in form:
        product_id    = form.get(f'items[{index}][product_id]',    type=int)
        quantity      = form.get(f'items[{index}][quantity]',       type=int)
        unit_price    = form.get(f'items[{index}][unit_price]',     type=float)
        discount_pct  = form.get(f'items[{index}][discount_percent]', 0, type=float)
        if product_id and quantity and unit_price is not None:
            items.append({
                'product_id':      product_id,
                'quantity':        quantity,
                'unit_price':      unit_price,
                'discount_percent': discount_pct,
            })
        index += 1
    return items


def _deduct_inventory_fifo(cursor, order_id, items):
    """
    Deduct inventory using FIFO (oldest intake first) for each item in the order.
    Records every deduction in order_allocations (with cost_price) so it can be
    reversed on reopen and used for accurate COGS calculation.
    Returns a list of warning strings for products with insufficient stock.
    """
    warnings = []
    for item in items:
        needed = item['quantity']
        product_id = item['product_id']

        # Get lots ordered by intake date (oldest first)
        cursor.execute('''
            SELECT id, remaining_quantity, COALESCE(cost_price, 0) as cost_price
            FROM inventory
            WHERE product_id = ? AND remaining_quantity > 0
            ORDER BY intake_date ASC, id ASC
        ''', (product_id,))
        lots = cursor.fetchall()

        allocated = 0
        for lot in lots:
            if needed <= 0:
                break
            take = min(needed, lot['remaining_quantity'])
            cursor.execute(
                'UPDATE inventory SET remaining_quantity = remaining_quantity - ? WHERE id = ?',
                (take, lot['id'])
            )
            cursor.execute('''
                INSERT INTO order_allocations
                    (order_id, inventory_lot_id, product_id, quantity_allocated, cost_price_at_sale)
                VALUES (?, ?, ?, ?, ?)
            ''', (order_id, lot['id'], product_id, take, lot['cost_price']))
            allocated += take
            needed    -= take

        if needed > 0:
            # Insufficient stock — warn but don't block (backorder scenario)
            cursor.execute('SELECT name FROM products WHERE id = ?', (product_id,))
            p = cursor.fetchone()
            pname = p['name'] if p else f'product #{product_id}'
            warnings.append(
                f'"{pname}": needed {item["quantity"]}, only {item["quantity"] - needed} in stock. '
                f'{needed} unit(s) will show as backorder.'
            )

    return warnings


def _restore_inventory_from_allocations(cursor, order_id):
    """
    Reverse every allocation recorded for this order — adds quantities back to
    the exact lots they were originally taken from, then deletes the records.
    """
    cursor.execute('''
        SELECT inventory_lot_id, quantity_allocated
        FROM order_allocations
        WHERE order_id = ?
    ''', (order_id,))
    allocations = cursor.fetchall()

    for alloc in allocations:
        cursor.execute(
            'UPDATE inventory SET remaining_quantity = remaining_quantity + ? WHERE id = ?',
            (alloc['quantity_allocated'], alloc['inventory_lot_id'])
        )

    cursor.execute('DELETE FROM order_allocations WHERE order_id = ?', (order_id,))


# ─────────────────────────────────────────────
#  LIST
# ─────────────────────────────────────────────

@orders_bp.route('/')
@login_required
def orders_list():
    db = get_db()
    try:
        cursor = db.cursor()

        page       = request.args.get('page', 1, type=int)
        per_page   = 50
        offset     = (page - 1) * per_page
        status_filter = request.args.get('status', '')
        search     = request.args.get('search', '').strip()

        query = '''
            SELECT o.id, o.order_code, o.order_date, o.order_status, o.payment_status,
                   o.total_amount, c.name as customer_name, c.customer_code
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE 1=1
        '''
        params = []
        if status_filter:
            query  += ' AND o.order_status = ?'
            params.append(status_filter)
        if search:
            query  += ' AND (o.order_code LIKE ? OR c.name LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%'])
        query  += ' ORDER BY o.order_date DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        cursor.execute(query, params)
        orders = cursor.fetchall()

        count_query = 'SELECT COUNT(*) as count FROM orders o JOIN customers c ON o.customer_id = c.id WHERE 1=1'
        count_params = []
        if status_filter:
            count_query  += ' AND o.order_status = ?';  count_params.append(status_filter)
        if search:
            count_query  += ' AND (o.order_code LIKE ? OR c.name LIKE ?)';  count_params.extend([f'%{search}%', f'%{search}%'])
        cursor.execute(count_query, count_params)
        total       = cursor.fetchone()['count']
        total_pages = (total + per_page - 1) // per_page

        if request.headers.get('HX-Request'):
            return render_template('partials/orders_table.html', orders=orders, page=page, total_pages=total_pages)

        return render_template('orders.html', orders=orders, page=page, total_pages=total_pages,
                               status_filter=status_filter, search=search)
    finally:
        db.close()


# ─────────────────────────────────────────────
#  NEW ORDER FORM
# ─────────────────────────────────────────────

@orders_bp.route('/new')
@login_required
def new_order():
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT id, customer_code, name FROM customers WHERE is_active = 1 ORDER BY name')
        customers = cursor.fetchall()
        cursor.execute('SELECT id, product_code, name, sale_price FROM products WHERE is_active = 1 ORDER BY name')
        products = cursor.fetchall()
        today = datetime.now().strftime('%Y-%m-%d')
        return render_template('order_form.html', customers=customers, products=products,
                               order=None, order_items=[], today=today)
    finally:
        db.close()


# ─────────────────────────────────────────────
#  CREATE ORDER
# ─────────────────────────────────────────────

@orders_bp.route('/', methods=['POST'])
@login_required
def create_order():
    db = get_db()
    try:
        customer_id      = request.form.get('customer_id', type=int)
        discount_amount  = request.form.get('discount_amount', 0, type=float)
        shipping_fee     = request.form.get('shipping_fee',    0, type=float)
        shipping_paid_by = request.form.get('shipping_paid_by', 'customer').strip()
        notes            = request.form.get('notes', '').strip()
        order_date_str   = request.form.get('order_date', '').strip()

        if shipping_paid_by not in ('customer', 'seller'):
            shipping_paid_by = 'customer'

        if not customer_id:
            flash('Customer is required', 'error')
            return redirect(url_for('orders.new_order'))

        items = _parse_items_from_form(request.form)
        if not items:
            flash('At least one item is required', 'error')
            return redirect(url_for('orders.new_order'))

        order_code = generate_code('HD')

        # Use the date entered in the form, or fall back to local now
        if order_date_str:
            try:
                order_date = datetime.strptime(order_date_str, '%Y-%m-%d').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                order_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            order_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        cursor = db.cursor()
        try:
            # Calculate line totals first (accounting for per-item discounts)
            for item in items:
                item['line_total'] = item['quantity'] * item['unit_price'] * (1 - item['discount_percent'] / 100)

            subtotal = sum(item['line_total'] for item in items)
            # total_amount = what the customer owes
            # Only add shipping when customer pays; seller-paid shipping is a cost, not customer revenue
            if shipping_paid_by == 'customer':
                total_amount = subtotal - discount_amount + shipping_fee
            else:
                total_amount = subtotal - discount_amount

            cursor.execute('''
                INSERT INTO orders
                    (order_code, customer_id, order_date, discount_amount, shipping_fee,
                     shipping_paid_by, subtotal, total_amount, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (order_code, customer_id, order_date, discount_amount, shipping_fee,
                  shipping_paid_by, subtotal, total_amount, notes))
            db.commit()

            cursor.execute('SELECT last_insert_rowid() as id')
            order_id = cursor.fetchone()['id']

            for item in items:
                cursor.execute('''
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_percent, line_total)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (order_id, item['product_id'], item['quantity'],
                      item['unit_price'], item['discount_percent'], item['line_total']))

            db.commit()
            flash(f'Order "{order_code}" created successfully', 'success')
            return redirect(url_for('orders.order_detail', id=order_id))

        except Exception as e:
            db.rollback()
            flash(f'Error creating order: {str(e)}', 'error')
            return redirect(url_for('orders.new_order'))
    finally:
        db.close()


# ─────────────────────────────────────────────
#  ORDER DETAIL
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>')
@login_required
def order_detail(id):
    db = get_db()
    try:
        cursor = db.cursor()

        cursor.execute('''
            SELECT o.*, c.name as customer_name, c.customer_code
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
        ''', (id,))
        order = cursor.fetchone()

        if not order:
            flash('Order not found', 'error')
            return redirect(url_for('orders.orders_list'))

        cursor.execute('''
            SELECT oi.id, oi.product_id, p.product_code, p.name,
                   oi.quantity, oi.unit_price, oi.discount_percent, oi.line_total
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (id,))
        items = cursor.fetchall()

        cursor.execute('''
            SELECT id, amount, payment_date, payment_method, notes
            FROM payments
            WHERE order_id = ?
            ORDER BY payment_date DESC
        ''', (id,))
        payments = cursor.fetchall()

        # Allocation summary (shown when order is completed)
        cursor.execute('''
            SELECT oa.product_id, p.name as product_name,
                   SUM(oa.quantity_allocated) as total_allocated
            FROM order_allocations oa
            JOIN products p ON oa.product_id = p.id
            WHERE oa.order_id = ?
            GROUP BY oa.product_id
        ''', (id,))
        allocations = cursor.fetchall()

        total_paid = sum(p['amount'] for p in payments)

        return render_template('order_detail.html',
                               order=order, items=items,
                               payments=payments, allocations=allocations,
                               total_paid=total_paid)
    finally:
        db.close()


# ─────────────────────────────────────────────
#  EDIT ORDER (draft only)
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>/edit')
@login_required
def edit_order(id):
    db = get_db()
    try:
        cursor = db.cursor()

        cursor.execute('''
            SELECT o.*, c.name as customer_name
            FROM orders o JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
        ''', (id,))
        order = cursor.fetchone()

        if not order:
            flash('Order not found', 'error')
            return redirect(url_for('orders.orders_list'))

        # Allow editing for draft and processing orders
        if order['order_status'] not in ('draft', 'processing'):
            flash('Only Draft or Processing orders can be edited. Use "Reopen to Draft" for completed orders.', 'warning')
            return redirect(url_for('orders.order_detail', id=id))

        cursor.execute('SELECT id, customer_code, name FROM customers WHERE is_active = 1 ORDER BY name')
        customers = cursor.fetchall()

        cursor.execute('SELECT id, product_code, name, sale_price FROM products WHERE is_active = 1 ORDER BY name')
        products = cursor.fetchall()

        cursor.execute('''
            SELECT oi.id, oi.product_id, p.name as product_name,
                   oi.quantity, oi.unit_price, oi.discount_percent, oi.line_total
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (id,))
        order_items = cursor.fetchall()

        today = datetime.now().strftime('%Y-%m-%d')
        return render_template('order_form.html',
                               customers=customers, products=products,
                               order=order, order_items=order_items, today=today)
    finally:
        db.close()


@orders_bp.route('/<int:id>/edit', methods=['POST'])
@login_required
def update_order(id):
    db = get_db()
    try:
        cursor = db.cursor()

        cursor.execute('SELECT order_status FROM orders WHERE id = ?', (id,))
        row = cursor.fetchone()
        if not row:
            flash('Order not found', 'error')
            return redirect(url_for('orders.orders_list'))
        if row['order_status'] not in ('draft', 'processing'):
            flash('Only Draft or Processing orders can be edited. Use "Reopen to Draft" for completed orders.', 'warning')
            return redirect(url_for('orders.order_detail', id=id))

        customer_id      = request.form.get('customer_id', type=int)
        discount_amount  = request.form.get('discount_amount', 0, type=float)
        shipping_fee     = request.form.get('shipping_fee',    0, type=float)
        shipping_paid_by = request.form.get('shipping_paid_by', 'customer').strip()
        notes            = request.form.get('notes', '').strip()
        order_date_str   = request.form.get('order_date', '').strip()

        if shipping_paid_by not in ('customer', 'seller'):
            shipping_paid_by = 'customer'

        if not customer_id:
            flash('Customer is required', 'error')
            return redirect(url_for('orders.edit_order', id=id))

        items = _parse_items_from_form(request.form)
        if not items:
            flash('At least one item is required', 'error')
            return redirect(url_for('orders.edit_order', id=id))

        if order_date_str:
            try:
                order_date = datetime.strptime(order_date_str, '%Y-%m-%d').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                order_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        else:
            order_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            # Calculate line totals first (accounting for per-item discounts)
            for item in items:
                item['line_total'] = item['quantity'] * item['unit_price'] * (1 - item['discount_percent'] / 100)

            subtotal = sum(item['line_total'] for item in items)
            if shipping_paid_by == 'customer':
                total_amount = subtotal - discount_amount + shipping_fee
            else:
                total_amount = subtotal - discount_amount

            cursor.execute('''
                UPDATE orders
                SET customer_id = ?, order_date = ?, discount_amount = ?,
                    shipping_fee = ?, shipping_paid_by = ?, subtotal = ?,
                    total_amount = ?, notes = ?
                WHERE id = ?
            ''', (customer_id, order_date, discount_amount, shipping_fee,
                  shipping_paid_by, subtotal, total_amount, notes, id))

            # Replace all items
            cursor.execute('DELETE FROM order_items WHERE order_id = ?', (id,))
            for item in items:
                cursor.execute('''
                    INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount_percent, line_total)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (id, item['product_id'], item['quantity'],
                      item['unit_price'], item['discount_percent'], item['line_total']))

            db.commit()
            flash('Order updated successfully', 'success')
            return redirect(url_for('orders.order_detail', id=id))

        except Exception as e:
            db.rollback()
            flash(f'Error updating order: {str(e)}', 'error')
            return redirect(url_for('orders.edit_order', id=id))
    finally:
        db.close()


# ─────────────────────────────────────────────
#  STATUS CHANGE  (with inventory logic)
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>/status', methods=['POST'])
@login_required
def update_order_status(id):
    db = get_db()
    try:
        new_status = request.form.get('status', '').strip()
        valid_statuses = ['draft', 'processing', 'completed', 'cancelled']
        if new_status not in valid_statuses:
            flash('Invalid status', 'error')
            return redirect(url_for('orders.order_detail', id=id))

        cursor = db.cursor()

        # Get current status
        cursor.execute('SELECT order_status FROM orders WHERE id = ?', (id,))
        row = cursor.fetchone()
        if not row:
            flash('Order not found', 'error')
            return redirect(url_for('orders.orders_list'))
        current_status = row['order_status']

        # Cannot change a cancelled order
        if current_status == 'cancelled' and new_status != 'cancelled':
            flash('Cancelled orders cannot be reopened. Please create a new order.', 'error')
            return redirect(url_for('orders.order_detail', id=id))

        try:
            # ── Completing an order → deduct inventory ──────────────────────────
            if new_status == 'completed' and current_status != 'completed':
                cursor.execute('''
                    SELECT product_id, quantity
                    FROM order_items WHERE order_id = ?
                ''', (id,))
                order_items = [{'product_id': r['product_id'], 'quantity': r['quantity']}
                               for r in cursor.fetchall()]

                warnings = _deduct_inventory_fifo(cursor, id, order_items)
                for w in warnings:
                    flash(f'Low stock warning: {w}', 'warning')

                # Update customer total_spent
                cursor.execute('SELECT total_amount FROM orders WHERE id = ?', (id,))
                total = cursor.fetchone()['total_amount'] or 0
                cursor.execute('UPDATE customers SET total_spent = total_spent + ? WHERE id = (SELECT customer_id FROM orders WHERE id = ?)', (total, id))

            # ── Reopening a completed order → restore inventory ─────────────────
            if current_status == 'completed' and new_status in ('draft', 'processing', 'cancelled'):
                _restore_inventory_from_allocations(cursor, id)

                # Reverse customer total_spent
                cursor.execute('SELECT total_amount, customer_id FROM orders WHERE id = ?', (id,))
                o = cursor.fetchone()
                if o:
                    cursor.execute('UPDATE customers SET total_spent = MAX(0, total_spent - ?) WHERE id = ?',
                                   (o['total_amount'], o['customer_id']))

            cursor.execute('UPDATE orders SET order_status = ? WHERE id = ?', (new_status, id))
            db.commit()

            status_labels = {
                'draft': 'Draft', 'processing': 'Processing',
                'completed': 'Completed', 'cancelled': 'Cancelled'
            }
            flash(f'Order status changed to {status_labels.get(new_status, new_status)}', 'success')

        except Exception as e:
            db.rollback()
            flash(f'Error updating status: {str(e)}', 'error')

        return redirect(url_for('orders.order_detail', id=id))
    finally:
        db.close()


# ─────────────────────────────────────────────
#  ADD PAYMENT
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>/payment', methods=['POST'])
@login_required
def add_payment(id):
    db = get_db()
    try:
        amount         = request.form.get('amount', type=float)
        payment_method = request.form.get('payment_method', '').strip()
        notes          = request.form.get('notes', '').strip()

        if not amount or amount <= 0:
            flash('Payment amount must be greater than 0', 'error')
            return redirect(url_for('orders.order_detail', id=id))

        cursor = db.cursor()
        try:
            cursor.execute('SELECT total_amount FROM orders WHERE id = ?', (id,))
            order = cursor.fetchone()
            if not order:
                flash('Order not found', 'error')
                return redirect(url_for('orders.orders_list'))

            cursor.execute('''
                INSERT INTO payments (order_id, amount, payment_method, notes)
                VALUES (?, ?, ?, ?)
            ''', (id, amount, payment_method, notes))

            cursor.execute('SELECT COALESCE(SUM(amount), 0) as total_paid FROM payments WHERE order_id = ?', (id,))
            total_paid = cursor.fetchone()['total_paid']

            order_total = order['total_amount']
            if total_paid >= order_total:
                payment_status = 'fully_paid'
            elif total_paid > 0:
                payment_status = 'partially_paid'
            else:
                payment_status = 'not_paid'

            cursor.execute('UPDATE orders SET payment_status = ? WHERE id = ?', (payment_status, id))
            db.commit()
            flash('Payment added successfully', 'success')

        except Exception as e:
            db.rollback()
            flash(f'Error adding payment: {str(e)}', 'error')

        return redirect(url_for('orders.order_detail', id=id))
    finally:
        db.close()


def _recalc_payment_status(cursor, order_id):
    """Recalculate and update payment_status for an order based on total payments."""
    cursor.execute('SELECT total_amount FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    if not order:
        return
    cursor.execute('SELECT COALESCE(SUM(amount), 0) as total_paid FROM payments WHERE order_id = ?', (order_id,))
    total_paid = cursor.fetchone()['total_paid']
    order_total = order['total_amount']
    if total_paid >= order_total:
        payment_status = 'fully_paid'
    elif total_paid > 0:
        payment_status = 'partially_paid'
    else:
        payment_status = 'not_paid'
    cursor.execute('UPDATE orders SET payment_status = ? WHERE id = ?', (payment_status, order_id))


# ─────────────────────────────────────────────
#  EDIT PAYMENT
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>/payment/<int:payment_id>/edit', methods=['POST'])
@login_required
def edit_payment(id, payment_id):
    db = get_db()
    try:
        amount         = request.form.get('amount', type=float)
        payment_method = request.form.get('payment_method', '').strip()
        notes          = request.form.get('notes', '').strip()

        if not amount or amount <= 0:
            flash('Payment amount must be greater than 0', 'error')
            return redirect(url_for('orders.order_detail', id=id))

        cursor = db.cursor()
        try:
            cursor.execute('SELECT id FROM payments WHERE id = ? AND order_id = ?', (payment_id, id))
            if not cursor.fetchone():
                flash('Payment not found', 'error')
                return redirect(url_for('orders.order_detail', id=id))

            cursor.execute('''
                UPDATE payments SET amount = ?, payment_method = ?, notes = ?
                WHERE id = ?
            ''', (amount, payment_method, notes, payment_id))

            _recalc_payment_status(cursor, id)
            db.commit()
            flash('Payment updated successfully', 'success')

        except Exception as e:
            db.rollback()
            flash(f'Error updating payment: {str(e)}', 'error')

        return redirect(url_for('orders.order_detail', id=id))
    finally:
        db.close()


# ─────────────────────────────────────────────
#  DELETE PAYMENT
# ─────────────────────────────────────────────

@orders_bp.route('/<int:id>/payment/<int:payment_id>/delete', methods=['POST'])
@login_required
def delete_payment(id, payment_id):
    db = get_db()
    try:
        cursor = db.cursor()
        try:
            cursor.execute('SELECT id FROM payments WHERE id = ? AND order_id = ?', (payment_id, id))
            if not cursor.fetchone():
                flash('Payment not found', 'error')
                return redirect(url_for('orders.order_detail', id=id))

            cursor.execute('DELETE FROM payments WHERE id = ?', (payment_id,))
            _recalc_payment_status(cursor, id)
            db.commit()
            flash('Payment deleted successfully', 'success')

        except Exception as e:
            db.rollback()
            flash(f'Error deleting payment: {str(e)}', 'error')

        return redirect(url_for('orders.order_detail', id=id))
    finally:
        db.close()
