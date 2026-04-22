from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db, generate_code
from auth import login_required
from datetime import datetime

refunds_bp = Blueprint('refunds', __name__, url_prefix='/refunds')


@refunds_bp.route('/')
@login_required
def refunds_list():
    db = get_db()
    try:
        cursor = db.cursor()

        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page

        # Search
        search = request.args.get('search', '').strip()

        # Build query — LEFT JOIN returns so we can show linked return code
        query = '''
            SELECT r.id, r.refund_code, r.order_id, r.return_id, r.amount,
                   r.refund_date, r.refund_method, r.reason,
                   o.order_code, c.name as customer_name, c.customer_code,
                   ret.return_code
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            JOIN customers c ON o.customer_id = c.id
            LEFT JOIN returns ret ON r.return_id = ret.id
            WHERE 1=1
        '''
        params = []

        if search:
            query += ' AND (r.refund_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        query += ' ORDER BY r.refund_date DESC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])

        cursor.execute(query, params)
        refunds = cursor.fetchall()

        # Get total count
        count_query = '''SELECT COUNT(*) as count FROM refunds r
                       JOIN orders o ON r.order_id = o.id
                       JOIN customers c ON o.customer_id = c.id
                       WHERE 1=1'''
        count_params = []
        if search:
            count_query += ' AND (r.refund_code LIKE ? OR o.order_code LIKE ? OR c.name LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        total_pages = (total + per_page - 1) // per_page

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/refunds_table.html', refunds=refunds, page=page, total_pages=total_pages)

        return render_template('refunds.html', refunds=refunds, page=page, total_pages=total_pages, search=search)
    finally:
        db.close()


@refunds_bp.route('/new')
@login_required
def new_refund():
    db = get_db()
    try:
        cursor = db.cursor()

        # Get all non-cancelled orders
        cursor.execute('''
            SELECT o.id, o.order_code, o.total_amount, c.name as customer_name
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.order_status != 'cancelled'
            ORDER BY o.order_date DESC
        ''')
        orders = cursor.fetchall()

        # Get all returns (for optional linking)
        cursor.execute('''
            SELECT ret.id, ret.return_code, ret.original_order_id, o.order_code
            FROM returns ret
            JOIN orders o ON ret.original_order_id = o.id
            ORDER BY ret.return_date DESC
        ''')
        returns = cursor.fetchall()

        return render_template('refund_form.html', orders=orders, returns=returns, refund=None)
    finally:
        db.close()


@refunds_bp.route('/', methods=['POST'])
@login_required
def create_refund():
    db = get_db()
    try:
        order_id = request.form.get('order_id', type=int)
        return_id = request.form.get('return_id', type=int)  # optional
        amount = request.form.get('amount', type=float)
        refund_method = request.form.get('refund_method', '').strip()
        reason = request.form.get('reason', '').strip()
        notes = request.form.get('notes', '').strip()

        if not order_id:
            flash('Order is required', 'error')
            return redirect(url_for('refunds.new_refund'))

        if not amount or amount <= 0:
            flash('Refund amount must be greater than 0', 'error')
            return redirect(url_for('refunds.new_refund'))

        # Generate refund code
        refund_code = generate_code('RF')

        cursor = db.cursor()
        try:
            cursor.execute('''
                INSERT INTO refunds (refund_code, order_id, return_id, amount, refund_method, reason, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (refund_code, order_id, return_id if return_id else None,
                  amount, refund_method, reason, notes))
            db.commit()

            cursor.execute('SELECT last_insert_rowid() as id')
            refund_id = cursor.fetchone()['id']

            flash(f'Refund "{refund_code}" created successfully', 'success')
            return redirect(url_for('refunds.refund_detail', id=refund_id))

        except Exception as e:
            db.rollback()
            flash(f'Error creating refund: {str(e)}', 'error')
            return redirect(url_for('refunds.new_refund'))

    finally:
        db.close()


@refunds_bp.route('/<int:id>/edit')
@login_required
def edit_refund(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('''
            SELECT r.*, o.order_code, o.total_amount, c.name as customer_name
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            JOIN customers c ON o.customer_id = c.id
            WHERE r.id = ?
        ''', (id,))
        refund = cursor.fetchone()
        if not refund:
            flash('Refund not found', 'error')
            return redirect(url_for('refunds.refunds_list'))

        cursor.execute('''
            SELECT o.id, o.order_code, o.total_amount, c.name as customer_name
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.order_status != 'cancelled'
            ORDER BY o.order_date DESC
        ''')
        orders = cursor.fetchall()

        cursor.execute('''
            SELECT ret.id, ret.return_code, ret.original_order_id, o.order_code
            FROM returns ret
            JOIN orders o ON ret.original_order_id = o.id
            ORDER BY ret.return_date DESC
        ''')
        returns = cursor.fetchall()

        return render_template('refund_form.html', refund=refund, orders=orders, returns=returns)
    finally:
        db.close()


@refunds_bp.route('/<int:id>/edit', methods=['POST'])
@login_required
def update_refund(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT id, refund_code FROM refunds WHERE id = ?', (id,))
        existing = cursor.fetchone()
        if not existing:
            flash('Refund not found', 'error')
            return redirect(url_for('refunds.refunds_list'))

        order_id      = request.form.get('order_id', type=int)
        return_id     = request.form.get('return_id', type=int)
        amount        = request.form.get('amount', type=float)
        refund_method = request.form.get('refund_method', '').strip()
        reason        = request.form.get('reason', '').strip()
        notes         = request.form.get('notes', '').strip()
        refund_date   = request.form.get('refund_date', '').strip()

        if not order_id:
            flash('Order is required', 'error')
            return redirect(url_for('refunds.edit_refund', id=id))

        if not amount or amount <= 0:
            flash('Refund amount must be greater than 0', 'error')
            return redirect(url_for('refunds.edit_refund', id=id))

        # Parse date or keep existing
        if refund_date:
            try:
                refund_date = datetime.strptime(refund_date, '%Y-%m-%d').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                refund_date = None

        try:
            if refund_date:
                cursor.execute('''
                    UPDATE refunds
                    SET order_id = ?, return_id = ?, amount = ?,
                        refund_method = ?, reason = ?, notes = ?, refund_date = ?
                    WHERE id = ?
                ''', (order_id, return_id or None, amount,
                      refund_method, reason, notes, refund_date, id))
            else:
                cursor.execute('''
                    UPDATE refunds
                    SET order_id = ?, return_id = ?, amount = ?,
                        refund_method = ?, reason = ?, notes = ?
                    WHERE id = ?
                ''', (order_id, return_id or None, amount,
                      refund_method, reason, notes, id))
            db.commit()
            flash(f'Refund {existing["refund_code"]} updated successfully', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error updating refund: {str(e)}', 'error')
            return redirect(url_for('refunds.edit_refund', id=id))

        return redirect(url_for('refunds.refund_detail', id=id))
    finally:
        db.close()


@refunds_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_refund(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT refund_code FROM refunds WHERE id = ?', (id,))
        row = cursor.fetchone()
        if not row:
            flash('Refund not found', 'error')
            return redirect(url_for('refunds.refunds_list'))

        try:
            cursor.execute('DELETE FROM refunds WHERE id = ?', (id,))
            db.commit()
            flash(f'Refund {row["refund_code"]} deleted. Dashboard and customer balances updated automatically.', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error deleting refund: {str(e)}', 'error')

        return redirect(url_for('refunds.refunds_list'))
    finally:
        db.close()


@refunds_bp.route('/<int:id>')
@login_required
def refund_detail(id):
    db = get_db()
    try:
        cursor = db.cursor()

        # Get refund with optional return info
        cursor.execute('''
            SELECT r.*, o.order_code, o.order_date, o.total_amount,
                   o.customer_id, c.name as customer_name, c.customer_code,
                   ret.return_code
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            JOIN customers c ON o.customer_id = c.id
            LEFT JOIN returns ret ON r.return_id = ret.id
            WHERE r.id = ?
        ''', (id,))
        refund = cursor.fetchone()

        if not refund:
            flash('Refund not found', 'error')
            return redirect(url_for('refunds.refunds_list'))

        # Get order items (for reference)
        cursor.execute('''
            SELECT oi.id, oi.product_id, p.product_code, p.name,
                   oi.quantity, oi.unit_price, oi.line_total
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        ''', (refund['order_id'],))
        items = cursor.fetchall()

        return render_template('refund_detail.html', refund=refund, items=items)
    finally:
        db.close()
