"""
routes/returns.py — Returns CRUD with optional returned-goods inventory restocking.

Accounting rule: returned goods are NOT restored into the original FIFO lots.
If goods are resellable, a NEW inventory lot is created at the stated restock/refurb cost
(default 0). This keeps the original sale COGS intact on the failed-sale order.

Return quantity validation uses a cumulative check per order_item:
  SUM(already_returned for this order_item across ALL returns) + new_qty ≤ original_ordered_qty
This prevents returning more units than were originally sold, even across multiple return events.

After-the-fact restock: Return Detail exposes a restock action for items that were not
restocked at return creation time (restock_inventory_lot_id IS NULL).
"""
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from auth import login_required
from db import generate_code, get_db
from list_utils import (
    PER_PAGE_OPTIONS,
    pagination_window,
    parse_list_controls,
    resolve_pagination,
    sort_direction_sql,
)

returns_bp = Blueprint('returns', __name__, url_prefix='/returns')

RETURN_SORTS = {
    'date': 'r.return_date',
    'code': 'r.return_code COLLATE NOCASE',
    'order': 'o.order_code COLLATE NOCASE',
    'customer': 'c.name COLLATE NOCASE',
}


def _create_returned_goods_lot(
        cursor, return_code, order_code, item, restock_quantity,
        restock_unit_cost, restock_shipping_cost):
    """Create the returned-goods inventory lot and link it to one return item."""
    if restock_quantity <= 0:
        return None

    cursor.execute('''
        INSERT INTO inventory (
            product_id, quantity, remaining_quantity, cost_price,
            shipping_cost, currency, exchange_rate, intake_date, notes
        )
        VALUES (?, ?, ?, ?, ?, 'VND', 1.0, ?, ?)
    ''', (
        item['product_id'],
        restock_quantity,
        restock_quantity,
        restock_unit_cost,
        restock_shipping_cost,
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        f'Returned-goods intake from {return_code} / {order_code}. '
        'Original order COGS remains on the failed sale; this lot value is restock/refurb cost only.'
    ))
    cursor.execute('SELECT last_insert_rowid() as id')
    lot_id = cursor.fetchone()['id']
    cursor.execute('''
        UPDATE return_items
        SET restock_action = 'return_intake',
            restock_quantity = ?,
            restock_unit_cost = ?,
            restock_shipping_cost = ?,
            restock_inventory_lot_id = ?
        WHERE id = ?
          AND restock_inventory_lot_id IS NULL
    ''', (
        restock_quantity,
        restock_unit_cost,
        restock_shipping_cost,
        lot_id,
        item['id'],
    ))
    return lot_id


@returns_bp.route('/')
@login_required
def returns_list():
    db = get_db()
    try:
        cursor = db.cursor()

        controls = parse_list_controls(
            request.args,
            RETURN_SORTS,
            default_sort='date',
            default_direction='desc',
            default_per_page=25,
        )
        search = request.args.get('search', '').strip()
        sort = controls['sort']
        direction = controls['direction']
        per_page = controls['per_page']

        count_query = '''SELECT COUNT(*) as count FROM returns r
                       JOIN orders o ON r.original_order_id = o.id
                       JOIN customers c ON o.customer_id = c.id
                       WHERE 1=1'''
        count_params = []
        if search:
            count_query += ' AND (r.return_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ? OR c.customer_code LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'])

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        page, total_pages, offset = resolve_pagination(controls['page'], per_page, total)

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
            query += ' AND (r.return_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ? OR c.customer_code LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%', f'%{search}%'])

        order_expr = RETURN_SORTS[sort]
        query += f' ORDER BY {order_expr} {sort_direction_sql(direction)}, r.id DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        cursor.execute(query, params)
        returns = cursor.fetchall()

        pagination_params = {
            'search': search,
            'sort': sort,
            'direction': direction,
            'per_page': per_page,
        }

        if request.headers.get('HX-Request'):
            return render_template('partials/returns_table.html', returns=returns, page=page, total_pages=total_pages)

        return render_template('returns.html', returns=returns, page=page, total_pages=total_pages,
                               total=total, per_page=per_page, per_page_options=PER_PAGE_OPTIONS,
                               page_numbers=pagination_window(page, total_pages),
                               pagination_params=pagination_params,
                               search=search, sort=sort, direction=direction)
    finally:
        db.close()


@returns_bp.route('/new')
@login_required
def new_return():
    db = get_db()
    try:
        cursor = db.cursor()
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
        cursor.execute('''
            SELECT o.id, o.order_code, c.name as customer_name, c.customer_code
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.id = ?
        ''', (order_id,))
        order = cursor.fetchone()

        if not order:
            return '', 404

        cursor.execute('''
            SELECT oi.id, oi.product_id, p.product_code, p.name,
                   oi.quantity, oi.unit_price, oi.line_total
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
        global_restock_fallback = request.form.get('restore_inventory') == 'on'

        if not order_id:
            flash('Order is required', 'error')
            return redirect(url_for('returns.new_return'))

        items = []
        index = 0
        while f'items[{index}][order_item_id]' in request.form:
            order_item_id = request.form.get(f'items[{index}][order_item_id]', type=int)
            product_id = request.form.get(f'items[{index}][product_id]', type=int)
            quantity = request.form.get(f'items[{index}][quantity]', type=int)
            refund_amount = request.form.get(f'items[{index}][refund_amount]', type=float)
            item_restock_key = f'items[{index}][restock]'
            restock_requested = request.form.get(item_restock_key) == 'on'
            if item_restock_key not in request.form and global_restock_fallback:
                restock_requested = True
            restock_quantity = request.form.get(f'items[{index}][restock_quantity]', type=int)
            restock_unit_cost = request.form.get(f'items[{index}][restock_unit_cost]', 0, type=float)
            restock_shipping_cost = request.form.get(f'items[{index}][restock_shipping_cost]', 0, type=float)

            if order_item_id and product_id and quantity:
                if refund_amount is None or refund_amount < 0:
                    flash('Refund amount must be zero or greater.', 'error')
                    return redirect(url_for('returns.new_return'))
                if restock_requested and restock_quantity is None:
                    if global_restock_fallback:
                        restock_quantity = quantity
                    else:
                        flash('Restock quantity is required when restocking returned goods.', 'error')
                        return redirect(url_for('returns.new_return'))
                if not restock_requested:
                    restock_quantity = 0
                items.append({
                    'order_item_id': order_item_id,
                    'product_id': product_id,
                    'quantity': quantity,
                    'refund_amount': refund_amount,
                    'restock_quantity': max(restock_quantity or 0, 0),
                    'restock_unit_cost': max(restock_unit_cost or 0, 0),
                    'restock_shipping_cost': max(restock_shipping_cost or 0, 0),
                })
            index += 1

        if not items:
            flash('At least one item is required', 'error')
            return redirect(url_for('returns.new_return'))

        cursor = db.cursor()
        cursor.execute('SELECT order_code, order_status FROM orders WHERE id = ?', (order_id,))
        order = cursor.fetchone()
        if not order:
            flash('Order not found', 'error')
            return redirect(url_for('returns.new_return'))
        if order['order_status'] != 'completed':
            flash('Returns can only be created for Completed orders.', 'error')
            return redirect(url_for('returns.new_return'))

        submitted_by_item = {}
        ordered_by_item = {}
        for item in items:
            cursor.execute(
                'SELECT quantity FROM order_items WHERE id = ? AND order_id = ?',
                (item['order_item_id'], order_id)
            )
            oi = cursor.fetchone()
            if not oi:
                flash(f'Order item not found (id={item["order_item_id"]})', 'error')
                return redirect(url_for('returns.new_return'))
            if item['quantity'] <= 0:
                flash('Return quantity must be greater than zero.', 'error')
                return redirect(url_for('returns.new_return'))
            if item['restock_quantity'] > item['quantity']:
                flash('Restock quantity cannot exceed the return quantity.', 'error')
                return redirect(url_for('returns.new_return'))
            if item['quantity'] > oi['quantity']:
                flash(
                    f'Return quantity ({item["quantity"]}) exceeds the ordered quantity '
                    f'({oi["quantity"]}) for order item #{item["order_item_id"]}.',
                    'error'
                )
                return redirect(url_for('returns.new_return'))
            submitted_by_item[item['order_item_id']] = (
                submitted_by_item.get(item['order_item_id'], 0) + item['quantity']
            )
            ordered_by_item[item['order_item_id']] = oi['quantity']

        for order_item_id, submitted_qty in submitted_by_item.items():
            cursor.execute('''
                SELECT COALESCE(SUM(ri.quantity), 0) AS already_returned
                FROM return_items ri
                JOIN returns r ON ri.return_id = r.id
                WHERE r.original_order_id = ?
                  AND ri.order_item_id = ?
            ''', (order_id, order_item_id))
            already_returned = cursor.fetchone()['already_returned'] or 0
            ordered_qty = ordered_by_item[order_item_id]
            if already_returned + submitted_qty > ordered_qty:
                flash(
                    f'Return quantity exceeds remaining returnable quantity for order item #{order_item_id}. '
                    f'Ordered: {ordered_qty}, already returned: {already_returned}, '
                    f'this return: {submitted_qty}.',
                    'error'
                )
                return redirect(url_for('returns.new_return'))

        return_code = generate_code('TR')

        try:
            cursor.execute('''
                INSERT INTO returns (return_code, original_order_id, return_reason)
                VALUES (?, ?, ?)
            ''', (return_code, order_id, return_reason))
            cursor.execute('SELECT last_insert_rowid() as id')
            return_id = cursor.fetchone()['id']

            # Returned-goods accounting rule:
            # - The original sale keeps its FIFO COGS and seller-paid shipping cost.
            # - The refund reduces revenue through the refunds table.
            # - If goods are restocked, they become a new inventory lot valued only
            #   at resale/refurb/restock cost (0 by default). This prevents the
            #   original purchase cost from being counted a second time on resale.
            for item in items:
                restock_quantity = item['restock_quantity']
                action = 'return_intake' if restock_quantity > 0 else 'none'
                cursor.execute('''
                    INSERT INTO return_items (
                        return_id, order_item_id, product_id, quantity, refund_amount,
                        restock_action, restock_quantity, restock_unit_cost, restock_shipping_cost
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    return_id,
                    item['order_item_id'],
                    item['product_id'],
                    item['quantity'],
                    item['refund_amount'],
                    action,
                    restock_quantity,
                    item['restock_unit_cost'] if restock_quantity > 0 else 0,
                    item['restock_shipping_cost'] if restock_quantity > 0 else 0,
                ))
                cursor.execute('SELECT last_insert_rowid() as id')
                return_item_id = cursor.fetchone()['id']

                if restock_quantity > 0:
                    _create_returned_goods_lot(cursor, return_code, order['order_code'], {
                        'id': return_item_id,
                        'product_id': item['product_id'],
                        'quantity': item['quantity'],
                    }, restock_quantity, item['restock_unit_cost'], item['restock_shipping_cost'])

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

        cursor.execute('''
            SELECT ri.id, ri.order_item_id, ri.product_id, p.product_code, p.name,
                   ri.quantity, ri.refund_amount, oi.unit_price,
                   ri.restock_action, ri.restock_quantity,
                   ri.restock_unit_cost, ri.restock_shipping_cost,
                   ri.restock_inventory_lot_id,
                   i.remaining_quantity AS restock_remaining_quantity
            FROM return_items ri
            JOIN products p ON ri.product_id = p.id
            JOIN order_items oi ON ri.order_item_id = oi.id
            LEFT JOIN inventory i ON i.id = ri.restock_inventory_lot_id
            WHERE ri.return_id = ?
        ''', (id,))
        items = cursor.fetchall()

        cursor.execute('''
            SELECT ia.adjustment_code, ia.adjustment_date, ia.quantity_delta,
                   ia.total_cost, ia.reason, ia.notes,
                   ri.id AS return_item_id, p.product_code, p.name AS product_name
            FROM inventory_adjustments ia
            JOIN return_items ri ON ia.return_item_id = ri.id
            JOIN products p ON ia.product_id = p.id
            WHERE ri.return_id = ?
            ORDER BY ia.adjustment_date DESC, ia.id DESC
        ''', (id,))
        adjustments = cursor.fetchall()

        total_refund = sum(item['refund_amount'] for item in items)
        restockable_items = [item for item in items if not item['restock_inventory_lot_id']]
        return render_template(
            'return_detail.html',
            return_data=return_data,
            items=items,
            total_refund=total_refund,
            restockable_items=restockable_items,
            adjustments=adjustments,
        )
    finally:
        db.close()


@returns_bp.route('/<int:id>/restock', methods=['POST'])
@login_required
def restock_return_items(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            SELECT r.id, r.return_code, o.order_code
            FROM returns r
            JOIN orders o ON r.original_order_id = o.id
            WHERE r.id = ?
        ''', (id,))
        return_data = cursor.fetchone()
        if not return_data:
            flash('Return not found', 'error')
            return redirect(url_for('returns.returns_list'))

        selected_ids = []
        for raw_id in request.form.getlist('return_item_id'):
            try:
                selected_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        if not selected_ids:
            flash('Select at least one return item to restock.', 'error')
            return redirect(url_for('returns.return_detail', id=id))

        cursor.execute('''
            SELECT id, product_id, quantity, restock_quantity
            FROM return_items
            WHERE return_id = ?
              AND restock_inventory_lot_id IS NULL
        ''', (id,))
        restockable_by_id = {row['id']: row for row in cursor.fetchall()}

        restocked_count = 0
        try:
            for return_item_id in selected_ids:
                item = restockable_by_id.get(return_item_id)
                if not item:
                    continue

                default_restock_qty = item['quantity'] - (item['restock_quantity'] or 0)
                restock_qty = request.form.get(
                    f'restock_quantity_{return_item_id}', default_restock_qty, type=int)
                unit_cost = request.form.get(f'restock_unit_cost_{return_item_id}', 0, type=float)
                shipping_cost = request.form.get(f'restock_shipping_cost_{return_item_id}', 0, type=float)
                if restock_qty is None or restock_qty <= 0:
                    flash('Restock quantity must be greater than zero.', 'error')
                    return redirect(url_for('returns.return_detail', id=id))
                if restock_qty > item['quantity']:
                    flash('Restock quantity cannot exceed the returned quantity.', 'error')
                    return redirect(url_for('returns.return_detail', id=id))
                unit_cost = unit_cost or 0
                shipping_cost = shipping_cost or 0
                if unit_cost < 0 or shipping_cost < 0:
                    flash('Restock costs must be zero or greater.', 'error')
                    return redirect(url_for('returns.return_detail', id=id))

                _create_returned_goods_lot(
                    cursor,
                    return_data['return_code'],
                    return_data['order_code'],
                    item,
                    restock_qty,
                    unit_cost,
                    shipping_cost,
                )
                restocked_count += 1

            if restocked_count == 0:
                db.rollback()
                flash('No selected return items can be restocked. They may already have inventory lots.', 'warning')
                return redirect(url_for('returns.return_detail', id=id))

            db.commit()
            flash(f'Restocked {restocked_count} return item(s) into new returned-goods inventory lot(s).', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error restocking returned goods: {str(e)}', 'error')

        return redirect(url_for('returns.return_detail', id=id))
    finally:
        db.close()
