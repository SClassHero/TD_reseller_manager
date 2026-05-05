# Schema Changelog

> **Keep this file up to date.** Every structural database change must be recorded here
> and `SCHEMA_VERSION` in `db.py` must be incremented.
>
> This file is the contract between app versions and the backup/restore system.
> Backups embed the schema version; restoring a backup into a newer app triggers
> `init_db()` which replays all migrations automatically.

---

## Rules for future changes (read before touching the schema)

| Change type | Action required |
|---|---|
| Add a new column | `_migrate_add_column()` in `init_db()` + increment SCHEMA_VERSION |
| Add a new table | `CREATE TABLE IF NOT EXISTS` in `init_db()` + increment SCHEMA_VERSION |
| Remove a column | **Do not** - SQLite doesn't support DROP COLUMN in older versions, and it breaks old backups. Instead, stop using the column and document it as deprecated. |
| Rename a column | **Do not** - add a new column, migrate data in `init_db()`, deprecate the old one. |
| Change a column type or meaning | Add a migration script that transforms existing data + increment SCHEMA_VERSION |
| Add an index | `CREATE INDEX IF NOT EXISTS` - no SCHEMA_VERSION increment needed |
| Change seed data | No increment needed |

**Test before releasing:** Create a backup at the old schema version, upgrade the app, restore the backup, verify data is intact and new columns have correct defaults.

---

## v1 - 2026-04-27 (initial production release)

`SCHEMA_VERSION = 1`

### Tables

| Table | Purpose |
|---|---|
| `settings` | Single-row app config: password, currency, exchange rate, recovery code, backup prefs |
| `categories` | Product categories |
| `products` | Products with sale/cost price as reference defaults only |
| `product_images` | Up to 3 photos per product, stored in `static/uploads/products/{id}/` |
| `customers` | Customers with live-computed outstanding_debt (never trust the cached column) |
| `inventory` | Intake lots with FIFO tracking via remaining_quantity |
| `orders` | Orders with 4 statuses (draft/processing/completed/cancelled) |
| `order_items` | Line items with actual agreed unit_price per order |
| `order_allocations` | FIFO deduction records for active sale orders; source of COGS |
| `payments` | Payments against orders |
| `refunds` | Refunds (full CRUD); linked optionally to a return |
| `returns` | Physical goods returns |
| `return_items` | Items within a return |

### Columns added via migration (safe for pre-existing DBs)

| Table | Column | Default | Notes |
|---|---|---|---|
| `order_allocations` | `cost_price_at_sale` | 0 | Per-lot COGS capture |
| `orders` | `shipping_paid_by` | `'customer'` | Customer vs seller pays shipping |
| `refunds` | `return_id` | NULL | Optional FK to returns |
| `inventory` | `shipping_cost` | 0 | Inbound shipping to warehouse |
| `products` | `product_code_custom` | 0 | Unused, safe to ignore |
| `settings` | `recovery_code_hash` | NULL | In-app password recovery code hash |
| `settings` | `auto_backup_enabled` | 1 | Auto-backup on/off |
| `settings` | `auto_backup_frequency_hours` | 168 | Hours between auto-backups (168=weekly) |
| `settings` | `auto_backup_keep` | 12 | Max auto-backups to retain |
| `settings` | `last_auto_backup_at` | NULL | ISO timestamp of last auto-backup |
| `settings` | `schema_version` | 1 | Tracks schema version in DB |

---

## v2 - 2026-04-28 (limited access account)

`SCHEMA_VERSION = 2`

### Changes from v1

- Added table `users` - login accounts with `username`, `password_hash`, `role`, `is_active`, and `created_at`.
- Added `admin` role - full access, seeded from `settings.password_hash`.
- Added `limited` role - view-only access to Products, Inventory, Customers, and Orders; Dashboard, Reports, Settings, Imports/Exports, Returns, Refunds, edit/new forms, and all POST changes are blocked.

### Migration notes

Existing data: automatic via `init_db()`. The first admin user is created from the existing hashed `settings.password_hash`; the legacy settings password remains for recovery and backup compatibility.

Backup compatibility: v1 backups restore into v2, then `init_db()` creates `users` and seeds the admin account.

---

## v3 - 2026-05-02 (returned-goods restock tracking)

`SCHEMA_VERSION = 3`

### Changes from v2

- Added `return_items.restock_action` (`TEXT DEFAULT 'none'`) - records whether a returned item created a new returned-goods inventory intake lot.
- Added `return_items.restock_unit_cost` (`REAL DEFAULT 0`) - per-unit resale/refurb/restock value assigned to returned goods.
- Added `return_items.restock_shipping_cost` (`REAL DEFAULT 0`) - total seller cost to receive/refurbish the returned quantity.
- Added `return_items.restock_inventory_lot_id` (`INTEGER REFERENCES inventory(id)`) - links a return item to the new returned-goods inventory lot when restocked.

### Migration notes

Existing data: automatic via `init_db()`. Historical return items receive defaults (`restock_action = 'none'`, costs = `0`, no linked lot), so old returns keep their previous records without inventing stock.

Backup compatibility: v1/v2 backups restore into v3, then `init_db()` adds the return restock columns. v3 backups embed schema version 3.

---

## v4 - 2026-05-03 (partial return restock quantity)

`SCHEMA_VERSION = 4`

### Changes from v3

- Added `return_items.restock_quantity` (`INTEGER DEFAULT 0`) - records how many units from a returned line were added back into inventory as a returned-goods lot.

### Migration notes

Existing data: automatic via `init_db()`. Historical return items with a linked `restock_inventory_lot_id` are backfilled to `restock_quantity = quantity`; return items without a linked lot keep `restock_quantity = 0`.

Backup compatibility: v1/v2/v3 backups restore into v4, then `init_db()` adds/backfills the restock quantity column. v4 backups embed schema version 4.

---

## v5 - 2026-05-03 (inventory adjustments and write-offs)

`SCHEMA_VERSION = 5`

### Changes from v4

- Added table `inventory_adjustments` - auditable inventory lot adjustments/write-offs.
- Added prefix `AD` for adjustment codes such as `AD000001`.
- Adjustment rows store `inventory_lot_id`, `product_id`, optional `return_item_id`, `adjustment_type`, signed `quantity_delta`, `unit_cost`, `total_cost`, `reason`, `notes`, and `adjustment_date`.

### Migration notes

Existing data: automatic via `init_db()`. The new table starts empty; no historical write-offs are inferred.

Backup compatibility: v1/v2/v3/v4 backups restore into v5, then `init_db()` creates the adjustment table. v5 backups embed schema version 5.

---

## Template for future versions

```markdown
## v6 - YYYY-MM-DD (brief description)

`SCHEMA_VERSION = 6`

### Changes from previous version

- Added `table.column` (type, default) - reason
- Added table `new_table` - reason

### Migration notes

Existing data: [describe any data transformation needed, or "automatic via init_db()"]
Backup compatibility: Backups from earlier versions restore correctly; new column gets default value X.
```
