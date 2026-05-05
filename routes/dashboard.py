"""
routes/dashboard.py — Dashboard view and shared P&L calculation helpers.

get_revenue_and_profit_data() is the canonical profit formula:
  Gross Revenue  = SUM(total_amount) for processing/completed orders in range
  Total Refunds  = SUM(refunds.amount) filtered by refund_date (not order_date)
  Net Revenue    = Gross Revenue - Total Refunds
  COGS           = FIFO allocations (see _calc_cogs); fallback for HD000001/HD000002 only
  Gross Profit   = Net Revenue - COGS - Seller Shipping
  Adjusted Profit= Gross Profit - Inventory Write-offs

This same formula is replicated in routes/reports.py — keep both in sync if logic changes.
"""
import logging
from flask import Blueprint, render_template, request
from db import get_db
from auth import login_required
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


def get_period_date_range(period, start_date=None, end_date=None):
    """
    Calculate date range based on period.
    Returns tuple of (start_str, end_str) as formatted strings for SQLite comparison.
    """
    now = datetime.now()

    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == 'this_week':
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == 'this_month':
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == 'this_quarter':
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == 'this_year':
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    elif period == 'custom' and start_date and end_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').replace(hour=0, minute=0, second=0)
            end = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        except ValueError:
            start = datetime(2000, 1, 1)
            end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    else:  # 'all' or default
        start = datetime(2000, 1, 1)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    # Always return as strings to ensure consistent SQLite text comparison
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


def _calc_cogs(cursor, start_date_str, end_date_str):
    """
    Calculate COGS for active sale orders in the date range.

    Primary path: FIFO — uses order_allocations.cost_price_at_sale, which is the
    intake cost of the exact inventory lot consumed.  This is the authoritative number.

    Fallback path: used only for known legacy no-allocation orders (HD000001,
    HD000002). Mixed periods include FIFO COGS for allocated orders plus
    fallback COGS for those legacy orders; modern zero-stock orders stay at 0 COGS.

    IMPORTANT: allocation COGS is trusted even when the cost is 0, so genuinely
    free items do not accidentally fall back to products.cost_price.
    """
    fifo_cogs = 0.0
    exclude_allocated_orders = True
    try:
        cursor.execute('''
            SELECT
                COALESCE(SUM(oa.quantity_allocated * oa.cost_price_at_sale), 0) AS alloc_cogs
            FROM order_allocations oa
            JOIN orders o ON oa.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
              AND o.order_date >= ? AND o.order_date <= ?
        ''', (start_date_str, end_date_str))
        row = cursor.fetchone()
        fifo_cogs = (row['alloc_cogs'] or 0.0) if row else 0.0
    except Exception:
        _log.exception('COGS FIFO query failed; falling back to products.cost_price')
        exclude_allocated_orders = False

    legacy_filter = '''
          AND NOT EXISTS (
              SELECT 1 FROM order_allocations oa2 WHERE oa2.order_id = o.id
          )
          AND o.order_code IN ('HD000001', 'HD000002')
    ''' if exclude_allocated_orders else ''

    cursor.execute(f'''
        SELECT COALESCE(SUM(oi.quantity * COALESCE(
            p.cost_price,
            (SELECT AVG(i.cost_price) FROM inventory i WHERE i.product_id = p.id),
            0
        )), 0) as total_cogs
        FROM order_items oi
        JOIN products p ON oi.product_id = p.id
        JOIN orders o ON oi.order_id = o.id
        WHERE o.order_status IN ('processing', 'completed')
          AND o.order_date >= ? AND o.order_date <= ?
          {legacy_filter}
    ''', (start_date_str, end_date_str))
    legacy_cogs = cursor.fetchone()['total_cogs'] or 0.0
    return fifo_cogs + legacy_cogs


def _calc_inventory_adjustments(cursor, start_date_str, end_date_str):
    """Inventory write-off costs in the period, kept separate from sales COGS."""
    cursor.execute('''
        SELECT COALESCE(SUM(total_cost), 0) AS adjustment_cost
        FROM inventory_adjustments
        WHERE adjustment_type = 'write_off'
          AND adjustment_date >= ? AND adjustment_date <= ?
    ''', (start_date_str, end_date_str))
    return cursor.fetchone()['adjustment_cost'] or 0.0


def get_revenue_and_profit_data(db, start_date_str, end_date_str):
    """
    Calculate revenue, COGS, refunds, shipping costs, profit, and margin
    for active sale orders in the date range.

    Accounting model (standard inventory management):
      Gross Revenue  = SUM(total_amount) from processing/completed orders
      Total Refunds  = SUM(refunds.amount) linked to processing/completed orders in range
      Net Revenue    = Gross Revenue - Total Refunds
      Product COGS   = FIFO allocation cost (or fallback)
      Seller Shipping= SUM(shipping_fee) where seller pays
      Total Costs    = Product COGS + Seller Shipping
      Gross Profit   = Net Revenue - Total Costs
      Profit Margin  = Gross Profit / Net Revenue × 100
    """
    cursor = db.cursor()

    # Gross Revenue from active sale orders
    cursor.execute('''
        SELECT COALESCE(SUM(total_amount), 0) as gross_revenue
        FROM orders
        WHERE order_status IN ('processing', 'completed')
        AND order_date >= ? AND order_date <= ?
    ''', (start_date_str, end_date_str))
    gross_revenue = cursor.fetchone()['gross_revenue'] or 0.0

    # Total Refunds issued against active sale orders in this period
    # (refund_date falls within the period, linked to a processing/completed order)
    cursor.execute('''
        SELECT COALESCE(SUM(r.amount), 0) as total_refunds
        FROM refunds r
        JOIN orders o ON r.order_id = o.id
        WHERE o.order_status IN ('processing', 'completed')
        AND r.refund_date >= ? AND r.refund_date <= ?
    ''', (start_date_str, end_date_str))
    total_refunds = cursor.fetchone()['total_refunds'] or 0.0

    net_revenue = gross_revenue - total_refunds

    # Product COGS
    product_cogs = _calc_cogs(cursor, start_date_str, end_date_str)

    # Seller-paid shipping (our cost, not customer revenue)
    try:
        cursor.execute('''
            SELECT COALESCE(SUM(shipping_fee), 0) as seller_shipping
            FROM orders
            WHERE order_status IN ('processing', 'completed')
            AND shipping_paid_by = 'seller'
            AND order_date >= ? AND order_date <= ?
        ''', (start_date_str, end_date_str))
        seller_shipping = cursor.fetchone()['seller_shipping'] or 0.0
    except Exception:
        seller_shipping = 0.0  # column absent on very old DBs — safe to ignore

    total_costs = product_cogs + seller_shipping
    gross_profit = net_revenue - total_costs
    inventory_adjustments = _calc_inventory_adjustments(cursor, start_date_str, end_date_str)
    adjusted_profit = gross_profit - inventory_adjustments
    profit_margin = (gross_profit / net_revenue * 100) if net_revenue > 0 else 0.0
    adjusted_margin = (adjusted_profit / net_revenue * 100) if net_revenue > 0 else 0.0

    # Order count
    cursor.execute('''
        SELECT COUNT(*) as count
        FROM orders
        WHERE order_status IN ('processing', 'completed')
        AND order_date >= ? AND order_date <= ?
    ''', (start_date_str, end_date_str))
    order_count = cursor.fetchone()['count'] or 0

    return {
        'gross_revenue': gross_revenue,
        'total_refunds': total_refunds,
        'total_revenue': net_revenue,     # net revenue (backward compat key)
        'total_cogs': product_cogs,
        'seller_shipping': seller_shipping,
        'total_costs': total_costs,
        'gross_profit': gross_profit,
        'inventory_adjustments': inventory_adjustments,
        'adjusted_profit': adjusted_profit,
        'profit_margin': profit_margin,
        'adjusted_margin': adjusted_margin,
        'order_count': order_count
    }


def get_monthly_revenue_data(db, months=6):
    """
    Get monthly revenue breakdown for the last N months.
    Returns list of dicts with month, revenue, cogs, and profit.
    """
    cursor = db.cursor()
    now = datetime.now()
    data = []

    for i in range(months - 1, -1, -1):
        # Calculate first day of target month
        # Subtract months properly
        target_month = now.month - i
        target_year = now.year
        while target_month <= 0:
            target_month += 12
            target_year -= 1

        month_start = datetime(target_year, target_month, 1, 0, 0, 0)
        # Last day of month
        if target_month == 12:
            month_end = datetime(target_year + 1, 1, 1, 0, 0, 0) - timedelta(seconds=1)
        else:
            month_end = datetime(target_year, target_month + 1, 1, 0, 0, 0) - timedelta(seconds=1)

        start_str = month_start.strftime('%Y-%m-%d %H:%M:%S')
        end_str = month_end.strftime('%Y-%m-%d %H:%M:%S')

        # Revenue for this month
        cursor.execute('''
            SELECT COALESCE(SUM(total_amount), 0) as revenue
            FROM orders
            WHERE order_status IN ('processing', 'completed')
            AND order_date >= ? AND order_date <= ?
        ''', (start_str, end_str))
        revenue = cursor.fetchone()['revenue'] or 0.0

        # Refunds for this month
        cursor.execute('''
            SELECT COALESCE(SUM(r.amount), 0) as refunds
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
            AND r.refund_date >= ? AND r.refund_date <= ?
        ''', (start_str, end_str))
        refunds = cursor.fetchone()['refunds'] or 0.0

        net_revenue = revenue - refunds

        # COGS for this month
        cogs = _calc_cogs(cursor, start_str, end_str)

        # Seller-paid shipping
        try:
            cursor.execute('''
                SELECT COALESCE(SUM(shipping_fee), 0) as s
                FROM orders
                WHERE order_status IN ('processing', 'completed')
                AND shipping_paid_by = 'seller'
                AND order_date >= ? AND order_date <= ?
            ''', (start_str, end_str))
            seller_ship = cursor.fetchone()['s'] or 0.0
        except Exception:
            seller_ship = 0.0

        total_costs = cogs + seller_ship
        inventory_adjustments = _calc_inventory_adjustments(cursor, start_str, end_str)

        data.append({
            'month': month_start.strftime('%b %Y'),
            'month_short': month_start.strftime('%b'),
            'revenue': net_revenue,
            'cogs': total_costs,
            'adjustments': inventory_adjustments,
            'profit': net_revenue - total_costs,
            'adjusted_profit': net_revenue - total_costs - inventory_adjustments
        })

    return data


@dashboard_bp.route('/')
@login_required
def dashboard():
    db = get_db()
    try:
        cursor = db.cursor()

        # Get period from query params (default: this_month)
        period = request.args.get('period', 'this_month')
        start_date_param = request.args.get('start_date')
        end_date_param = request.args.get('end_date')

        # Calculate date range as strings
        start_date_str, end_date_str = get_period_date_range(period, start_date_param, end_date_param)

        # Existing stats
        cursor.execute('SELECT COUNT(*) as count FROM products WHERE is_active = 1')
        total_products = cursor.fetchone()['count']

        cursor.execute('SELECT COALESCE(SUM(remaining_quantity), 0) as total FROM inventory')
        total_stock = cursor.fetchone()['total']

        cursor.execute('SELECT COUNT(*) as count FROM customers WHERE is_active = 1')
        total_customers = cursor.fetchone()['count']

        cursor.execute('''
            SELECT COALESCE(SUM(remaining_quantity * cost_price), 0) as value
            FROM inventory
        ''')
        inventory_value = cursor.fetchone()['value']

        cursor.execute('''
            SELECT COUNT(*) as count FROM orders
            WHERE order_status = 'processing'
        ''')
        pending_orders = cursor.fetchone()['count']

        # Low stock: below min_stock_level but not negative
        cursor.execute('''
            SELECT COUNT(DISTINCT p.id) as count
            FROM products p
            WHERE p.is_active = 1
            AND (
                SELECT COALESCE(SUM(i.remaining_quantity), 0)
                FROM inventory i WHERE i.product_id = p.id
            ) BETWEEN 0 AND p.min_stock_level - 1
        ''')
        low_stock_count = cursor.fetchone()['count']

        # Negative stock: backorders / pre-orders that are not yet in the warehouse
        cursor.execute('''
            SELECT COUNT(DISTINCT p.id) as count
            FROM products p
            WHERE p.is_active = 1
            AND (
                SELECT COALESCE(SUM(i.remaining_quantity), 0)
                FROM inventory i WHERE i.product_id = p.id
            ) < 0
        ''')
        negative_stock_count = cursor.fetchone()['count']

        # Outstanding collections: active sale orders not yet fully paid
        cursor.execute('''
            SELECT COALESCE(SUM(
                o.total_amount - COALESCE((
                    SELECT SUM(p2.amount) FROM payments p2 WHERE p2.order_id = o.id
                ), 0)
            ), 0) AS outstanding
            FROM orders o
            WHERE o.order_status IN ('processing', 'completed')
              AND o.payment_status IN ('not_paid', 'partially_paid')
        ''')
        outstanding_collections = cursor.fetchone()['outstanding'] or 0.0

        cursor.execute('''
            SELECT o.id, o.order_code, o.order_date, c.name as customer_name,
                   o.total_amount, o.order_status, o.payment_status
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            ORDER BY o.order_date DESC
            LIMIT 5
        ''')
        recent_orders = cursor.fetchall()

        # Top 5 products by revenue in the selected period
        cursor.execute('''
            SELECT p.product_code, p.name,
                SUM(oi.quantity) AS units_sold,
                COALESCE(SUM(oi.line_total), 0) AS revenue
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            JOIN orders o ON oi.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
              AND o.order_date >= ? AND o.order_date <= ?
            GROUP BY p.id
            ORDER BY revenue DESC
            LIMIT 5
        ''', (start_date_str, end_date_str))
        top_products = cursor.fetchall()

        # Top 5 customers by spend in the selected period
        cursor.execute('''
            SELECT c.customer_code, c.name, c.id AS customer_id,
                COUNT(o.id) AS order_count,
                COALESCE(SUM(o.total_amount), 0) AS total_spent
            FROM customers c
            JOIN orders o ON c.id = o.customer_id
            WHERE o.order_status IN ('processing', 'completed')
              AND o.order_date >= ? AND o.order_date <= ?
            GROUP BY c.id
            ORDER BY total_spent DESC
            LIMIT 5
        ''', (start_date_str, end_date_str))
        top_customers = cursor.fetchall()

        # Revenue and profit for selected period
        period_data = get_revenue_and_profit_data(db, start_date_str, end_date_str)

        # Monthly chart data (pre-compute max for template)
        monthly_data = get_monthly_revenue_data(db, 6)
        max_revenue = max((m['revenue'] for m in monthly_data), default=0) or 1

        return render_template('dashboard.html',
                               total_products=total_products,
                               total_stock=total_stock,
                               total_customers=total_customers,
                               inventory_value=inventory_value,
                               pending_orders=pending_orders,
                               low_stock_count=low_stock_count,
                               negative_stock_count=negative_stock_count,
                               outstanding_collections=outstanding_collections,
                               recent_orders=recent_orders,
                               top_products=top_products,
                               top_customers=top_customers,
                               period=period,
                               period_data=period_data,
                               monthly_data=monthly_data,
                               max_revenue=max_revenue,
                               start_date=start_date_param,
                               end_date=end_date_param)
    finally:
        db.close()
