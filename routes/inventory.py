"""
routes/inventory.py — Inventory intake (lot creation) and write-offs.

Each intake creates one inventory lot. Key fields:
  quantity           — original amount recorded at intake; negative = backorder/pre-order lot
  remaining_quantity — live available stock; decremented by FIFO orders, restored on reopen
  cost_price         — fixed at intake; per-unit base cost used in FIFO COGS
  shipping_cost      — inbound shipping cost; amortized into per-unit FIFO cost as cost_price + (shipping/qty)

Write-offs (inventory_adjustments):
  Reduce remaining_quantity for a specific lot and create an inventory_adjustments record.
  Write-offs are NOT sales COGS — they do not affect orders, refunds, revenue, or customer debt.
  The atomic UPDATE ... WHERE remaining_quantity >= quantity prevents over-writing concurrent changes.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import generate_code, get_db
from auth import login_required
from datetime import datetime
from list_utils import (
    PER_PAGE_OPTIONS,
    pagination_window,
    parse_list_controls,
    resolve_pagination,
)

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')

INVENTORY_SORTS = {
    'attention': 'Needs attention',
    'name': 'Product name',
    'code': 'Product code',
    'available': 'Available units',
    'backorder': 'Backorder units',
    'value': 'Stock value',
    'lots': 'Active lots',
}


def _effective_unit_cost(lot):
    """Unit cost used for write-off reporting: item cost + amortized inbound/restock cost."""
    quantity = lot['quantity'] or 0
    base_cost = lot['cost_price'] or 0.0
    shipping_cost = lot['shipping_cost'] or 0.0
    if quantity > 0:
        return base_cost + (shipping_cost / quantity)
    return base_cost


@inventory_bp.route('/')
@login_required
def inventory_list():
    db = get_db()
    try:
        cursor = db.cursor()
        q = (request.args.get('q') or '').strip()
        status_filter = (request.args.get('status') or 'all').strip()
        if status_filter not in ('all', 'in_stock', 'low_stock', 'backorder', 'out'):
            status_filter = 'all'
        controls = parse_list_controls(
            request.args,
            INVENTORY_SORTS,
            default_sort='attention',
            default_direction='asc',
            default_per_page=25,
        )
        sort = controls['sort']
        direction = controls['direction']
        per_page = controls['per_page']

        search_like = f'%{q.lower()}%'
        cursor.execute('''
            SELECT p.id, p.product_code, p.name AS product_name, p.min_stock_level,
                   COALESCE(SUM(CASE WHEN i.remaining_quantity > 0 THEN i.remaining_quantity ELSE 0 END), 0) AS available_quantity,
                   COALESCE(SUM(CASE WHEN i.remaining_quantity < 0 THEN ABS(i.remaining_quantity) ELSE 0 END), 0) AS backorder_quantity,
                   COALESCE(SUM(CASE WHEN i.remaining_quantity != 0 THEN 1 ELSE 0 END), 0) AS open_lot_count,
                   COALESCE(SUM(
                       CASE
                           WHEN i.remaining_quantity > 0 THEN
                               i.remaining_quantity * (
                                   COALESCE(i.cost_price, 0) +
                                   CASE
                                       WHEN i.quantity > 0 THEN COALESCE(i.shipping_cost, 0) / i.quantity
                                       ELSE 0
                                   END
                               )
                           ELSE 0
                       END
                   ), 0) AS stock_value
            FROM products p
            LEFT JOIN inventory i ON i.product_id = p.id
            WHERE p.is_active = 1
              AND (
                  ? = ''
                  OR LOWER(p.name) LIKE ?
                  OR LOWER(p.product_code) LIKE ?
                  OR LOWER(COALESCE(p.barcode, '')) LIKE ?
              )
            GROUP BY p.id
            ORDER BY
                CASE
                    WHEN COALESCE(SUM(CASE WHEN i.remaining_quantity < 0 THEN ABS(i.remaining_quantity) ELSE 0 END), 0) > 0 THEN 0
                    WHEN COALESCE(SUM(CASE WHEN i.remaining_quantity > 0 THEN i.remaining_quantity ELSE 0 END), 0) = 0 THEN 1
                    ELSE 2
                END,
                p.name COLLATE NOCASE
        ''', (q.lower(), search_like, search_like, search_like))
        product_rows = cursor.fetchall()

        products = []
        for row in product_rows:
            product = dict(row)
            available = product['available_quantity'] or 0
            backorder = product['backorder_quantity'] or 0
            min_stock = product['min_stock_level'] or 0
            if backorder > 0:
                product['stock_status'] = 'backorder'
                product['stock_status_label'] = 'Backorder'
            elif available <= 0:
                product['stock_status'] = 'out'
                product['stock_status_label'] = 'Out'
            elif min_stock > 0 and available <= min_stock:
                product['stock_status'] = 'low'
                product['stock_status_label'] = 'Low'
            else:
                product['stock_status'] = 'ok'
                product['stock_status_label'] = 'In stock'

            if status_filter == 'in_stock' and available <= 0:
                continue
            if status_filter == 'low_stock' and not (available > 0 and min_stock > 0 and available <= min_stock):
                continue
            if status_filter == 'backorder' and backorder <= 0:
                continue
            if status_filter == 'out' and not (available <= 0 and backorder <= 0):
                continue
            products.append(product)

        status_rank = {'backorder': 0, 'out': 1, 'low': 2, 'ok': 3}
        sorters = {
            'attention': lambda p: (status_rank.get(p['stock_status'], 9), (p['product_name'] or '').lower()),
            'name': lambda p: (p['product_name'] or '').lower(),
            'code': lambda p: (p['product_code'] or '').lower(),
            'available': lambda p: p['available_quantity'] or 0,
            'backorder': lambda p: p['backorder_quantity'] or 0,
            'value': lambda p: p['stock_value'] or 0,
            'lots': lambda p: p['open_lot_count'] or 0,
        }
        products.sort(key=sorters[sort], reverse=(direction == 'desc'))

        stats = {
            'product_count': len(products),
            'available_units': sum(product['available_quantity'] or 0 for product in products),
            'backorder_units': sum(product['backorder_quantity'] or 0 for product in products),
            'stock_value': sum(product['stock_value'] or 0 for product in products),
        }

        total = len(products)
        page, total_pages, offset = resolve_pagination(controls['page'], per_page, total)
        page_products = products[offset:offset + per_page]

        product_ids = [product['id'] for product in page_products]
        lots_by_product = {product_id: [] for product_id in product_ids}
        if product_ids:
            placeholders = ','.join('?' for _ in product_ids)
            cursor.execute(f'''
                SELECT i.id, i.product_id, p.name as product_name, p.product_code,
                       i.quantity, i.remaining_quantity, i.cost_price, i.currency,
                       i.shipping_cost, i.intake_date, i.notes, i.created_at
                FROM inventory i
                JOIN products p ON i.product_id = p.id
                WHERE i.remaining_quantity != 0
                  AND i.product_id IN ({placeholders})
                ORDER BY i.product_id, i.intake_date DESC, i.id DESC
            ''', product_ids)
            for lot in cursor.fetchall():
                lots_by_product.setdefault(lot['product_id'], []).append(lot)

        for product in page_products:
            product['lots'] = lots_by_product.get(product['id'], [])

        pagination_params = {
            'q': q,
            'status': status_filter,
            'sort': sort,
            'direction': direction,
            'per_page': per_page,
        }

        cursor.execute('''
            SELECT ia.adjustment_code, ia.adjustment_date, ia.quantity_delta,
                   ia.total_cost, ia.reason, ia.notes,
                   p.product_code, p.name AS product_name
            FROM inventory_adjustments ia
            JOIN products p ON ia.product_id = p.id
            ORDER BY ia.adjustment_date DESC, ia.id DESC
            LIMIT 10
        ''')
        recent_adjustments = cursor.fetchall()

        if request.headers.get('HX-Request'):
            return render_template(
                'partials/inventory_table.html',
                products=page_products,
                q=q,
                status_filter=status_filter,
            )

        return render_template(
            'inventory.html',
            products=page_products,
            stats=stats,
            recent_adjustments=recent_adjustments,
            q=q,
            status_filter=status_filter,
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            per_page_options=PER_PAGE_OPTIONS,
            page_numbers=pagination_window(page, total_pages),
            pagination_params=pagination_params,
            sort=sort,
            direction=direction,
        )
    finally:
        db.close()


@inventory_bp.route('/new')
@login_required
def new_intake():
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT id, product_code, name, cost_price FROM products WHERE is_active = 1 ORDER BY name')
        products = cursor.fetchall()
        return render_template('partials/intake_form.html', products=products)
    finally:
        db.close()


@inventory_bp.route('/', methods=['POST'])
@login_required
def create_intake():
    db = get_db()
    try:
        product_id    = request.form.get('product_id', type=int)
        quantity      = request.form.get('quantity', type=int)
        cost_price    = request.form.get('cost_price', type=float)
        shipping_cost = request.form.get('shipping_cost', 0.0, type=float)
        currency      = (request.form.get('currency', 'VND') or 'VND').strip().upper()
        intake_date   = request.form.get('intake_date')
        notes         = request.form.get('notes', '').strip()

        if not product_id or quantity is None:
            flash('Product and quantity are required', 'error')
            return redirect(url_for('inventory.new_intake'))

        if quantity == 0:
            flash('Quantity cannot be zero', 'error')
            return redirect(url_for('inventory.new_intake'))

        cursor = db.cursor()
        try:
            if currency not in ('VND', 'USD'):
                currency = 'VND'

            exchange_rate = 1.0
            if currency == 'USD':
                row = cursor.execute('SELECT vnd_usd_rate FROM settings LIMIT 1').fetchone()
                exchange_rate = row['vnd_usd_rate'] if row and row['vnd_usd_rate'] else 24000.0
                if exchange_rate <= 0:
                    exchange_rate = 24000.0
                cost_price = cost_price * exchange_rate if cost_price is not None else None
                shipping_cost = (shipping_cost or 0.0) * exchange_rate

            if intake_date:
                intake_date = datetime.fromisoformat(intake_date).isoformat()
            else:
                intake_date = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO inventory
                    (product_id, quantity, remaining_quantity, cost_price, shipping_cost,
                     currency, exchange_rate, intake_date, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (product_id, quantity, quantity, cost_price, shipping_cost or 0.0,
                  currency, exchange_rate, intake_date, notes))
            db.commit()

            qty_label = f'{quantity}' if quantity > 0 else f'{quantity} (backorder/pre-order)'
            flash(f'Inventory intake recorded: {qty_label} units', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error creating intake: {str(e)}', 'error')
            return redirect(url_for('inventory.new_intake'))

        return redirect(url_for('inventory.inventory_list'))
    finally:
        db.close()


@inventory_bp.route('/<int:lot_id>/write-off', methods=['POST'])
@login_required
def write_off_lot(lot_id):
    db = get_db()
    try:
        quantity = request.form.get('quantity', type=int)
        reason = request.form.get('reason', 'damaged_unsellable').strip() or 'damaged_unsellable'
        notes = request.form.get('notes', '').strip()
        return_item_id = request.form.get('return_item_id', type=int)

        if quantity is None or quantity <= 0:
            flash('Write-off quantity must be greater than zero.', 'error')
            return redirect(request.referrer or url_for('inventory.inventory_list'))

        cursor = db.cursor()
        cursor.execute('''
            SELECT i.*, p.product_code, p.name AS product_name
            FROM inventory i
            JOIN products p ON i.product_id = p.id
            WHERE i.id = ?
        ''', (lot_id,))
        lot = cursor.fetchone()

        if not lot:
            flash('Inventory lot not found.', 'error')
            return redirect(request.referrer or url_for('inventory.inventory_list'))
        if lot['remaining_quantity'] <= 0:
            flash('This inventory lot has no available stock to write off.', 'error')
            return redirect(request.referrer or url_for('inventory.inventory_list'))
        if quantity > lot['remaining_quantity']:
            flash(
                f'Write-off quantity cannot exceed available stock '
                f'({lot["remaining_quantity"]} unit(s) in this lot).',
                'error'
            )
            return redirect(request.referrer or url_for('inventory.inventory_list'))

        linked_return_item_id = None
        if return_item_id:
            cursor.execute('''
                SELECT id
                FROM return_items
                WHERE id = ?
                  AND restock_inventory_lot_id = ?
            ''', (return_item_id, lot_id))
            return_item = cursor.fetchone()
            if not return_item:
                flash('Return item does not match this returned-goods inventory lot.', 'error')
                return redirect(request.referrer or url_for('inventory.inventory_list'))
            linked_return_item_id = return_item_id

        unit_cost = _effective_unit_cost(lot)
        total_cost = unit_cost * quantity
        adjustment_code = generate_code('AD')
        adjustment_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        try:
            cursor.execute('''
                UPDATE inventory
                SET remaining_quantity = remaining_quantity - ?
                WHERE id = ?
                  AND remaining_quantity >= ?
            ''', (quantity, lot_id, quantity))
            if cursor.rowcount != 1:
                db.rollback()
                flash('Stock changed before the write-off could be saved. Please review the lot and try again.', 'error')
                return redirect(request.referrer or url_for('inventory.inventory_list'))

            cursor.execute('''
                INSERT INTO inventory_adjustments (
                    adjustment_code, inventory_lot_id, product_id, return_item_id,
                    adjustment_type, quantity_delta, unit_cost, total_cost,
                    reason, notes, adjustment_date
                )
                VALUES (?, ?, ?, ?, 'write_off', ?, ?, ?, ?, ?, ?)
            ''', (
                adjustment_code,
                lot_id,
                lot['product_id'],
                linked_return_item_id,
                -quantity,
                unit_cost,
                total_cost,
                reason,
                notes,
                adjustment_date,
            ))
            db.commit()
            flash(
                f'Wrote off {quantity} unit(s) from {lot["product_code"]} '
                f'({adjustment_code}). Stock was reduced; sales revenue and order COGS were unchanged.',
                'success'
            )
        except Exception as e:
            db.rollback()
            flash(f'Error writing off inventory: {str(e)}', 'error')

        return redirect(request.referrer or url_for('inventory.inventory_list'))
    finally:
        db.close()
