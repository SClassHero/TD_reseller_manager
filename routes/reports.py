"""
routes/reports.py — Sales, inventory, and profit reports.

Accounting rules (must match dashboard.py):
  Revenue    = SUM(orders.total_amount) for processing/completed orders
  Net Revenue= Revenue - Refunds
  COGS       = FIFO (order_allocations.cost_price_at_sale) when allocation records exist;
               fallback to products.cost_price for legacy orders with no allocations.
  Gross Profit = Net Revenue - COGS - Seller Shipping

IMPORTANT: Never use products.cost_price as the primary COGS source.
That field is a default/reference for data entry only.
"""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request
from db import get_db
from auth import login_required

_log = logging.getLogger(__name__)

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

_ALL_TIME_START = '2000-01-01 00:00:00'


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _calc_cogs(cursor, start_str, end_str):
    """
    COGS for processing/completed orders in [start_str, end_str].

    Primary path — FIFO: uses order_allocations.cost_price_at_sale, which records
    the exact intake cost of each lot consumed when an order was completed.

    Fallback path — legacy only: used only for known legacy no-allocation orders
    (HD000001, HD000002). Mixed periods include FIFO COGS for allocated orders
    plus fallback COGS for those legacy orders; modern zero-stock orders stay at
    0 COGS.

    Allocation COGS is trusted even when cost is 0, so genuinely free items do
    not accidentally trigger the fallback.

    This mirrors dashboard._calc_cogs() exactly — keep both in sync if logic changes.
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
        ''', (start_str, end_str))
        row = cursor.fetchone()
        fifo_cogs = (row['alloc_cogs'] or 0.0) if row else 0.0
    except Exception:
        _log.exception('Reports: FIFO COGS query failed, falling back to products.cost_price')
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
        )), 0) AS total_cogs
        FROM order_items oi
        JOIN products p ON oi.product_id = p.id
        JOIN orders o ON oi.order_id = o.id
        WHERE o.order_status IN ('processing', 'completed')
          AND o.order_date >= ? AND o.order_date <= ?
          {legacy_filter}
    ''', (start_str, end_str))
    legacy_cogs = cursor.fetchone()['total_cogs'] or 0.0
    return fifo_cogs + legacy_cogs


def _calc_inventory_adjustments(cursor, start_str, end_str):
    """Inventory write-off costs in the period, separate from sales COGS."""
    cursor.execute('''
        SELECT COALESCE(SUM(total_cost), 0) AS adjustment_cost
        FROM inventory_adjustments
        WHERE adjustment_type = 'write_off'
          AND adjustment_date >= ? AND adjustment_date <= ?
    ''', (start_str, end_str))
    return cursor.fetchone()['adjustment_cost'] or 0.0


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

        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')

        if not start_date or not end_date:
            end_date_obj = datetime.now()
            start_date_obj = end_date_obj - timedelta(days=30)
            start_date = start_date_obj.strftime('%Y-%m-%d')
            end_date = end_date_obj.strftime('%Y-%m-%d')

        cursor.execute('''
            SELECT
                COUNT(DISTINCT o.id) AS total_orders,
                COALESCE(SUM(o.total_amount), 0) AS total_revenue,
                AVG(o.total_amount) AS avg_order_value
            FROM orders o
            WHERE o.order_status IN ('processing', 'completed')
              AND DATE(o.order_date) >= ?
              AND DATE(o.order_date) <= ?
        ''', (start_date, end_date))
        summary = cursor.fetchone()

        cursor.execute('''
            SELECT
                p.product_code,
                p.name,
                SUM(oi.quantity) AS total_qty,
                SUM(oi.line_total) AS total_revenue
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            JOIN orders o ON oi.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
              AND DATE(o.order_date) >= ?
              AND DATE(o.order_date) <= ?
            GROUP BY p.id
            ORDER BY total_qty DESC
            LIMIT 10
        ''', (start_date, end_date))
        top_products = cursor.fetchall()

        cursor.execute('''
            SELECT
                c.customer_code,
                c.name,
                COUNT(o.id) AS order_count,
                COALESCE(SUM(o.total_amount), 0) AS total_spent
            FROM customers c
            LEFT JOIN orders o ON c.id = o.customer_id
                AND o.order_status IN ('processing', 'completed')
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
    """
    Inventory report with stock levels and valuation.

    Stock value uses actual intake cost per lot (inventory.cost_price * remaining_quantity),
    NOT products.cost_price. A product with multiple intake lots at different costs gets
    the correct weighted value this way.
    """
    db = get_db()
    try:
        cursor = db.cursor()

        # Per-product stock: value = SUM of (remaining_qty * actual lot cost) across all lots
        cursor.execute('''
            SELECT
                p.id,
                p.product_code,
                p.name,
                c.name AS category_name,
                p.min_stock_level,
                COALESCE(SUM(i.remaining_quantity), 0) AS current_stock,
                COALESCE(AVG(i.cost_price), p.cost_price, 0) AS cost_price,
                COALESCE(SUM(i.remaining_quantity * i.cost_price), 0) AS stock_value
            FROM products p
            LEFT JOIN inventory i ON p.id = i.product_id
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.is_active = 1
            GROUP BY p.id
            ORDER BY p.name
        ''')
        all_products = cursor.fetchall()

        low_stock = [p for p in all_products if p['current_stock'] <= p['min_stock_level']]

        # Dead stock: has remaining inventory but no sales in the last 30 days
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT
                p.id,
                p.product_code,
                p.name,
                c.name AS category_name,
                COALESCE(SUM(i.remaining_quantity), 0) AS current_stock,
                COALESCE(SUM(i.remaining_quantity * i.cost_price), 0) AS stock_value
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

        total_inventory_value = sum(p['stock_value'] or 0 for p in all_products)

        cursor.execute('''
            SELECT
                c.name AS category_name,
                COUNT(DISTINCT p.id) AS product_count,
                COALESCE(SUM(i.remaining_quantity), 0) AS total_qty,
                COALESCE(SUM(i.remaining_quantity * i.cost_price), 0) AS category_value
            FROM categories c
            LEFT JOIN products p ON c.id = p.category_id AND p.is_active = 1
            LEFT JOIN inventory i ON p.id = i.product_id
            GROUP BY c.id
            ORDER BY c.name
        ''')
        by_category = cursor.fetchall()

        cursor.execute('''
            SELECT ia.adjustment_code, ia.adjustment_date, ia.quantity_delta,
                   ia.total_cost, ia.reason, ia.notes,
                   p.product_code, p.name AS product_name
            FROM inventory_adjustments ia
            JOIN products p ON ia.product_id = p.id
            ORDER BY ia.adjustment_date DESC, ia.id DESC
            LIMIT 25
        ''')
        recent_adjustments = cursor.fetchall()

        return render_template('report_inventory.html',
                               all_products=all_products,
                               low_stock=low_stock,
                               dead_stock=dead_stock,
                               total_inventory_value=total_inventory_value,
                               by_category=by_category,
                               recent_adjustments=recent_adjustments)
    finally:
        db.close()


@reports_bp.route('/debt-aging')
@login_required
def debt_aging_report():
    """
    Customer debt aging report.

    Shows each customer with an outstanding balance broken into aging buckets:
      Current (0-7 days), Short (8-30 days), Medium (31-90 days), Long (90+ days)

    Age is measured from the oldest unpaid order date.
    Only processing/completed orders with payment_status != 'fully_paid' are included.
    Debt amounts computed live — never uses the stale customers.outstanding_debt column.
    """
    db = get_db()
    try:
        cursor = db.cursor()
        now = datetime.now()

        # Fetch all active, not-fully-paid orders with their unpaid balance
        cursor.execute('''
            SELECT
                c.id AS customer_id,
                c.customer_code,
                c.name AS customer_name,
                o.id AS order_id,
                o.order_code,
                o.order_date,
                o.total_amount,
                COALESCE((
                    SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id
                ), 0) AS amount_paid
            FROM orders o
            JOIN customers c ON o.customer_id = c.id
            WHERE o.order_status IN ('processing', 'completed')
              AND o.payment_status IN ('not_paid', 'partially_paid')
            ORDER BY c.name, o.order_date ASC
        ''')
        raw_orders = cursor.fetchall()

        # Group by customer, compute balance and age per order
        from collections import defaultdict
        customer_map = {}
        for row in raw_orders:
            cid = row['customer_id']
            if cid not in customer_map:
                customer_map[cid] = {
                    'customer_id': cid,
                    'customer_code': row['customer_code'],
                    'customer_name': row['customer_name'],
                    'orders': [],
                    'total_owed': 0.0,
                    'bucket_current': 0.0,   # 0-7 days
                    'bucket_short': 0.0,     # 8-30 days
                    'bucket_medium': 0.0,    # 31-90 days
                    'bucket_long': 0.0,      # 90+ days
                }
            balance = (row['total_amount'] or 0.0) - (row['amount_paid'] or 0.0)
            if balance <= 0:
                continue

            try:
                order_date = datetime.fromisoformat(str(row['order_date'])[:10])
            except Exception:
                order_date = now

            age_days = (now - order_date).days

            customer_map[cid]['orders'].append({
                'order_id': row['order_id'],
                'order_code': row['order_code'],
                'order_date': str(row['order_date'])[:10],
                'total_amount': row['total_amount'],
                'amount_paid': row['amount_paid'],
                'balance': balance,
                'age_days': age_days,
            })
            customer_map[cid]['total_owed'] += balance

            if age_days <= 7:
                customer_map[cid]['bucket_current'] += balance
            elif age_days <= 30:
                customer_map[cid]['bucket_short'] += balance
            elif age_days <= 90:
                customer_map[cid]['bucket_medium'] += balance
            else:
                customer_map[cid]['bucket_long'] += balance

        # Filter out customers with zero balance (edge case) and sort by total owed desc
        customers = [c for c in customer_map.values() if c['total_owed'] > 0]
        customers.sort(key=lambda x: x['total_owed'], reverse=True)

        # Summary totals
        totals = {
            'total_owed': sum(c['total_owed'] for c in customers),
            'bucket_current': sum(c['bucket_current'] for c in customers),
            'bucket_short': sum(c['bucket_short'] for c in customers),
            'bucket_medium': sum(c['bucket_medium'] for c in customers),
            'bucket_long': sum(c['bucket_long'] for c in customers),
        }

        return render_template('report_debt_aging.html', customers=customers, totals=totals)
    finally:
        db.close()


@reports_bp.route('/profit')
@login_required
def profit_report():
    """
    Profit & Loss report across all time, broken down by month and product.

    COGS uses the FIFO allocation system (order_allocations.cost_price_at_sale).
    Legacy orders without allocation records fall back to products.cost_price.
    This matches the dashboard accounting model exactly.
    """
    db = get_db()
    try:
        cursor = db.cursor()
        now_str = _now_str()

        # ── Overall P&L (all active sale orders, all time) ──────────────────────

        cursor.execute('''
            SELECT COALESCE(SUM(total_amount), 0) AS gross_revenue
            FROM orders WHERE order_status IN ('processing', 'completed')
        ''')
        gross_revenue = cursor.fetchone()['gross_revenue'] or 0.0

        cursor.execute('''
            SELECT COALESCE(SUM(r.amount), 0) AS total_refunds
            FROM refunds r
            JOIN orders o ON r.order_id = o.id
            WHERE o.order_status IN ('processing', 'completed')
        ''')
        total_refunds = cursor.fetchone()['total_refunds'] or 0.0

        net_revenue = gross_revenue - total_refunds
        product_cogs = _calc_cogs(cursor, _ALL_TIME_START, now_str)

        try:
            cursor.execute('''
                SELECT COALESCE(SUM(shipping_fee), 0) AS seller_shipping
                FROM orders
                WHERE order_status IN ('processing', 'completed') AND shipping_paid_by = 'seller'
            ''')
            seller_shipping = cursor.fetchone()['seller_shipping'] or 0.0
        except Exception:
            seller_shipping = 0.0  # column absent on very old DBs

        total_costs = product_cogs + seller_shipping
        gross_profit = net_revenue - total_costs
        inventory_adjustments = _calc_inventory_adjustments(cursor, _ALL_TIME_START, now_str)
        adjusted_profit = gross_profit - inventory_adjustments
        overall_margin = (gross_profit / net_revenue * 100) if net_revenue > 0 else 0.0
        adjusted_margin = (adjusted_profit / net_revenue * 100) if net_revenue > 0 else 0.0

        overall_pl = {
            'total_revenue': net_revenue,
            'total_cogs': total_costs,
            'inventory_adjustments': inventory_adjustments,
            'gross_profit': gross_profit,
            'adjusted_profit': adjusted_profit,
        }

        # ── P&L by Month (last 12 months, newest first) ───────────────────────

        by_month = []
        now = datetime.now()
        for i in range(0, 12):  # i=0 → current month, i=11 → 11 months ago
            target_month = now.month - i
            target_year = now.year
            while target_month <= 0:
                target_month += 12
                target_year -= 1

            month_start = datetime(target_year, target_month, 1, 0, 0, 0)
            if target_month == 12:
                month_end = datetime(target_year + 1, 1, 1) - timedelta(seconds=1)
            else:
                month_end = datetime(target_year, target_month + 1, 1) - timedelta(seconds=1)

            ms = month_start.strftime('%Y-%m-%d %H:%M:%S')
            me = month_end.strftime('%Y-%m-%d %H:%M:%S')

            cursor.execute('''
                SELECT COUNT(*) AS order_count, COALESCE(SUM(total_amount), 0) AS revenue
                FROM orders
                WHERE order_status IN ('processing', 'completed') AND order_date >= ? AND order_date <= ?
            ''', (ms, me))
            row = cursor.fetchone()
            month_gross = row['revenue'] or 0.0
            order_count = row['order_count'] or 0

            cursor.execute('''
                SELECT COALESCE(SUM(r.amount), 0) AS refunds
                FROM refunds r
                JOIN orders o ON r.order_id = o.id
                WHERE o.order_status IN ('processing', 'completed')
                  AND r.refund_date >= ? AND r.refund_date <= ?
            ''', (ms, me))
            month_refunds = cursor.fetchone()['refunds'] or 0.0

            month_net = month_gross - month_refunds
            month_cogs = _calc_cogs(cursor, ms, me)

            try:
                cursor.execute('''
                    SELECT COALESCE(SUM(shipping_fee), 0) AS s
                    FROM orders
                    WHERE order_status IN ('processing', 'completed') AND shipping_paid_by = 'seller'
                      AND order_date >= ? AND order_date <= ?
                ''', (ms, me))
                month_ship = cursor.fetchone()['s'] or 0.0
            except Exception:
                month_ship = 0.0

            month_total_costs = month_cogs + month_ship
            month_adjustments = _calc_inventory_adjustments(cursor, ms, me)
            by_month.append({
                'month': month_start.strftime('%Y-%m'),
                'order_count': order_count,
                'revenue': month_net,
                'cogs': month_total_costs,
                'inventory_adjustments': month_adjustments,
                'profit': month_net - month_total_costs,
                'adjusted_profit': month_net - month_total_costs - month_adjustments,
            })

        # ── P&L by Product ────────────────────────────────────────────────────
        #
        # COGS per product:
        #   fifo_cogs  = SUM of allocation records for this product (FIFO orders)
        #   legacy_cogs= SUM for order items in orders with NO allocation records
        # Total COGS = fifo_cogs + legacy_cogs (handles mixed FIFO/legacy history)

        cursor.execute('''
            SELECT
                p.id,
                p.product_code,
                p.name,
                COALESCE(SUM(oi.quantity), 0) AS units_sold,
                COALESCE(SUM(oi.line_total), 0) AS revenue,
                COALESCE((
                    SELECT SUM(oa.quantity_allocated * oa.cost_price_at_sale)
                    FROM order_allocations oa
                    JOIN orders o2 ON oa.order_id = o2.id
                    WHERE oa.product_id = p.id AND o2.order_status IN ('processing', 'completed')
                ), 0) AS fifo_cogs,
                COALESCE(SUM(
                    CASE WHEN NOT EXISTS (
                        SELECT 1 FROM order_allocations oa3 WHERE oa3.order_id = o.id
                    ) THEN oi.quantity * COALESCE(p.cost_price, 0)
                    ELSE 0
                    END
                ), 0) AS legacy_cogs
            FROM products p
            LEFT JOIN order_items oi ON p.id = oi.product_id
            LEFT JOIN orders o ON oi.order_id = o.id AND o.order_status IN ('processing', 'completed')
            WHERE p.is_active = 1
            GROUP BY p.id
            HAVING COALESCE(SUM(oi.quantity), 0) > 0
            ORDER BY revenue DESC
        ''')

        by_product = []
        for row in cursor.fetchall():
            cogs = (row['fifo_cogs'] or 0.0) + (row['legacy_cogs'] or 0.0)
            profit = (row['revenue'] or 0.0) - cogs
            by_product.append({
                'product_code': row['product_code'],
                'name': row['name'],
                'units_sold': row['units_sold'],
                'revenue': row['revenue'] or 0.0,
                'cogs': cogs,
                'profit': profit,
            })
        by_product.sort(key=lambda x: x['profit'], reverse=True)

        return render_template('report_profit.html',
                               overall_pl=overall_pl,
                               overall_margin=overall_margin,
                               adjusted_margin=adjusted_margin,
                               by_month=by_month,
                               by_product=by_product)
    finally:
        db.close()
