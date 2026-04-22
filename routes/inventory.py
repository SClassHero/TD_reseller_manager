from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db
from auth import login_required
from datetime import datetime

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')


@inventory_bp.route('/')
@login_required
def inventory_list():
    db = get_db()
    try:
        cursor = db.cursor()

        # Show all lots with non-zero remaining quantity (including negative = backorder)
        cursor.execute('''
            SELECT i.id, i.product_id, p.name as product_name, p.product_code,
                   i.quantity, i.remaining_quantity, i.cost_price, i.currency,
                   i.shipping_cost, i.intake_date, i.notes, i.created_at
            FROM inventory i
            JOIN products p ON i.product_id = p.id
            WHERE i.remaining_quantity != 0
            ORDER BY i.intake_date DESC
        ''')
        lots = cursor.fetchall()

        if request.headers.get('HX-Request'):
            return render_template('partials/inventory_table.html', lots=lots)

        return render_template('inventory.html', lots=lots)
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
        currency      = request.form.get('currency', 'VND').strip()
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
            if intake_date:
                intake_date = datetime.fromisoformat(intake_date).isoformat()
            else:
                intake_date = datetime.now().isoformat()

            cursor.execute('''
                INSERT INTO inventory
                    (product_id, quantity, remaining_quantity, cost_price, shipping_cost, currency, intake_date, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (product_id, quantity, quantity, cost_price, shipping_cost or 0.0, currency, intake_date, notes))
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
