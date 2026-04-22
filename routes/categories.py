from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db
from auth import login_required

categories_bp = Blueprint('categories', __name__, url_prefix='/categories')


@categories_bp.route('/')
@login_required
def categories_list():
    db = get_db()
    try:
        cursor = db.cursor()

        # Get categories with product count
        cursor.execute('''
            SELECT c.id, c.name, c.description, c.created_at,
                   COUNT(p.id) as product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = 1
            GROUP BY c.id
            ORDER BY c.name
        ''')
        categories = cursor.fetchall()

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/categories_table.html', categories=categories)

        return render_template('categories.html', categories=categories)
    finally:
        db.close()


@categories_bp.route('/new')
@login_required
def new_category():
    return render_template('partials/category_form.html', category=None)


@categories_bp.route('/<int:id>/edit')
@login_required
def edit_category(id):
    db = get_db()
    try:
        cursor = db.cursor()
        cursor.execute('SELECT * FROM categories WHERE id = ?', (id,))
        category = cursor.fetchone()
        if not category:
            flash('Category not found', 'error')
            return redirect(url_for('categories.categories_list'))
        return render_template('partials/category_form.html', category=category)
    finally:
        db.close()


@categories_bp.route('/', methods=['POST'])
@login_required
def create_category():
    db = get_db()
    try:
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash('Category name is required', 'error')
            return redirect(url_for('categories.new_category'))

        cursor = db.cursor()
        try:
            cursor.execute('''
                INSERT INTO categories (name, description)
                VALUES (?, ?)
            ''', (name, description))
            db.commit()
            flash(f'Category "{name}" created successfully', 'success')
        except db.IntegrityError:
            db.rollback()
            flash(f'Category "{name}" already exists', 'error')
            return redirect(url_for('categories.new_category'))

        return redirect(url_for('categories.categories_list'))
    finally:
        db.close()


@categories_bp.route('/<int:id>', methods=['POST'])
@login_required
def update_category(id):
    db = get_db()
    try:
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if not name:
            flash('Category name is required', 'error')
            return redirect(url_for('categories.edit_category', id=id))

        cursor = db.cursor()
        try:
            cursor.execute('''
                UPDATE categories
                SET name = ?, description = ?
                WHERE id = ?
            ''', (name, description, id))
            db.commit()
            flash('Category updated successfully', 'success')
        except db.IntegrityError:
            db.rollback()
            flash(f'Category "{name}" already exists', 'error')
            return redirect(url_for('categories.edit_category', id=id))

        return redirect(url_for('categories.categories_list'))
    finally:
        db.close()


@categories_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_category(id):
    db = get_db()
    try:
        cursor = db.cursor()

        # Check if category has products
        cursor.execute('SELECT COUNT(*) as count FROM products WHERE category_id = ?', (id,))
        if cursor.fetchone()['count'] > 0:
            flash('Cannot delete category with products', 'error')
            return redirect(url_for('categories.categories_list'))

        cursor.execute('DELETE FROM categories WHERE id = ?', (id,))
        db.commit()
        flash('Category deleted successfully', 'success')

        return redirect(url_for('categories.categories_list'))
    finally:
        db.close()
