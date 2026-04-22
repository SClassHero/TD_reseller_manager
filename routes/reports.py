from flask import Blueprint, render_template, request, flash
from db import get_db
from auth import login_required
from datetime import datetime, timedelta

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


@reports_bp.route('/')
@login_required
def reports_index():
    """Reports index page with links to different report types."""
    return render_template('reports.html')


@reports_bp.route('/sales')
@login_required
def sales_report():
    """Sales report with date range filter."""
    db = get_db()
    try:
        cursor = db.cursor()

        # Get date range from query params
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')

        # Default to last 30 days if not specified
        if not start_date or not end_date:
            end_date_obj = datetime.now()
            start_date_obj = end_date_obj - timedelta(days=30)
            start_date = start_date_obj.strftime('%Y-%m-%d')
            end_date = end_date_obj.strftime('%Y-%m-%d')

        # Get total orders and revenue for period
        cursor.execute('''
            SELECT
                COUNT(DISTINCT o.id) as total_orders,
                COALESCE(SUM(o.total_amount), 0) as total_revenue,
                AVG(o.total_amount) as avg_order_value
            FROM orders o
            WHERE o.order_status = 'completed'
                AND DATE(o.order_date) >= ?
                AND DATE(o.order_date) <= ?
        ''', (start_date, end_date))
        summary = cursor.fetchone()

        # Get top products by quantity sold
        cursor.execute('''
            SELECT
                p.product_code,
                p.name,
                SUM(oi.quantity) as total_qty,
                SUM(oi.line_total) as total_revenue
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            JOIN orders o ON oi.order_id = o.id
            WHERE o.order_status = 'completed'
                AND DATE(o.order_date) >= ?
                AND DATE(o.order_date) <= ?
            GROUP BY p.id
            ORDER BY total_qty DESC
            LIMIT 10
        ''', (start_date, end_date))
        top_products = cursor.fetchall()

        # Get top customers by spend
        cursor.execute('''
            SELECT
                c.customer_code,
                c.name,
                COUNT(o.id) as order_count,
                COALESCE(SUM(o.total_amount), 0) as total_spent
            FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id AND o.order_status = 'completed'
                AND DATE(o.order_date) >= ?
                AND DATE(o.order_date) <= ?
            GROUP BY c.id
            ORDER BY total_spent DESC
            LIMIT 10
        ''', (start_date, end_date))
        top_customers = cursor.fetchall()

        return render_template('report_sales.html',
                             summary=summary,
                             top_products=top_products,
                             top_customers=top_customers,
                             start_date=start_date,
                             end_date=end_date)
    finally:
        db.close()


@reports_bp.route('/inventory')
@login_required
def inventory_report():
    """Inventory report with stock levels and alerts."""
    db = get_db()
    try:
        cursor = db.cursor()

        # Get current stock levels and inventory value
        cursor.execute('''
            SELECT
                p.id,
                p.product_code,
                p.name,
                c.name as category_name,
                p.min_stock_level,
                COALESCE(SUM(i.remaining_quantity), 0) as current_stock,
                p.cost_price,
                COALESCE(SUM(i.remaining_quantity), 0) * COALESCE(p.cost_price, 0) as stock_value
            FROM products p
            LEFT JOIN inventory i ON p.id = i.product_id
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.is_active = 1
            GROUP BY p.id
            ORDER BY p.name
        ''')
        all_products = cursor.fetchall()

        # Identify low stock products
        low_stock = [p for p in all_products if p['current_stock'] <= p['min_stock_level']]

        # Get dead stock (products with no sales in 30+ days)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT
                p.id,
                p.product_code,
                p.name,
                c.name as category_name,
                COALESCE(SUM(i.remaining_quantity), 0) as current_stock,
                COALESCE(SUM(i.remaining_quantity), 0) * COALESCE(p.cost_price, 0) as stock_value
            FROM products p
            LEFT JOIN inventory i ON p.id = i.product_id
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.is_active = 1
                AND p.id NOT IN (
                    SELECT DISTINCT oi.product_id
                    FROM order_items oi
                    JOIN orders o ON oi.order_id = o.id
                    WHERE DATE(o.order_date) >= ?
                )
            GROUP BY p.id
            HAVING COALESCE(SUM(i.remaining_quantity), 0) > 0
            ORDER BY p.name
        ''', (thirty_days_ago,))
        dead_stock = cursor.fetchall()

        # Calculate total inventory value
        total_inventory_value = sum(p['stock_value'] or 0 for p in all_products)

        # Get inventory by category
        cursor.execute('''
            SELECT
                c.name as category_name,
                COUNT(p.id) as product_count,
                COALESCE(SUM(i.remaining_quantity), 0) as total_qty,
                COALESCE(SUM(i.remaining_quantity * p.cost_price), 0) as category_value
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = 1
            LEFT JOIN inventory i ON p.id = i.product_id
            GROUP BY c.id
            ORDER BY c.name
        ''')
        by_category = cursor.fetchall()

        return render_template('report_inventory.html',
                             all_products=all_products,
                             low_stock=low_stock,
                             dead_stock=dead_stock,
                             total_inventory_value=total_inventory_value,
                             by_category=by_category)
    finally:
        db.close()


@reports_bp.route('/profit')
@login_required
def profit_report():
    """Profit & Loss report by time period and product."""
    db = get_db()
    try:
        cursor = db.cursor()

        # Get overall P&L
        cursor.execute('''
            SELECT
                COALESCE(SUM(o.total_amount), 0) as total_revenue,
                COALESCE(SUM(oi.quantity * p.cost_price), 0) as total_cogs,
                COALESCE(SUM(o.total_amount), 0) - COALESCE(SUM(oi.quantity * p.cost_price), 0) as gross_profit
            FROM orders o
            LEFT JOIN order_items oi ON o.id = oi.order_id
            LEFT JOIN products p ON oi.product_id = p.id
            WHERE o.order_status = 'completed'
        ''')
        overall_pl = cursor.fetchone()

        # Calculate profit margin
        overall_margin = 0
        if overall_pl['total_revenue'] and overall_pl['total_revenue'] > 0:
            overall_margin = (overall_pl['gross_profit'] / overall_pl['total_revenue']) * 100

        # P&L by month
        cursor.execute('''
            SELECT
                strftime('%Y-%m', o.order_date) as month,
                COUNT(o.id) as order_count,
                COALESCE(SUM(o.total_amount), 0) as revenue,
                COALESCE(SUM(oi.quantity * p.cost_price), 0) as cogs,
                COALESCE(SUM(o.total_amount), 0) - COALESCE(SUM(oi.quantity * p.cost_price), 0) as profit
            FROM orders o
            LEFT JOIN order_items oi ON o.id = oi.order_id
            LEFT JOIN products p ON oi.product_id = p.id
            WHERE o.order_status = 'completed'
            GROUP BY month
            ORDER BY month DESC
            LIMIT 12
        ''')
        by_month = cursor.fetchall()

        # P&L by product
        cursor.execute('''
            SELECT
                p.product_code,
                p.name,
                SUM(oi.quantity) as units_sold,
                COALESCE(SUM(oi.line_total), 0) as revenue,
                COALESCE(SUM(oi.quantity * p.cost_price), 0) as cogs,
                COALESCE(SUM(oi.line_total), 0) - COALESCE(SUM(oi.quantity * p.cost_price), 0) as profit
            FROM products p
            LEFT JOIN order_items oi ON p.id = oi.product_id
            LEFT JOIN orders o ON oi.order_id = o.id AND o.order_status = 'completed'
            WHERE p.is_active = 1
            GROUP BY p.id
            HAVING units_sold > 0 OR revenue > 0
            ORDER BY profit DESC
        ''')
        by_product = cursor.fetchall()

        return render_template('report_profit.html',
                             overall_pl=overall_pl,
                             overall_margin=overall_margin,
                             by_month=by_month,
                             by_product=by_product)
    finally:
        db.close()
