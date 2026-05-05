"""
auth.py — Authentication decorators and role-based access guard.

Roles:
  admin   — full access to all pages and write actions
  limited — view-only: Products, Inventory, Customers, Orders (detail included)
             Blocked from: Dashboard, Reports, Settings, Imports/Exports, Returns, Refunds,
             any new/edit/delete form, and all POST requests

enforce_limited_access() is registered as a before_request hook in app.py and runs on
every request. Old sessions without a 'role' key default to 'admin' for backward compat.
"""
from functools import wraps
from flask import flash, redirect, request, session, url_for


# Pages a 'limited' account may open (GET only)
LIMITED_ALLOWED_ENDPOINTS = {
    'products.products_list',
    'customers.customers_list',
    'customers.customer_detail',
    'inventory.inventory_list',
    'orders.orders_list',
    'orders.order_detail',
}

ALWAYS_ALLOWED_ENDPOINTS = {
    None,
    'static',
    'login',
    'recover',
    'logout',
    'switch_currency',
}


def current_role():
    """Return the active session role. Old sessions without a role are admin."""
    if not session.get('authenticated'):
        return None
    return session.get('role') or 'admin'


def is_admin():
    return current_role() == 'admin'


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login'))
        if not is_admin():
            flash('This account is view-only for products, inventory, customers, and orders.', 'warning')
            return redirect(url_for('orders.orders_list'))
        return f(*args, **kwargs)
    return decorated_function


def enforce_limited_access():
    """Global guard for the limited view-only account."""
    if not session.get('authenticated') or is_admin():
        return None

    endpoint = request.endpoint
    if endpoint in ALWAYS_ALLOWED_ENDPOINTS:
        return None

    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        flash('This account can view records but cannot make changes.', 'warning')
        return redirect(url_for('orders.orders_list'))

    if endpoint in LIMITED_ALLOWED_ENDPOINTS:
        return None

    flash('This account is view-only for products, inventory, customers, and orders.', 'warning')
    return redirect(url_for('orders.orders_list'))
