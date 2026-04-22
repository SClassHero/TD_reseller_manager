import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from db import get_db, generate_code, generate_product_code, MAX_PRODUCT_IMAGES
from auth import login_required

products_bp = Blueprint('products', __name__, url_prefix='/products')


def _save_product_images(product_id, files):
    """
    Save uploaded image files for a product.
    Returns list of saved filenames.
    Raises ValueError if too many images would be uploaded.
    """
    saved = []
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], str(product_id))
    os.makedirs(upload_dir, exist_ok=True)

    for f in files:
        if f and f.filename:
            from app import allowed_file
            if not allowed_file(f.filename):
                flash(f'File "{f.filename}" has an unsupported format. Allowed: png, jpg, jpeg, gif, webp.', 'warning')
                continue
            ext = f.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"
            f.save(os.path.join(upload_dir, filename))
            saved.append(filename)

    return saved


@products_bp.route('/generate-code')
@login_required
def generate_product_code_api():
    """Return a suggested product code based on the selected category."""
    category_id = request.args.get('category_id', type=int)
    code = generate_product_code(category_id=category_id)
    return jsonify({'code': code})


@products_bp.route('/')
@login_required
def products_list():
    db = get_db()
    try:
        cursor = db.cursor()

        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page

        # Search and category filter
        search = request.args.get('search', '').strip()
        category_filter = request.args.get('category_id', type=int)

        # Build query
        query = '''
            SELECT p.id, p.product_code, p.name, p.category_id, c.name as category_name,
                   p.sale_price, p.cost_price, p.min_stock_level, p.is_active,
                   COALESCE(SUM(i.remaining_quantity), 0) as stock,
                   (SELECT filename FROM product_images WHERE product_id = p.id ORDER BY sort_order, id LIMIT 1) as thumb
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN inventory i ON i.product_id = p.id
            WHERE p.is_active = 1
        '''
        params = []

        if search:
            query += ' AND (p.name LIKE ? OR p.product_code LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%'])

        if category_filter:
            query += ' AND p.category_id = ?'
            params.append(category_filter)

        query += ' GROUP BY p.id ORDER BY p.name LIMIT ? OFFSET ?'
        params.extend([per_page, offset])

        cursor.execute(query, params)
        products = cursor.fetchall()

        # Get total count
        count_query = 'SELECT COUNT(*) as count FROM products WHERE is_active = 1'
        count_params = []
        if search:
            count_query += ' AND (name LIKE ? OR product_code LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%'])
        if category_filter:
            count_query += ' AND category_id = ?'
            count_params.append(category_filter)

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        total_pages = (total + per_page - 1) // per_page

        # Get categories for filter
        cursor.execute('SELECT id, name FROM categories ORDER BY name')
        categories = cursor.fetchall()

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/products_table.html', products=products, page=page, total_pages=total_pages)

        return render_template('products.html', products=products, page=page, total_pages=total_pages,
                             search=search, category_filter=category_filter, categories=categories)
    finally:
        db.close()


@products_bp.route('/new')
@login_required
def new_product():
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT id, name FROM categories ORDER BY name')
        categories = cursor.fetchall()
        # Suggest a default code (no category yet)
        suggested_code = generate_product_code()
        return render_template('partials/product_form.html', product=None, categories=categories,
                               suggested_code=suggested_code, product_images=[])
    finally:
        db.close()


@products_bp.route('/<int:id>/edit')
@login_required
def edit_product(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT * FROM products WHERE id = ?', (id,))
        product = cursor.fetchone()
        if not product:
            flash('Product not found', 'error')
            return redirect(url_for('products.products_list'))

        cursor.execute('SELECT id, name FROM categories ORDER BY name')
        categories = cursor.fetchall()

        cursor.execute('SELECT id, filename, sort_order FROM product_images WHERE product_id = ? ORDER BY sort_order, id', (id,))
        product_images = cursor.fetchall()

        return render_template('partials/product_form.html', product=product, categories=categories,
                               suggested_code=product['product_code'], product_images=product_images)
    finally:
        db.close()


@products_bp.route('/', methods=['POST'])
@login_required
def create_product():
    db = get_db()
    try:
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id', type=int)
        product_code = request.form.get('product_code', '').strip()
        sale_price = request.form.get('sale_price', type=float)
        cost_price = request.form.get('cost_price', type=float)
        barcode = request.form.get('barcode', '').strip()
        min_stock_level = request.form.get('min_stock_level', 0, type=int)
        notes = request.form.get('notes', '').strip()

        if not name:
            flash('Product name is required', 'error')
            return redirect(url_for('products.new_product'))

        # Auto-generate if code is blank
        if not product_code:
            product_code = generate_product_code(category_id=category_id, db_conn=db)

        cursor = db.cursor()
        try:
            cursor.execute('''
                INSERT INTO products (product_code, name, category_id, sale_price, cost_price, barcode, min_stock_level, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (product_code, name, category_id if category_id else None, sale_price, cost_price, barcode, min_stock_level, notes))
            db.commit()

            cursor.execute('SELECT last_insert_rowid() as id')
            product_id = cursor.fetchone()['id']

            # Handle image uploads
            images = request.files.getlist('product_images')
            images = [f for f in images if f and f.filename]
            if images:
                if len(images) > MAX_PRODUCT_IMAGES:
                    flash(f'Maximum {MAX_PRODUCT_IMAGES} photos allowed. Only the first {MAX_PRODUCT_IMAGES} were saved.', 'warning')
                    images = images[:MAX_PRODUCT_IMAGES]
                saved = _save_product_images(product_id, images)
                for i, filename in enumerate(saved):
                    cursor.execute(
                        'INSERT INTO product_images (product_id, filename, sort_order) VALUES (?, ?, ?)',
                        (product_id, filename, i)
                    )
                db.commit()

            flash(f'Product "{name}" created with code {product_code}', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error creating product: {str(e)}', 'error')
            return redirect(url_for('products.new_product'))

        return redirect(url_for('products.products_list'))
    finally:
        db.close()


@products_bp.route('/<int:id>', methods=['POST'])
@login_required
def update_product(id):
    db = get_db()
    try:
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id', type=int)
        product_code = request.form.get('product_code', '').strip()
        sale_price = request.form.get('sale_price', type=float)
        cost_price = request.form.get('cost_price', type=float)
        barcode = request.form.get('barcode', '').strip()
        min_stock_level = request.form.get('min_stock_level', 0, type=int)
        notes = request.form.get('notes', '').strip()

        if not name:
            flash('Product name is required', 'error')
            return redirect(url_for('products.edit_product', id=id))

        if not product_code:
            product_code = generate_product_code(category_id=category_id, db_conn=db)

        cursor = db.cursor()
        try:
            cursor.execute('''
                UPDATE products
                SET name = ?, product_code = ?, category_id = ?, sale_price = ?,
                    cost_price = ?, barcode = ?, min_stock_level = ?, notes = ?
                WHERE id = ?
            ''', (name, product_code, category_id if category_id else None,
                  sale_price, cost_price, barcode, min_stock_level, notes, id))
            db.commit()

            # Handle image uploads (check current count first)
            cursor.execute('SELECT COUNT(*) as cnt FROM product_images WHERE product_id = ?', (id,))
            current_count = cursor.fetchone()['cnt']

            images = request.files.getlist('product_images')
            images = [f for f in images if f and f.filename]
            if images:
                slots_left = MAX_PRODUCT_IMAGES - current_count
                if slots_left <= 0:
                    flash(f'Already at maximum {MAX_PRODUCT_IMAGES} photos. Delete some before adding more.', 'warning')
                else:
                    if len(images) > slots_left:
                        flash(f'Only {slots_left} photo slot(s) remaining. Extra images skipped.', 'warning')
                        images = images[:slots_left]
                    saved = _save_product_images(id, images)
                    for i, filename in enumerate(saved):
                        cursor.execute(
                            'INSERT INTO product_images (product_id, filename, sort_order) VALUES (?, ?, ?)',
                            (id, filename, current_count + i)
                        )
                    db.commit()

            flash('Product updated successfully', 'success')
        except Exception as e:
            db.rollback()
            flash(f'Error updating product: {str(e)}', 'error')
            return redirect(url_for('products.edit_product', id=id))

        return redirect(url_for('products.products_list'))
    finally:
        db.close()


@products_bp.route('/<int:id>/images/<int:image_id>/delete', methods=['POST'])
@login_required
def delete_product_image(id, image_id):
    """Delete a single product image."""
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT filename FROM product_images WHERE id = ? AND product_id = ?', (image_id, id))
        row = cursor.fetchone()
        if row:
            # Remove file from disk
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], str(id), row['filename'])
            if os.path.exists(file_path):
                os.remove(file_path)
            cursor.execute('DELETE FROM product_images WHERE id = ?', (image_id,))
            db.commit()
            flash('Photo deleted', 'success')
        else:
            flash('Image not found', 'error')
    except Exception as e:
        db.rollback()
        flash(f'Error deleting image: {str(e)}', 'error')
    finally:
        db.close()

    # If HTMX, return the updated edit form
    if request.headers.get('HX-Request'):
        return redirect(url_for('products.edit_product', id=id))
    return redirect(url_for('products.products_list'))


@products_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_product(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('UPDATE products SET is_active = 0 WHERE id = ?', (id,))
        db.commit()
        flash('Product deleted successfully', 'success')
        return redirect(url_for('products.products_list'))
    finally:
        db.close()
