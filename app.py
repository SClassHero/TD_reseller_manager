import os
from flask import Flask, session, redirect, url_for, render_template, request, flash, g
from db import init_db, get_db

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Upload folder configuration
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'products')
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
    """Load app settings into g so filters and templates can access them."""
    try:
        db = get_db()
        try:
            row = db.execute('SELECT vnd_usd_rate, default_currency FROM settings LIMIT 1').fetchone()
            g.vnd_usd_rate = row['vnd_usd_rate'] if row else 24000.0
            g.default_currency = row['default_currency'] if row else 'VND'
        finally:
            db.close()
    except Exception:
        g.vnd_usd_rate = 24000.0
        g.default_currency = 'VND'


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
        usd = value / rate if rate else 0
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


# Currency switch route
@app.route('/switch-currency', methods=['POST'])
def switch_currency():
    current = session.get('display_currency', getattr(g, 'default_currency', 'VND'))
    session['display_currency'] = 'USD' if current == 'VND' else 'VND'
    return redirect(request.referrer or url_for('dashboard.dashboard'))


# Authentication routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        db = get_db()
        try:
            row = db.execute('SELECT password_hash FROM settings LIMIT 1').fetchone()
            stored = row['password_hash'] if row else 'admin123'
        finally:
            db.close()
        if password == stored:
            session['authenticated'] = True
            return redirect(url_for('dashboard.dashboard'))
        else:
            flash('Invalid password', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
def index():
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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
