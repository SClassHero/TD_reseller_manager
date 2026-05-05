# Inventory App v5 — CLAUDE.md

> This file is the single source of truth for Claude Code sessions on this project.
> Read it fully before making any changes. Update it whenever architecture changes.

---

## What This App Is

A self-hosted inventory management web app for a small Vietnamese e-commerce business.
Built with Flask + HTMX + SQLite. Runs on a Windows PC or NAS (accessible over the local network).

**Default URL:** `http://localhost:5000`
**Default admin login:** username `admin`, password `admin123` (stored as a Werkzeug password hash)
**Primary currency:** VND (Vietnamese Dong), with a live USD toggle

**Current auth note:** Login uses the `users` table. `settings.password_hash` is still mirrored for the admin account so password recovery and old backups remain compatible. Old plaintext values are migrated to Werkzeug hashes on startup.

---

## How to Run

```bash
cd inventory_app_v5/
python app.py
# → Running on http://0.0.0.0:5000
```

Requirements: `pip install -r requirements.txt` (Flask + openpyxl for Excel exports).
The SQLite database (`inventory.db`) is created automatically on first run via `init_db()`.
openpyxl is only needed for XLSX export; CSV export works without it.

---

## How to Test

```bash
python test_app.py                    # 396 tests: app CRUD, role access, mobile markup, exports, CSRF
python test_backup.py                 # 16 tests: backup/restore/reset/deployment smoke tests
python test_real_world_scenarios.py   # 129 tests: golden-ledger FIFO/accounting
python test_edge_cases.py             # 78 tests: cancellation, guards, cumulative validation
python test_edge_cases_2.py           # 53 tests: partial returns, overpayment, recovery code
```

Total: 672 tests, all passing. Auto-backup is suppressed under Flask `TESTING` mode.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask with Blueprints |
| Database | SQLite via `sqlite3` stdlib (no ORM) |
| Frontend | Jinja2 templates, HTMX 1.9 for partials |
| Auth | Simple session cookie + `users` table + Werkzeug password hashes |
| Uploads | Flask static file serving (`static/uploads/products/`) |

---

## Project Structure

```
inventory_app_v5/
├── app.py                  # App factory, filters, context processors, currency switch
├── auth.py                 # login/admin decorators and limited-role guard
├── list_utils.py           # Shared list search/sort/pagination helpers
├── db.py                   # Schema, init_db(), generate_code(), generate_product_code()
├── Dockerfile              # Container image for Synology/Container Manager deployment
├── docker-compose.synology.yml
├── DEPLOY_SYNOLOGY.md      # Step-by-step DSM 7 deployment guide
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
│   ├── exports.py          # CSV and XLSX export for Products/Customers/Orders/Inventory
│   ├── imports.py
│   ├── inventory.py        # Intake CRUD; allows negative qty for backorders
│   ├── orders.py           # FIFO helpers: _deduct_inventory_fifo(), _restore_inventory_from_allocations()
│   ├── products.py         # Includes /generate-code API endpoint, photo upload
│   ├── refunds.py          # Full CRUD including edit + delete
│   ├── reports.py          # Sales, Inventory, Profit (FIFO COGS), Customer Debt Aging
│   ├── returns.py
│   └── settings.py         # Exchange rate, passwords/accounts, backup, DB reset
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

**`settings`** — single row; holds `app_name`, mirrored admin `password_hash`, `default_currency`, `vnd_usd_rate`, backup prefs, and recovery code hash.

**`users`** — login accounts: `username` (UNIQUE), `password_hash`, `role` (`admin` or `limited`), `is_active`.
- `admin` has full access. On migration, the first admin user is seeded from `settings.password_hash`.
- `limited` is view-only: Products, Inventory, Customers, Orders, order detail, and customer detail. It cannot access Dashboard, Reports, Settings, Imports/Exports, Returns, Refunds, new/edit forms, or any POST changes.

**`categories`** — `id`, `name` (UNIQUE), `description`.

**`products`** — `id`, `product_code` (UNIQUE), `name`, `category_id`, `sale_price`, `cost_price`, `barcode`, `min_stock_level`, `is_active`.
- `sale_price` / `cost_price` are **reference/default fields only** — they do NOT feed into revenue or COGS calculations. `sale_price` pre-fills the order form; `cost_price` pre-fills the intake form.

**`product_images`** — `id`, `product_id`, `filename`, `sort_order`. Max 3 per product. Files stored at `static/uploads/products/{product_id}/{uuid}.ext`.

**`customers`** — `id`, `customer_code` (UNIQUE), `name`, `email`, `phone`, `address`, `region`, `total_spent`, `outstanding_debt`, `is_active`.
- ⚠️ `outstanding_debt` is a **stale cached field** — do NOT use it for display. Customer debt is always computed live from orders + payments (see customers.py list query).
- `total_spent` is incremented when an order first becomes an active sale (`processing` or `completed`) and decremented when that active sale is reopened to `draft` or cancelled.

**`inventory`** — `id`, `product_id`, `quantity`, `remaining_quantity`, `cost_price`, `shipping_cost`, `currency`, `intake_date`, `notes`.
- `quantity` = original intake amount (can be negative for backorders/pre-orders).
- `remaining_quantity` = what's left after FIFO deductions (tracks in real time).
- `shipping_cost` = cost to get the goods to the warehouse (separate from order shipping to customer).

**`orders`** — `id`, `order_code`, `customer_id`, `order_status` (draft/processing/completed/cancelled), `payment_status` (not_paid/partially_paid/fully_paid), `subtotal`, `discount_amount`, `shipping_fee`, `shipping_paid_by` (customer/seller), `total_amount`.
- `total_amount` = what the customer owes = `subtotal − discount + shipping_fee` (only if `shipping_paid_by = 'customer'`).
- `draft` has no stock/accounting effect and is the only status that can be edited or deleted.
- `processing` and `completed` are active sale statuses: they reserve/deduct stock through FIFO allocations and count in revenue, COGS, customer debt, reports, exports, and dashboard totals.

**`order_items`** — `id`, `order_id`, `product_id`, `quantity`, `unit_price`, `discount_percent`, `line_total`.
- `unit_price` is the **actual agreed sale price** entered per order — this is what drives revenue.

**`order_allocations`** — `id`, `order_id`, `inventory_lot_id`, `product_id`, `quantity_allocated`, `cost_price_at_sale`.
- Written when an order first moves from `draft` into `processing` or `completed`. If a Processing backorder later moves to Completed after stock arrives, any still-unallocated quantity is allocated then. Deleted when the active sale is reopened to `draft` or cancelled.
- `cost_price_at_sale` = the `inventory.cost_price` of the specific lot consumed. This is the **authoritative COGS source**.

**`payments`** — `id`, `order_id`, `amount`, `payment_date`, `payment_method`, `notes`.

**`refunds`** — `id`, `refund_code`, `order_id`, `return_id` (nullable FK), `amount`, `refund_date`, `refund_method`, `reason`, `notes`.
- Full CRUD: create, edit (all fields except `refund_code`), delete.

**`returns`** / **`return_items`** — tracks physical returns of goods. `return_items` also stores returned-goods restock metadata: `restock_action`, `restock_quantity`, `restock_unit_cost`, `restock_shipping_cost`, and `restock_inventory_lot_id`.

**`inventory_adjustments`** — auditable stock write-offs/adjustments by inventory lot. Uses `AD######` codes. Stores `inventory_lot_id`, `product_id`, optional `return_item_id`, signed `quantity_delta`, `unit_cost`, `total_cost`, `reason`, `notes`, and `adjustment_date`.

---

## Critical Accounting Rules

### Revenue
```
orders.total_amount  (denormalized, recomputed on every save)
 = SUM(order_items.unit_price × quantity × (1 − discount_pct/100))
   − discount_amount
   + shipping_fee  (only if shipping_paid_by = 'customer')
```
Dashboard/reports sum `total_amount` across active sale orders (`processing`, `completed`). `products.sale_price` is never used.

### COGS — FIFO (primary path)
```
SUM(order_allocations.quantity_allocated × cost_price_at_sale)
```
Only orders with allocation records use FIFO. Legacy orders (HD000001, HD000002) predate the allocation system and have no rows in `order_allocations`.

### COGS — Fallback (legacy orders only)
```python
# In _calc_cogs() — applies per active sale order only when that order has no allocations
SUM(order_items.quantity × COALESCE(products.cost_price, AVG(inventory.cost_price), 0))
```
Mixed periods are valid: FIFO orders contribute allocation COGS, while known legacy no-allocation orders (`HD000001`, `HD000002`) contribute fallback COGS. Modern zero-stock/backorder completions with no allocations do not use fallback COGS. This is the only place `products.cost_price` affects financial calculations.

### Net Revenue
```
Gross Revenue (processing/completed orders total_amount)
− Total Refunds (SUM refunds.amount linked to processing/completed orders)
= Net Revenue
```

### Gross Profit
```
Net Revenue − COGS (FIFO) − Seller Shipping
```

### Inventory Write-offs
Inventory write-offs are **not** sales COGS and do not change order revenue, refunds, customer debt, or FIFO allocations. They reduce `inventory.remaining_quantity` for one lot and create an `inventory_adjustments` row with `quantity_delta < 0`.

Write-off `unit_cost` uses the same economic cost basis as FIFO: `inventory.cost_price + shipping_cost / quantity` when the lot quantity is positive. Dashboard and Profit Report show write-offs separately as `inventory_adjustments`; `gross_profit` remains sales profit, and `adjusted_profit` subtracts write-offs.

### Customer Outstanding Debt (always computed live)
```sql
SELECT SUM(o.total_amount) FROM orders WHERE customer_id=? AND status IN ('processing','completed')
MINUS
SELECT SUM(p.amount) FROM payments p JOIN orders o ... WHERE customer_id=? AND status IN ('processing','completed')
```
Never trust `customers.outstanding_debt` column for display — it's a stale cache.

---

## FIFO Implementation (`routes/orders.py`)

### On Draft → Processing/Completed: `_deduct_inventory_fifo(cursor, order_id, items)`
```sql
SELECT id, remaining_quantity, cost_price
FROM inventory
WHERE product_id = ? AND remaining_quantity > 0
ORDER BY intake_date ASC, id ASC   -- oldest lot first = FIFO
```
For each lot, takes `min(needed, lot.remaining_quantity)`, decrements `remaining_quantity`, and writes one `order_allocations` row capturing `cost_price_at_sale`. If stock is insufficient, warns but does NOT block (backorder scenario). The order still becomes an active sale; unallocated COGS remains 0 until stock is later allocated.

### On Processing → Completed: `_allocate_unreserved_stock(cursor, order_id)`
Attempts FIFO allocation for any order quantity not already allocated. This supports a Processing backorder where stock arrives after the order was accepted.

### On Active Sale → Draft/Cancelled: `_restore_inventory_from_allocations(cursor, order_id)`
Reads all `order_allocations` for the order, increments `remaining_quantity` on each exact source lot, then deletes all allocation rows. This is a precise reversal for order cancellation/reopen — stock goes back to the exact lot it came from.

### Returns and Restock Accounting
Returns do **not** restore into original FIFO lots. Restocked goods create a new intake lot (`cost_price` defaults to 0). Original sale COGS and seller shipping are unaffected. After-the-fact restock is available from Return Detail for items without a linked lot. Returned-goods lots can be written off from Return Detail; write-off qty must be ≤ lot's current `remaining_quantity`.

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

Use `_migrate_add_column(table, column, definition)` in `db.py` for every new column. Never use raw `ALTER TABLE` at the top level. See `SCHEMA_CHANGELOG.md` for migration history.

### Schema versioning — CRITICAL for backup/restore compatibility

`db.py` exports `SCHEMA_VERSION = <int>`. Every structural schema change must:

1. Use `_migrate_add_column()` for new columns, `CREATE TABLE IF NOT EXISTS` for new tables
2. **Increment `SCHEMA_VERSION`** in `db.py`
3. **Add an entry to `SCHEMA_CHANGELOG.md`** describing what changed
4. Test: backup at old version → apply change → restore → verify data + new columns have correct defaults

**Never do:** raw `ALTER TABLE`, dropping columns, renaming columns, or changing column types without a data transformation migration.

Backups embed `schema_version` in their filename and `backup_info.json`. When restoring an older backup into a newer app, `init_db()` is automatically called to apply missing migrations. This is why the `_migrate_add_column` + `CREATE TABLE IF NOT EXISTS` pattern is mandatory — it is the migration engine.

---

## Currency / Display

- Settings table stores `vnd_usd_rate` and `default_currency`.
- `app.before_request` loads `g.vnd_usd_rate` and `g.default_currency` each request.
- Session key `display_currency` ('VND' or 'USD') controls display. Toggle via `POST /switch-currency`.
- The `|vnd` Jinja filter reads session + g to format any number in the current display currency.
- All monetary values are stored in VND internally. USD display is conversion-only.
- Inventory intake with `currency = 'USD'` converts submitted unit cost and inbound shipping to VND using `settings.vnd_usd_rate`; `inventory.currency` stores the source currency label and `inventory.exchange_rate` stores the rate used.

---

## Deployment Runtime Paths

Local development still defaults to repo-relative runtime files:

- `inventory.db`
- `.secret_key`
- `backups/`
- `static/uploads/products/`

For Synology Container Manager/Docker, these can be redirected into a mounted data volume with environment variables:

- `INVENTORY_DB`
- `INVENTORY_SECRET_KEY_FILE`
- `INVENTORY_BACKUPS_DIR`
- `INVENTORY_UPLOADS_DIR`

`docker-compose.synology.yml` maps DB/backups/secret key to `/data`; it also mounts `./data/uploads/products` into `/app/static/uploads/products` so product photos persist while still being served by Flask's static route. See `DEPLOY_SYNOLOGY.md`.

---

## Template / HTMX Patterns

**Modal partials** (`templates/partials/*.html`):
- Loaded via `hx-get` into `#modal-container`.
- Submitted via regular HTML `<form method="POST">` (not hx-post) — causes full page reload on submit.
- This means flash messages from errors inside modals redirect to the full list page (so flashes render in base.html). Do NOT redirect errors back to the partial URL.

**Partial tables** (`partials/*_table.html`):
- Returned for HTMX search/filter/pagination requests (`HX-Request` header check).
- Full page returned otherwise.

**List browsing controls**:
- Products, Categories, Customers, Inventory, Orders, Returns, and Refunds use GET-based search/filter/sort/per-page controls so browser refresh/back/bookmark behavior is predictable on desktop and mobile.
- Route code must validate user-controlled sort keys through per-route allowlists and `list_utils.py`; never interpolate raw query-string sort values into SQL.
- Use `templates/partials/list_pagination.html` for visible result counts and compact Previous/Next/page links. Preserve active search/filter/sort/per-page parameters in `pagination_params`.

**Mobile tables**: Add `mobile-card-table` to every `<table class="table">`. CSS converts rows to labeled cards on phone widths; `applyMobileTableLabels()` in `base.html` fills missing `data-label` attributes on load and after HTMX swaps. Modal partials use `.modal-body`/`.modal-footer` and `.modal-field-grid`/`.modal-code-row` for responsive layout.

**Jinja2 gotcha:** Do NOT use backslash-escaping inside template expressions inside HTML attributes (e.g. `onclick="...{{ var\['key'\] }}"` breaks). Use `data-*` attributes instead:
```html
<button data-code="{{ refund.refund_code }}"
        onclick="return confirm('Delete ' + this.dataset.code + '?')">
```

---

## Known Legacy Data

`HD000001` and `HD000002` were created before the FIFO allocation system. They have no rows in `order_allocations`. Their COGS on the dashboard uses the fallback formula (`products.cost_price`). To fix: Reopen → Re-complete (triggers FIFO allocation). Or leave as-is.

---

## Implementation Status

All major features are complete and tested (672 tests passing). Full CRUD for Products, Categories, Customers, Inventory, Orders, Returns, Refunds, Payments, Reports, Export, Import. FIFO stock allocation, order state machine, customer debt, write-offs, multi-role auth, backup/restore, mobile-responsive UI, Synology Container Manager/NAS deployment. See test files and the sections above for behavioral details.

---

## Things NOT To Do

- Do not add `products.sale_price` or `products.cost_price` to any revenue or COGS query — they are defaults/references only.
- Do not trust `customers.outstanding_debt` column for display — always compute live.
- Do not use `PRAGMA journal_mode=WAL` without a try/except — some FUSE/NAS filesystems reject it.
- Do not use backslash escapes in Jinja2 attribute expressions — use `data-*` attributes.
- Do not redirect form errors back to the partial (modal) URL — redirect to the list page so flash messages render in `base.html`.
- Do not add a new DB column without using `_migrate_add_column()` — users have live databases.
- Do not increment `SCHEMA_VERSION` without updating `SCHEMA_CHANGELOG.md` — they must stay in sync.
- Do not make destructive schema changes (DROP COLUMN, RENAME COLUMN) without a data migration path.
- Do not compute inventory stock value using `products.cost_price` — always use `inventory.cost_price * remaining_quantity` per lot.
- Do not compute COGS using `products.cost_price` — always use FIFO allocations first; legacy fallback only applies to known legacy no-allocation orders (`HD000001`, `HD000002`).
- Do not restore returned goods directly into the original FIFO lots. Use returned-goods intake lots so original sale COGS remains on the failed sale and resale does not double-count original purchase cost.
- Do not represent damaged/lost stock with negative intake unless explicitly modeling a backorder/pre-order. Use `inventory_adjustments` write-offs so stock loss is auditable and separated from sales COGS.
- Do not validate refund amounts against order total in isolation — always check `new_amount + SUM(existing refunds for this order) <= order.total_amount`. For edits, exclude the refund being edited (`AND id != current_refund_id`). A single-refund check passes even when the cumulative total already exceeds the cap. See `routes/refunds.py` `create_refund()` and `update_refund()`.
- Do not allow `discount_amount > subtotal` on an order — this produces a negative `total_amount` which corrupts customer debt, revenue reporting, and payment validation. Validate in both `create_order()` and `update_order()` in `routes/orders.py`.
- Do not validate payment amounts in isolation — always check `new_amount + SUM(existing payments for this order) <= order.total_amount` before inserting. Without this guard, overpayment is silently accepted. See `routes/orders.py` `add_payment()`.
