"""
app.py — Flask application entry point.

Responsibilities:
  - Persistent secret key (stored in .secret_key, never committed to VCS)
  - CSRF protection: session token checked on every POST except /login
  - Currency toggle: VND ↔ USD, session-based via POST /switch-currency
  - Auto-backup: fires in a background thread when the configured interval has elapsed
  - Password recovery: one-time recovery code set in Settings; code rotates on use
  - Blueprint registration for all feature modules

Auth flow: /login authenticates against the users table (or settings.password_hash for
backward compatibility). Role is stored in session['role']: 'admin' or 'limited'.
"""
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, session, redirect, url_for, render_template, request, flash, g, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from db import init_db, get_db
from auth import current_role, enforce_limited_access, is_admin
from version import APP_VERSION

_log = logging.getLogger(__name__)
_backup_lock = threading.Lock()
_BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=os.path.join(_BASE_DIR, 'templates'),
    static_folder=os.path.join(_BASE_DIR, 'static'),
)

# Persistent secret key — survives restarts (stored in .secret_key or a configured data path)
_SECRET_KEY_FILE = os.environ.get(
    'INVENTORY_SECRET_KEY_FILE',
    os.path.join(os.path.dirname(__file__), '.secret_key')
)


def _read_secret_key(path):
    try:
        with open(path, 'rb') as f:
            key = f.read()
            return key if len(key) >= 24 else None
    except FileNotFoundError:
        return None


def _load_or_create_secret_key(path):
    """
    Load the persistent Flask signing key, creating it atomically when missing.

    Gunicorn imports the app in separate worker processes. A normal
    exists-then-write sequence can let two fresh workers create different keys,
    making login sessions invalid whenever the next request hits another worker.
    """
    secret_key_dir = os.path.dirname(path)
    if secret_key_dir:
        os.makedirs(secret_key_dir, exist_ok=True)

    existing = _read_secret_key(path)
    if existing:
        return existing

    new_key = os.urandom(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(new_key)
        return new_key
    except FileExistsError:
        # Another worker just created it. Wait briefly for the write to finish.
        for _ in range(20):
            existing = _read_secret_key(path)
            if existing:
                return existing
            time.sleep(0.05)

    # Last-resort recovery from an empty/corrupt key file.
    with open(path, 'wb') as f:
        f.write(new_key)
    return new_key


app.secret_key = _load_or_create_secret_key(_SECRET_KEY_FILE)

# Upload folder configuration
UPLOAD_FOLDER = os.environ.get(
    'INVENTORY_UPLOADS_DIR',
    os.path.join(_BASE_DIR, 'static', 'uploads', 'products')
)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Initialize database on startup
init_db()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.before_request
def load_settings():
    """Load app settings into g so filters, templates, and auto-backup can access them."""
    try:
        db = get_db()
        try:
            row = db.execute(
                'SELECT vnd_usd_rate, default_currency, '
                'auto_backup_enabled, auto_backup_frequency_hours, '
                'auto_backup_keep, last_auto_backup_at '
                'FROM settings LIMIT 1'
            ).fetchone()
            g.vnd_usd_rate = row['vnd_usd_rate'] if row else 24000.0
            g.default_currency = row['default_currency'] if row else 'VND'
            g.auto_backup_enabled = row['auto_backup_enabled'] if row else 1
            g.auto_backup_frequency_hours = row['auto_backup_frequency_hours'] if row else 168
            g.auto_backup_keep = row['auto_backup_keep'] if row else 12
            g.last_auto_backup_at = row['last_auto_backup_at'] if row else None
        finally:
            db.close()
    except Exception:
        g.vnd_usd_rate = 24000.0
        g.default_currency = 'VND'
        g.auto_backup_enabled = 1
        g.auto_backup_frequency_hours = 168
        g.auto_backup_keep = 12
        g.last_auto_backup_at = None


def _run_auto_backup(keep=12):
    """Background thread: create auto backup then enforce retention. Never raises."""
    if not _backup_lock.acquire(blocking=False):
        return  # Another backup already running
    try:
        from backup import create_backup, enforce_retention
        create_backup('auto')
        db = get_db()
        try:
            db.execute(
                'UPDATE settings SET last_auto_backup_at = ? WHERE id = 1',
                (datetime.now().isoformat(),)
            )
            db.commit()
        finally:
            db.close()
        enforce_retention(keep)
    except Exception as exc:
        _log.error('Auto-backup failed: %s', exc)
    finally:
        _backup_lock.release()


@app.before_request
def _maybe_auto_backup():
    """Fire an auto-backup in a background thread if one is due."""
    if request.endpoint in (None, 'static', 'login', 'recover'):
        return
    if app.config.get('TESTING'):
        return
    if not getattr(g, 'auto_backup_enabled', 1):
        return

    last_str = getattr(g, 'last_auto_backup_at', None)
    freq_hours = getattr(g, 'auto_backup_frequency_hours', 168)
    keep = getattr(g, 'auto_backup_keep', 12)

    if last_str:
        try:
            if datetime.now() - datetime.fromisoformat(last_str) < timedelta(hours=freq_hours):
                return  # Not due yet
        except Exception:
            pass  # Malformed timestamp — treat as never backed up

    threading.Thread(target=_run_auto_backup, args=(keep,), daemon=True).start()


# Template filters
@app.template_filter('vnd')
def format_vnd(value):
    """Format number in current display currency (VND or USD)."""
    display = session.get('display_currency', getattr(g, 'default_currency', 'VND'))
    if value is None:
        value = 0
    try:
        value = float(value)
    except (ValueError, TypeError):
        value = 0

    if display == 'USD':
        rate = getattr(g, 'vnd_usd_rate', 24000.0)
        usd = round(value / rate, 2) if rate else 0
        return f"${usd:,.2f}"
    else:
        return f"₫{value:,.0f}".replace(',', '.')


@app.template_filter('number')
def format_number(value):
    """Format number with thousand separators."""
    if value is None:
        return '0'
    try:
        value = float(value)
        return f"{value:,.0f}".replace(',', '.')
    except (ValueError, TypeError):
        return '0'


# ── CSRF protection ──────────────────────────────────────────────────────────

def _get_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

app.jinja_env.globals['csrf_token'] = _get_csrf_token


@app.before_request
def _check_csrf():
    if request.method != 'POST':
        return
    if request.endpoint == 'login':
        return  # login is pre-auth; no session token to validate
    token = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
    if not token or token != session.get('csrf_token'):
        flash('Invalid or expired request token. Please try again.', 'error')
        return redirect(request.referrer or url_for('dashboard.dashboard'))


@app.before_request
def _enforce_limited_access():
    return enforce_limited_access()


# Context processors
@app.context_processor
def inject_current_path():
    return {'current_path': request.path}


@app.context_processor
def inject_currency_info():
    """Inject currency display preference into all templates."""
    display = session.get('display_currency', getattr(g, 'default_currency', 'VND'))
    rate = getattr(g, 'vnd_usd_rate', 24000.0)
    return {
        'display_currency': display,
        'vnd_usd_rate': rate,
        'currency_symbol': '$' if display == 'USD' else '₫',
    }


@app.context_processor
def inject_auth_info():
    return {
        'current_role': current_role(),
        'is_admin': is_admin(),
    }


@app.context_processor
def inject_app_version():
    return {'app_version': APP_VERSION}


# Currency switch route
@app.route('/switch-currency', methods=['POST'])
def switch_currency():
    current = session.get('display_currency', getattr(g, 'default_currency', 'VND'))
    session['display_currency'] = 'USD' if current == 'VND' else 'VND'
    return redirect(request.referrer or url_for('dashboard.dashboard'))


@app.route('/product-uploads/<path:filename>')
def product_upload(filename):
    """Serve product photos from the configured persistent upload folder."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 60


# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    import time
    if request.method == 'POST':
        now = time.time()
        lockout_until = session.get('login_lockout_until', 0)
        if lockout_until and now < lockout_until:
            remaining = int(lockout_until - now)
            flash(f'Too many failed attempts. Try again in {remaining} second(s).', 'error')
            return render_template('login.html')

        username = (request.form.get('username') or 'admin').strip() or 'admin'
        password = request.form.get('password', '')
        db = get_db()
        try:
            user = db.execute(
                'SELECT id, username, password_hash, role FROM users '
                'WHERE lower(username) = lower(?) AND is_active = 1 LIMIT 1',
                (username,)
            ).fetchone()
            if user:
                stored = user['password_hash']
                user_id = user['id']
                canonical_username = user['username']
                role = user['role']
            elif username.lower() == 'admin':
                row = db.execute('SELECT password_hash FROM settings LIMIT 1').fetchone()
                stored = row['password_hash'] if row else None
                user_id = None
                canonical_username = 'admin'
                role = 'admin'
            else:
                stored = None
                user_id = None
                canonical_username = username
                role = None
        finally:
            db.close()

        if stored and check_password_hash(stored, password):
            session['authenticated'] = True
            session['user_id'] = user_id
            session['username'] = canonical_username
            session['role'] = role
            session.pop('login_attempts', None)
            session.pop('login_lockout_until', None)
            if role == 'limited':
                return redirect(url_for('orders.orders_list'))
            return redirect(url_for('dashboard.dashboard'))
        else:
            attempts = session.get('login_attempts', 0) + 1
            session['login_attempts'] = attempts
            if attempts >= _LOGIN_MAX_ATTEMPTS:
                session['login_lockout_until'] = now + _LOGIN_LOCKOUT_SECONDS
                session['login_attempts'] = 0
                flash(f'Too many failed attempts. Locked out for {_LOGIN_LOCKOUT_SECONDS} seconds.', 'error')
            else:
                remaining_attempts = _LOGIN_MAX_ATTEMPTS - attempts
                flash(f'Invalid password or account ({remaining_attempts} attempt(s) remaining).', 'error')
    return render_template('login.html')


@app.route('/login/recover', methods=['GET', 'POST'])
def recover():
    """Password recovery using the one-time recovery code set up in Settings."""
    import base64
    if request.method == 'POST':
        entered = request.form.get('recovery_code', '').strip().upper()
        entered = entered.replace('-', '').replace(' ', '')
        new_password = request.form.get('new_password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()

        if not entered or not new_password or not confirm:
            flash('All fields are required.', 'error')
            return render_template('recover.html')

        if new_password != confirm:
            flash('New passwords do not match.', 'error')
            return render_template('recover.html')

        if len(new_password) < 4:
            flash('Password must be at least 4 characters.', 'error')
            return render_template('recover.html')

        db = get_db()
        try:
            row = db.execute('SELECT id, recovery_code_hash FROM settings LIMIT 1').fetchone()
            if not row or not row['recovery_code_hash']:
                flash('No recovery code has been configured. Ask someone with access to generate one in Settings.', 'error')
                return render_template('recover.html')

            if not check_password_hash(row['recovery_code_hash'], entered):
                flash('Recovery code is incorrect.', 'error')
                return render_template('recover.html')

            # Valid — reset password and rotate the recovery code immediately
            new_pw_hash = generate_password_hash(new_password)
            raw = secrets.token_bytes(12)
            new_code = base64.b32encode(raw).decode()[:20]  # 20 chars, no padding
            new_code_fmt = '-'.join(new_code[i:i+4] for i in range(0, 20, 4))
            new_code_hash = generate_password_hash(new_code)

            db.execute(
                'UPDATE settings SET password_hash = ?, recovery_code_hash = ? WHERE id = ?',
                (new_pw_hash, new_code_hash, row['id'])
            )
            db.execute(
                "UPDATE users SET password_hash = ?, is_active = 1 WHERE role = 'admin'",
                (new_pw_hash,)
            )
            db.commit()

            # Clear any login lockout from the current session
            session.pop('login_attempts', None)
            session.pop('login_lockout_until', None)

            return render_template('recovery_success.html', new_code=new_code_fmt)
        finally:
            db.close()

    return render_template('recover.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
def index():
    if session.get('authenticated') and current_role() == 'limited':
        return redirect(url_for('orders.orders_list'))
    return redirect(url_for('dashboard.dashboard'))


# Register blueprints
from routes.dashboard import dashboard_bp
from routes.categories import categories_bp
from routes.products import products_bp
from routes.customers import customers_bp
from routes.inventory import inventory_bp
from routes.orders import orders_bp
from routes.returns import returns_bp
from routes.refunds import refunds_bp
from routes.reports import reports_bp
from routes.imports import imports_bp
from routes.settings import settings_bp
from routes.exports import exports_bp

app.register_blueprint(dashboard_bp)
app.register_blueprint(categories_bp)
app.register_blueprint(products_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(inventory_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(returns_bp)
app.register_blueprint(refunds_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(imports_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(exports_bp)


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=debug)
