"""
routes/categories.py — Category CRUD.

Categories are referenced by products (category_id FK). The 2-letter category name
prefix is used by generate_product_code() to auto-generate product codes (e.g. EL0001).
Deleting a category that still has products is blocked by the UI (no cascade delete).
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from db import get_db
from auth import login_required
from list_utils import (
    PER_PAGE_OPTIONS,
    pagination_window,
    parse_list_controls,
    resolve_pagination,
    sort_direction_sql,
)

categories_bp = Blueprint('categories', __name__, url_prefix='/categories')

CATEGORY_SORTS = {
    'name': 'c.name COLLATE NOCASE',
    'product_count': 'product_count',
    'created': 'c.created_at',
}


@categories_bp.route('/')
@login_required
def categories_list():
    db = get_db()
    try:
        cursor = db.cursor()

        controls = parse_list_controls(
            request.args,
            CATEGORY_SORTS,
            default_sort='name',
            default_direction='asc',
            default_per_page=25,
        )
        search = request.args.get('search', '').strip()
        sort = controls['sort']
        direction = controls['direction']
        per_page = controls['per_page']

        count_query = 'SELECT COUNT(*) as count FROM categories c WHERE 1=1'
        count_params = []
        if search:
            count_query += ' AND (c.name LIKE ? OR c.description LIKE ?)'
            count_params.extend([f'%{search}%', f'%{search}%'])
        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['count']
        page, total_pages, offset = resolve_pagination(controls['page'], per_page, total)

        query = '''
            SELECT c.id, c.name, c.description, c.created_at,
                   COUNT(p.id) as product_count
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = 1
            WHERE 1=1
        '''
        params = []
        if search:
            query += ' AND (c.name LIKE ? OR c.description LIKE ?)'
            params.extend([f'%{search}%', f'%{search}%'])
        order_expr = CATEGORY_SORTS[sort]
        query += f' GROUP BY c.id ORDER BY {order_expr} {sort_direction_sql(direction)}, c.id ASC LIMIT ? OFFSET ?'
        params.extend([per_page, offset])
        cursor.execute(query, params)
        categories = cursor.fetchall()

        pagination_params = {
            'search': search,
            'sort': sort,
            'direction': direction,
            'per_page': per_page,
        }

        # If HTMX request, return just the table partial
        if request.headers.get('HX-Request'):
            return render_template('partials/categories_table.html', categories=categories)

        return render_template('categories.html', categories=categories, page=page, total_pages=total_pages,
                               total=total, per_page=per_page, per_page_options=PER_PAGE_OPTIONS,
                               page_numbers=pagination_window(page, total_pages),
                               pagination_params=pagination_params,
                               search=search, sort=sort, direction=direction)
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
            return redirect(url_for('categories.categories_list'))

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
