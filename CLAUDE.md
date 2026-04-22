# Inventory App v5 — CLAUDE.md

> This file is the single source of truth for Claude Code sessions on this project.
> Read it fully before making any changes. Update it whenever architecture changes.

---

## What This App Is

A self-hosted inventory management web app for a small Vietnamese e-commerce business.
Built with Flask + HTMX + SQLite. Runs on a Windows PC or NAS (accessible over the local network).

**Default URL:** `http://localhost:5000`
**Default password:** `admin123` (stored as plaintext in `settings.password_hash` — no hashing, by design for simplicity)
**Primary currency:** VND (Vietnamese Dong), with a live USD toggle

---

## How to Run

```bash
cd inventory_app_v5/
python app.py
# → Running on http://0.0.0.0:5000
```

Requirements: `pip install flask` (and whatever is in `requirements.txt`).
The SQLite database (`inventory.db`) is created automatically on first run via `init_db()`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask with Blueprints |
| Database | SQLite via `sqlite3` stdlib (no ORM) |
| Frontend | Jinja2 templates, HTMX 1.9 for partials |
| Auth | Simple session cookie + plaintext password |
| Uploads | Flask static file serving (`static/uploads/products/`) |

---

## Project Structure

```
inventory_app_v5/
├── app.py                  # App factory, filters, context processors, currency switch
├── auth.py                 # login_required decorator
├── db.py                   # Schema, init_db(), generate_code(), generate_product_code()
├── inventory.db            # SQLite database (do not commit)
├── requirements.txt
├── static/
│   ├── style.css
│   └── uploads/
│       └── products/       # Product photos, organised by product_id/
├── routes/
│   ├── categories.py
│   ├── customers.py        # Includes /generate-code API endpoint
│   ├── dashboard.py        # get_revenue_and_profit_data(), _calc_cogs(), get_monthly_revenue_data()
│   ├── imports.py
│   ├── inventory.py        # Intake CRUD; allows negative qty for backorders
│   ├── orders.py           # FIFO helpers: _deduct_inventory_fifo(), _restore_inventory_from_allocations()
│   ├── products.py         # Includes /generate-code API endpoint, photo upload
│   ├── refunds.py          # Full CRUD including edit + delete
│   ├── reports.py
│   ├── returns.py
│   └── settings.py         # Exchange rate, password, backup, DB reset
└── templates/
    ├── base.html            # Sidebar nav, currency toggle button, flash messages
    ├── partials/            # HTMX modal partials (loaded via hx-get, submitted via regular POST)
    │   ├── customer_form.html
    │   ├── intake_form.html
    │   ├── product_form.html
    │   └── ...
    └── ...                  # Full-page templates (extend base.html)
```

---

## Database Schema

### Key tables and their roles

**`settings`** — single row; holds `app_name`, `password_hash`, `default_currency`, `vnd_usd_rate`.

**`categories`** — `id`, `name` (UNIQUE), `description`.

**`products`** — `id`, `product_code` (UNIQUE), `name`, `category_id`, `sale_price`, `cost_price`, `barcode`, `min_stock_level`, `is_active`.
- `sale_price` / `cost_price` are **reference/default fields only** — they do NOT feed into revenue or COGS calculations. `sale_price` pre-fills the order form; `cost_price` pre-fills the intake form.

**`product_images`** — `id`, `product_id`, `filename`, `sort_order`. Max 3 per product. Files stored at `static/uploads/products/{product_id}/{uuid}.ext`.

**`customers`** — `id`, `customer_code` (UNIQUE), `name`, `email`, `phone`, `address`, `region`, `total_spent`, `outstanding_debt`, `is_active`.
- ⚠️ `outstanding_debt` is a **stale cached field** — do NOT use it for display. Customer debt is always computed live from orders + payments (see customers.py list query).
- `total_spent` is incremented on order Complete and decremented on Reopen.

**`inventory`** — `id`, `product_id`, `quantity`, `remaining_quantity`, `cost_price`, `shipping_cost`, `currency`, `intake_date`, `notes`.
- `quantity` = original intake amount (can be negative for backorders/pre-orders).
- `remaining_quantity` = what's left after FIFO deductions (tracks in real time).
- `shipping_cost` = cost to get the goods to the warehouse (separate from order shipping to customer).

**`orders`** — `id`, `order_code`, `customer_id`, `order_status` (draft/processing/completed/cancelled), `payment_status` (not_paid/partially_paid/fully_paid), `subtotal`, `discount_amount`, `shipping_fee`, `shipping_paid_by` (customer/seller), `total_amount`.
- `total_amount` = what the customer owes = `subtotal − discount + shipping_fee` (only if `shipping_paid_by = 'customer'`).

**`order_items`** — `id`, `order_id`, `product_id`, `quantity`, `unit_price`, `discount_percent`, `line_total`.
- `unit_price` is the **actual agreed sale price** entered per order — this is what drives revenue.

**`order_allocations`** — `id`, `order_id`, `inventory_lot_id`, `product_id`, `quantity_allocated`, `cost_price_at_sale`.
- Written when order → Completed (FIFO deduction). Deleted when order → Reopened.
- `cost_price_at_sale` = the `inventory.cost_price` of the specific lot consumed. This is the **authoritative COGS source**.

**`payments`** — `id`, `order_id`, `amount`, `payment_date`, `payment_method`, `notes`.

**`refunds`** — `id`, `refund_code`, `order_id`, `return_id` (nullable FK), `amount`, `refund_date`, `refund_method`, `reason`, `notes`.
- Full CRUD: create, edit (all fields except `refund_code`), delete.

**`returns`** / **`return_items`** — tracks physical returns of goods.

---

## Critical Accounting Rules

### Revenue
```
orders.total_amount  (denormalized, recomputed on every save)
 = SUM(order_items.unit_price × quantity × (1 − discount_pct/100))
   − discount_amount
   + shipping_fee  (only if shipping_paid_by = 'customer')
```
Dashboard sums `total_amount` across completed orders. `products.sale_price` is never used.

### COGS — FIFO (primary path)
```
SUM(order_allocations.quantity_allocated × cost_price_at_sale)
```
Only orders with allocation records use FIFO. Legacy orders (HD000001, HD000002) predate the allocation system and have no rows in `order_allocations`.

### COGS — Fallback (legacy orders only)
```python
# In _calc_cogs() — only fires when COUNT(allocations) = 0, NOT when SUM = 0
SUM(order_items.quantity × COALESCE(products.cost_price, AVG(inventory.cost_price), 0))
```
This is the only place `products.cost_price` affects financial calculations.

### Net Revenue
```
Gross Revenue (completed orders total_amount)
− Total Refunds (SUM refunds.amount linked to completed orders)
= Net Revenue
```

### Gross Profit
```
Net Revenue − COGS (FIFO) − Seller Shipping
```

### Customer Outstanding Debt (always computed live)
```sql
SELECT SUM(o.total_amount) FROM orders WHERE customer_id=? AND status='completed'
MINUS
SELECT SUM(p.amount) FROM payments p JOIN orders o ... WHERE customer_id=? AND status='completed'
```
Never trust `customers.outstanding_debt` column for display — it's a stale cache.

---

## FIFO Implementation (`routes/orders.py`)

### On Order → Completed: `_deduct_inventory_fifo(cursor, order_id, items)`
```sql
SELECT id, remaining_quantity, cost_price
FROM inventory
WHERE product_id = ? AND remaining_quantity > 0
ORDER BY intake_date ASC, id ASC   -- oldest lot first = FIFO
```
For each lot, takes `min(needed, lot.remaining_quantity)`, decrements `remaining_quantity`, and writes one `order_allocations` row capturing `cost_price_at_sale`. If stock is insufficient, warns but does NOT block (backorder scenario).

### On Order → Reopened: `_restore_inventory_from_allocations(cursor, order_id)`
Reads all `order_allocations` for the order, increments `remaining_quantity` on each exact source lot, then deletes all allocation rows. This is a precise reversal — stock goes back to the exact lot it came from.

---

## Code Generation Patterns

**Customer code:** `generate_code('KH')` → `KH000001`, `KH000002`, ... (sequential, DB-driven)
**Order code:** `generate_code('HD')` → `HD000001`, ...
**Return code:** `generate_code('TR')` → `TR000001`, ...
**Refund code:** `generate_code('RF')` → `RF000001`, ...
**Product code:** `generate_product_code(category_id)` → 2-letter category prefix + 4 digits (e.g. `EL0001`, `CL0001`). Falls back to `SP####` if no category.

All codes are unique, user-overridable at creation, and editable after creation (customer + product codes). The DB `UNIQUE` constraint enforces uniqueness — catch the exception and flash a clear error.

---

## DB Migration Pattern

`init_db()` in `db.py` uses a safe `_migrate_add_column()` helper to add columns to existing databases without breaking restarts:

```python
def _migrate_add_column(table, column, definition):
    try:
        cursor.execute(f'SELECT {column} FROM {table} LIMIT 1')
    except Exception:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
```

Always use this for any new column. Never use raw `ALTER TABLE` at the top level.

Migrations applied so far:
- `order_allocations.cost_price_at_sale`
- `orders.shipping_paid_by`
- `refunds.return_id`
- `inventory.shipping_cost`
- `products.product_code_custom` (unused, safe to ignore)

---

## Currency / Display

- Settings table stores `vnd_usd_rate` and `default_currency`.
- `app.before_request` loads `g.vnd_usd_rate` and `g.default_currency` each request.
- Session key `display_currency` ('VND' or 'USD') controls display. Toggle via `POST /switch-currency`.
- The `|vnd` Jinja filter reads session + g to format any number in the current display currency.
- All monetary values are stored in VND internally. USD display is conversion-only.

---

## Template / HTMX Patterns

**Modal partials** (`templates/partials/*.html`):
- Loaded via `hx-get` into `#modal-container`.
- Submitted via regular HTML `<form method="POST">` (not hx-post) — causes full page reload on submit.
- This means flash messages from errors inside modals redirect to the full list page (so flashes render in base.html). Do NOT redirect errors back to the partial URL.

**Partial tables** (`partials/*_table.html`):
- Returned for HTMX search/filter/pagination requests (`HX-Request` header check).
- Full page returned otherwise.

**Jinja2 gotcha:** Do NOT use backslash-escaping inside template expressions inside HTML attributes (e.g. `onclick="...{{ var\['key'\] }}"` breaks). Use `data-*` attributes instead:
```html
<button data-code="{{ refund.refund_code }}"
        onclick="return confirm('Delete ' + this.dataset.code + '?')">
```

---

## Known Legacy Data

`HD000001` and `HD000002` were created before the FIFO allocation system. They have no rows in `order_allocations`. Their COGS on the dashboard uses the fallback formula (`products.cost_price`). To fix: Reopen → Re-complete (triggers FIFO allocation). Or leave as-is.

---

## Features Implemented (as of April 2026)

- Full CRUD: Products, Categories, Customers, Inventory, Orders, Returns, Refunds, Payments, Reports
- **Product codes** — manual or category-based auto-gen (2-letter prefix + 4 digits)
- **Product photos** — up to 3 per product, stored in `static/uploads/products/`
- **Customer codes** — manual or auto-gen (`KH######`), editable
- **Inventory intake** — `shipping_cost` field (cost to warehouse, separate from order shipping)
- **Negative inventory** — allowed for backorder/pre-order scenarios; shown highlighted in amber
- **FIFO order completion** — deducts from oldest lot first; records allocation with per-lot cost
- **Order reopen** — precisely restores exact inventory lots
- **Payments** — add, edit, delete inline; payment_status auto-recalculates
- **Shipping modes** — Customer pays (added to order total) vs Seller pays (cost only, not in total)
- **Refunds** — full CRUD (create, edit all fields, delete); no manual recalc needed anywhere
- **Returns** — linked to refunds optionally
- **Currency toggle** — VND ↔ USD, session-based, rate from Settings
- **Customer debt** — computed live from DB, not from cached column
- **Dashboard** — Net Revenue, COGS (FIFO), Seller Shipping, Gross Profit, Margin; period filter
- **COGS fallback fix** — checks `COUNT(allocations) > 0` not `SUM > 0` (prevents silent override by `products.cost_price`)
- **DB migrations** — safe column additions via `_migrate_add_column()` on every startup

---

## Things NOT To Do

- Do not add `products.sale_price` or `products.cost_price` to any revenue or COGS query — they are defaults/references only.
- Do not trust `customers.outstanding_debt` column for display — always compute live.
- Do not use `PRAGMA journal_mode=WAL` without a try/except — some FUSE/NAS filesystems reject it.
- Do not use backslash escapes in Jinja2 attribute expressions — use `data-*` attributes.
- Do not redirect form errors back to the partial (modal) URL — redirect to the list page so flash messages render in `base.html`.
- Do not add a new DB column without using `_migrate_add_column()` — users have live databases.
