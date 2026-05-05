# Changelog

App versions follow semantic versioning where practical:

- `MAJOR`: breaking app or deployment changes
- `MINOR`: new user-facing features
- `PATCH`: bug fixes and small documentation/test updates

Database schema compatibility is tracked separately in `SCHEMA_CHANGELOG.md`.

## [0.7.0] - 2026-05-05

### Added

- **Returns and Refunds export** (CSV + XLSX): Returns export includes return code, date, order, customer, reason, item counts, and linked refund codes. Refunds export includes refund code, date, order, customer, amount, method, reason, notes, and linked return code.
- **Categories export** (CSV + XLSX): All categories with name and description. Re-importable.
- **Categories import**: New `/import/categories` endpoint. Creates new categories; rows whose name already exists are silently skipped (idempotent — safe to re-run).
- **Full XLSX export** now produces 7 sheets (added Categories, Returns, Refunds).
- **Export/Import page overhaul**: Clear limitations box at top; exports split into "Re-importable data" and "Read-only data" sections; pages link to each other.
- **VND currency labels** on every monetary form input: order form (discount, shipping, unit price, line total, running total), product form (sale price, cost price), refund form (amount), return form (refund amount, restock costs), intake form ("in currency below").
- **USD warning banners**: Amber in-context notice on the order form and order detail payment section when viewing in USD display mode, reminding that inputs must be entered in VND.
- **Intake form always defaults to VND**: Currency selector no longer mirrors the display toggle — it always opens on VND to prevent accidentally inflating supplier costs.

### Fixed

- `import_inventory()`: `shipping_cost` and `currency` columns were parsed from CSV but never inserted — INSERT had only 6 columns. Fixed to pass all 8.
- `import_customers()`: CSV-supplied `customer_code` was silently discarded; a new KH###### code was always generated. Fixed: CSV code is used when present.
- `import_products()`: Always called `generate_code('SP')` regardless of category. Fixed to call `generate_product_code(category_id=...)` so the correct 2-letter category prefix is used.
- `exports.py` `_fetch_returns()`: Joined on `r.order_id` — column is `r.original_order_id`. Fixed.
- Export monetary values were written as floats (e.g. `2000000.0`). Added `_cast_int()` post-processor; all VND columns now export as plain integers.
- Duplicate product code import error message now includes the conflicting code for easier diagnosis.

### Current Baseline

- 880 documented tests passing (added `test_images.py`, `test_backup_images.py`, `test_import_export.py`, `test_currency_ui.py`).
- Database schema version: 5 (no schema changes this release).

## [0.6.0] - 2026-05-05

### Added

- English/Vietnamese UI language toggle in the top header.
- Display-only Vietnamese labels for the main navigation, list pages, common tables, order form, return flow, and frequently used modal dialogs.
- Session-based UI language preference; backend status values, schema, routes, accounting rules, and stored data remain unchanged.
- Vietnamese starter docs: `README.vi.md` and `USERGUIDE.vi.md`.
- Language-toggle integration tests covering full pages, filtered refreshes, modal partials, HTMX return item partials, and switch-back behavior.

### Current Baseline

- 702 documented tests passing.
- Database schema version: 5.

## [0.5.0] - 2026-05-04

First formal versioned checkpoint.

### Added

- Formal app version metadata in `version.py`.
- Synology Container Manager deployment files.
- Tailscale-based remote access documentation.
- Windows portable packaging guide and build script.
- Desktop launcher entry point for local packaged builds.
- README for GitHub users.

### Current Baseline

- 673 documented tests passing.
- Database schema version: 5.
