# TD Reseller Manager

A self-hosted inventory and order management app for a small resale/e-commerce business.

The app is built for practical day-to-day use: track product lots, sell from inventory using FIFO costing, record payments, handle returns/refunds, write off damaged stock, and see revenue/profit/debt reports from a phone or desktop browser.

## What It Does

- Products, categories, customers, inventory intakes, orders, payments, returns, refunds, and reports
- FIFO inventory allocation with per-lot COGS
- Processing/completed order accounting
- Customer debt and debt aging
- Partial returns and after-the-fact returned-goods restock
- Inventory write-offs for damaged/lost/unsellable stock
- CSV/XLSX import and export
- Product photos
- VND-first money storage with USD display toggle
- Hashed admin password, recovery code, CSRF protection, limited-access account
- Mobile-friendly list and modal layouts
- SQLite backup/restore with schema-version checks
- Synology NAS deployment through Container Manager, with Tailscale for remote access

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://localhost:5000
```

Default login:

```text
username: admin
password: admin123
```

Change the password immediately in **Settings**, then generate and save a recovery code.

## Current Version

App version: `0.5.0`

Database schema version: `5`

App release versions are tracked in [CHANGELOG.md](CHANGELOG.md). Database structure changes are tracked separately in [SCHEMA_CHANGELOG.md](SCHEMA_CHANGELOG.md).

## Daily Workflow

### 1. Set Up Products

Create categories first, then products.

Product `sale_price` and `cost_price` are defaults only:

- `sale_price` pre-fills order item price.
- `cost_price` pre-fills inventory intake cost.
- Actual revenue comes from order item unit prices.
- Actual COGS comes from inventory lots consumed by FIFO.

### 2. Buy Stock

Go to **Inventory -> New Intake**.

Each intake creates a lot with:

- quantity
- remaining quantity
- unit cost
- inbound shipping cost
- intake date

The intake date matters because FIFO sells the oldest available lot first.

### 3. Sell In-Stock Items

Recommended flow:

```text
Orders -> New Order -> Mark as Processing or Completed -> Record Payment
```

When an order becomes **Processing** or **Completed**, it becomes an active sale:

- stock is reserved/deducted by FIFO
- revenue counts
- COGS counts
- customer debt counts

Draft orders do not affect stock or accounting and can be edited/deleted.

### 4. Sell Before Stock Arrives

Use **Processing** for a confirmed order that you still need to source.

If there is not enough stock, the app allows the order and allocates what it can. When stock arrives, add an intake and complete the order; the app allocates remaining COGS from the new stock.

### 5. Correct an Active Order

Do not edit a Processing or Completed order directly.

Use:

```text
Order Detail -> Reopen to Draft -> Edit -> Complete/Processing again
```

This restores the exact FIFO lots first, then reallocates from the corrected order.

### 6. Record Payments

Payments live on the order detail page.

The app blocks overpayment: total payments for an order cannot exceed that order total.

Payment status updates automatically:

- Not Paid
- Partially Paid
- Fully Paid

### 7. Returns vs Refunds

Returns and refunds are separate on purpose.

Use **Returns** when goods physically come back.
Use **Refunds** when money goes back to the customer.

They can be linked, but they do not have to be.

### 8. Returned Goods

When a customer returns an item, choose how many returned units are sellable and should go back into stock.

Sellable returned items become a new returned-goods inventory lot. They are not restored into the original FIFO lot. This keeps the failed sale's original COGS history intact and prevents resale accounting from double-counting the old purchase cost.

If you forgot to restock during return creation, use the return detail page to add returned goods after the fact.

### 9. Damaged, Lost, or Unsellable Stock

Use **Write Off** from Inventory or Return Detail.

Write-offs:

- reduce stock from one exact lot
- create an `AD######` inventory adjustment
- do not change revenue, refunds, customer debt, or order COGS
- appear separately as inventory adjustment cost

Use write-offs for damaged returns, missing stock, opened items, samples/giveaways, or stock count corrections.

## Accounting Rules

Revenue:

```text
order total = line subtotals - order discount + customer-paid shipping
```

Net revenue:

```text
processing/completed order totals - refunds
```

Gross profit:

```text
net revenue - FIFO COGS - seller-paid shipping
```

Adjusted profit:

```text
gross profit - inventory write-offs
```

Important guardrails:

- Draft orders have no stock/accounting effect.
- Processing and Completed orders count as active sales.
- Cancelled orders are excluded from sales/debt/accounting.
- Cumulative refunds for an order cannot exceed the order total.
- Order-level discount cannot exceed subtotal.
- Customer debt is always computed live from active order totals minus payments.

## Reports

The dashboard and reports include:

- gross revenue
- refunds
- net revenue
- FIFO COGS
- seller-paid shipping
- gross profit
- inventory adjustments/write-offs
- adjusted profit
- outstanding collections
- low stock and negative stock
- top products/customers
- customer debt aging

## Import and Export

Import supports CSV for:

- products
- customers
- inventory intakes

Export supports CSV and XLSX for:

- products
- customers
- orders
- inventory
- export all as one workbook

XLSX export requires `openpyxl`, included in `requirements.txt`.

## Deployment

### Local

```bash
python app.py
```

For non-technical Windows users, build a portable local app folder with:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1
```

The user can then double-click `Start TD Reseller Manager.bat` from the generated `dist\TDResellerManager` folder. See [PACKAGING_WINDOWS.md](PACKAGING_WINDOWS.md).

### Synology NAS

Recommended deployment is Synology Container Manager/Docker.

The included compose file publishes:

```text
NAS port 5080 -> container port 5000
```

Runtime data is stored outside the image:

```text
data/
|-- inventory.db
|-- .secret_key
|-- backups/
`-- uploads/products/
```

For outside-home access, the current working route is Tailscale:

```text
http://NAS-Tailscale-IP:5080
```

See [DEPLOY_SYNOLOGY.md](DEPLOY_SYNOLOGY.md) for the NAS setup guide.

## Backup and Restore

Backups are ZIP files containing:

- SQLite database
- product photos
- backup metadata including schema version

Backups are available in **Settings**. Auto-backup is enabled by default and retention is configurable.

Restore creates a safety backup first, then restores the selected archive. Older backups are migrated forward automatically through `init_db()`.

## Tests

```bash
python test_app.py
python test_backup.py
python test_real_world_scenarios.py
python test_edge_cases.py
python test_edge_cases_2.py
```

Current documented baseline: 673 passing tests.

## Project Docs

- [USERGUIDE.md](USERGUIDE.md): detailed user manual and workflows
- [DEPLOY_SYNOLOGY.md](DEPLOY_SYNOLOGY.md): Synology deployment guide
- [PACKAGING_WINDOWS.md](PACKAGING_WINDOWS.md): portable Windows build guide
- [CHANGELOG.md](CHANGELOG.md): app release history
- [SCHEMA_CHANGELOG.md](SCHEMA_CHANGELOG.md): database schema history
- [CLAUDE.md](CLAUDE.md): technical reference for Claude Code sessions
- [AGENTS.md](AGENTS.md): technical reference for Codex sessions

## Security Notes

- Passwords are stored as Werkzeug hashes, not plaintext.
- The app is intended for trusted self-hosted use.
- Do not commit `inventory.db`, `.secret_key`, backups, or uploaded product photos.
- Use Tailscale or another private network path for remote access unless you deliberately harden and expose the app.
