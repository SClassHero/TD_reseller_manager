"""
routes/exports.py — Export data to CSV or Excel (XLSX).

Supported exports:
  /exports/               index page
  /exports/<type>.csv     CSV download
  /exports/<type>.xlsx    Excel download (requires openpyxl)

Types: products, customers, orders, inventory

Design notes:
  - CSV uses Python stdlib csv — no extra dependencies.
  - XLSX uses openpyxl — must be installed (pip install openpyxl).
  - All monetary values are exported in VND (raw integers) for portability.
  - Dates exported in ISO format (YYYY-MM-DD) for easy re-import.
"""
import csv
import io
import logging
from datetime import datetime

from flask import Blueprint, render_template, Response, flash, redirect, url_for
from db import get_db
from auth import login_required

_log = logging.getLogger(__name__)

exports_bp = Blueprint('exports', __name__, url_prefix='/exports')

# ── Helpers ───────────────────────────────────────────────────────────────────

def _csv_response(filename, headers, rows):
    """Return a streaming CSV download response."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    output = buf.getvalue()
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


def _xlsx_response(filename, sheets):
    """
    Return an XLSX download response.
    sheets: list of (sheet_name, headers, rows)
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        raise RuntimeError('openpyxl not installed — run: pip install openpyxl')

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default empty sheet

    header_fill = PatternFill(start_color='1E40AF', end_color='1E40AF', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)

    for sheet_name, headers, rows in sheets:
        ws = wb.create_sheet(title=sheet_name[:31])  # Excel sheet name limit: 31 chars
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        for row in rows:
            ws.append(list(row))
        # Auto-size columns (approximate)
        for col in ws.columns:
            max_len = max((len(str(cell.value or '')) for cell in col), default=10)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


def _fmt_date(val):
    """Format a datetime string or None for export."""
    if not val:
        return ''
    return str(val)[:10]


# ── Data fetchers ─────────────────────────────────────────────────────────────

def _fetch_products(db):
    headers = ['Product Code', 'Name', 'Category', 'Sale Price (VND)', 'Cost Price (VND)',
               'Barcode', 'Min Stock Level', 'Current Stock', 'Active']
    rows = db.execute('''
        SELECT p.product_code, p.name, c.name AS category,
               COALESCE(p.sale_price, 0), COALESCE(p.cost_price, 0),
               COALESCE(p.barcode, ''),
               COALESCE(p.min_stock_level, 0),
               COALESCE(SUM(i.remaining_quantity), 0) AS current_stock,
               CASE WHEN p.is_active = 1 THEN 'Yes' ELSE 'No' END
        FROM products p
        LEFT JOIN categories c ON p.category_id = c.id
        LEFT JOIN inventory i ON p.id = i.product_id
        GROUP BY p.id
        ORDER BY p.product_code
    ''').fetchall()
    return headers, [tuple(r) for r in rows]


def _fetch_customers(db):
    headers = ['Customer Code', 'Name', 'Email', 'Phone', 'Address', 'Region',
               'Total Orders', 'Total Spent (VND)', 'Outstanding Debt (VND)', 'Active']
    rows = db.execute('''
        SELECT c.customer_code, c.name,
               COALESCE(c.email, ''), COALESCE(c.phone, ''),
               COALESCE(c.address, ''), COALESCE(c.region, ''),
               COUNT(DISTINCT o.id) AS total_orders,
               COALESCE(SUM(CASE WHEN o.order_status IN ('processing', 'completed') THEN o.total_amount ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN o.order_status IN ('processing', 'completed') THEN o.total_amount ELSE 0 END), 0)
               - COALESCE((
                   SELECT SUM(p2.amount) FROM payments p2
                   JOIN orders o2 ON p2.order_id = o2.id
                   WHERE o2.customer_id = c.id AND o2.order_status IN ('processing', 'completed')
               ), 0),
               CASE WHEN c.is_active = 1 THEN 'Yes' ELSE 'No' END
        FROM customers c
        LEFT JOIN orders o ON c.id = o.customer_id
        GROUP BY c.id
        ORDER BY c.customer_code
    ''').fetchall()
    return headers, [tuple(r) for r in rows]


def _fetch_orders(db):
    headers = ['Order Code', 'Date', 'Customer', 'Status', 'Payment Status',
               'Subtotal (VND)', 'Discount (VND)', 'Shipping Fee (VND)', 'Shipping Paid By',
               'Total Amount (VND)', 'Amount Paid (VND)', 'Notes']
    rows = db.execute('''
        SELECT o.order_code, o.order_date, c.name,
               o.order_status, o.payment_status,
               COALESCE(o.subtotal, 0),
               COALESCE(o.discount_amount, 0),
               COALESCE(o.shipping_fee, 0),
               COALESCE(o.shipping_paid_by, 'customer'),
               COALESCE(o.total_amount, 0),
               COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id), 0),
               COALESCE(o.notes, '')
        FROM orders o
        JOIN customers c ON o.customer_id = c.id
        ORDER BY o.order_date DESC, o.id DESC
    ''').fetchall()
    return headers, [tuple(r) for r in rows]


def _fetch_inventory(db):
    headers = ['Product Code', 'Product Name', 'Quantity (Original)', 'Remaining Quantity',
               'Cost Price (VND)', 'Shipping to Warehouse (VND)', 'Currency', 'Intake Date', 'Notes']
    rows = db.execute('''
        SELECT p.product_code, p.name,
               i.quantity, i.remaining_quantity,
               COALESCE(i.cost_price, 0),
               COALESCE(i.shipping_cost, 0),
               COALESCE(i.currency, 'VND'),
               COALESCE(i.intake_date, ''),
               COALESCE(i.notes, '')
        FROM inventory i
        JOIN products p ON i.product_id = p.id
        ORDER BY p.product_code, i.intake_date ASC, i.id ASC
    ''').fetchall()
    return headers, [tuple(r) for r in rows]


_FETCHERS = {
    'products':  _fetch_products,
    'customers': _fetch_customers,
    'orders':    _fetch_orders,
    'inventory': _fetch_inventory,
}

_DISPLAY_NAMES = {
    'products':  'Products',
    'customers': 'Customers',
    'orders':    'Orders',
    'inventory': 'Inventory',
}


# ── Routes ────────────────────────────────────────────────────────────────────

@exports_bp.route('/')
@login_required
def exports_index():
    return render_template('exports.html')


@exports_bp.route('/<export_type>.csv')
@login_required
def export_csv(export_type):
    if export_type not in _FETCHERS:
        flash(f'Unknown export type: {export_type}', 'error')
        return redirect(url_for('exports.exports_index'))
    db = get_db()
    try:
        headers, rows = _FETCHERS[export_type](db)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return _csv_response(f'{export_type}_{ts}.csv', headers, rows)
    finally:
        db.close()


@exports_bp.route('/<export_type>.xlsx')
@login_required
def export_xlsx(export_type):
    if export_type not in _FETCHERS:
        flash(f'Unknown export type: {export_type}', 'error')
        return redirect(url_for('exports.exports_index'))
    db = get_db()
    try:
        headers, rows = _FETCHERS[export_type](db)
        sheet_name = _DISPLAY_NAMES[export_type]
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return _xlsx_response(f'{export_type}_{ts}.xlsx', [(sheet_name, headers, rows)])
    except RuntimeError as e:
        flash(str(e), 'error')
        return redirect(url_for('exports.exports_index'))
    finally:
        db.close()


@exports_bp.route('/all.xlsx')
@login_required
def export_all_xlsx():
    """Export all tables in a single multi-sheet Excel workbook."""
    db = get_db()
    try:
        sheets = []
        for key in ('products', 'customers', 'orders', 'inventory'):
            headers, rows = _FETCHERS[key](db)
            sheets.append((_DISPLAY_NAMES[key], headers, rows))
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return _xlsx_response(f'inventory_export_{ts}.xlsx', sheets)
    except RuntimeError as e:
        flash(str(e), 'error')
        return redirect(url_for('exports.exports_index'))
    finally:
        db.close()
