# Inventory Manager — User Guide

> For a micro-business that sources and resells goods via Facebook Marketplace,
> personal pages, and direct customer orders.

---

## Table of Contents

1. [How the App Thinks](#1-how-the-app-thinks)
2. [First-Time Setup](#2-first-time-setup)
3. [Your Two Business Models](#3-your-two-business-models)
4. [Recording Stock (Inventory Intake)](#4-recording-stock-inventory-intake)
5. [Managing Orders](#5-managing-orders)
6. [Collecting Payment](#6-collecting-payment)
7. [Returns and Refunds](#7-returns-and-refunds)
8. [Tracking Who Owes Money](#8-tracking-who-owes-money)
9. [Dashboard and Financial Reports](#9-dashboard-and-financial-reports)
10. [Exporting Data](#10-exporting-data)
11. [Bulk Import via CSV](#11-bulk-import-via-csv)
12. [Common Scenarios (Step-by-Step)](#12-common-scenarios-step-by-step)
13. [Glossary](#13-glossary)
14. [Password Management and Access Control](#14-password-management-and-access-control)
15. [Local Windows Use Without Command Line](#15-local-windows-use-without-command-line)
16. [Running on a Synology NAS (Home + Remote Access)](#16-running-on-a-synology-nas-home--remote-access)

---

## 1. How the App Thinks

Understanding three core concepts will make everything else click.

### Inventory Lots

Every time you buy stock, the app records a **lot** — a batch with its own
purchase cost and date. When you sell, the app deducts from the **oldest lot
first** (FIFO — First In, First Out). This gives you an accurate cost of goods
sold even if you bought the same product at different prices over time.

```
Example:
  Lot A (Jan): 5 units at 100,000₫ each  ← sold first
  Lot B (Mar): 5 units at 120,000₫ each  ← sold after Lot A is gone
```

### Order Status Flow

Orders move through four states. **Draft** is only a no-impact working copy.
Inventory, revenue, COGS, and customer debt begin when the order becomes
**Processing** or **Completed**.

```
Draft → Processing → Completed → (done)
                  ↘ Cancelled
```

| Status | What it means | Inventory |
|---|---|---|
| **Draft** | Order noted, not yet committed | Not deducted; can be edited/deleted |
| **Processing** | Accepted, being prepared/sourced | Deducted/reserved by FIFO where stock exists |
| **Completed** | Shipped / handed over | Active sale; allocates any remaining backorder stock |
| **Cancelled** | Voided | Restored if it was Processing/Completed |

### The COGS Number Comes From Your Purchase Price

Your **profit** is calculated using the actual price you paid for each lot,
not a fixed "cost price" field on the product. The product's cost price field
is only a default suggestion when creating a new intake — the real number is
what you enter per lot.

---

## 2. First-Time Setup

Do this once before recording any orders.

### 2.1 Settings (`/settings/`)

| Field | What to set |
|---|---|
| **App Name** | Whatever you like — appears in the page title |
| **Default Currency** | VND (the app stores everything in VND internally) |
| **VND/USD Rate** | Current exchange rate — only affects USD display toggle |
| **Password** | Change from `admin123` immediately |

### 2.2 Categories (`/categories/`)

Group your products so you can filter and report by type.

Examples: `Áo (Clothing)`, `Túi (Bags)`, `Đồ Gia Dụng (Home)`, `Mỹ Phẩm (Cosmetics)`

The category name's first two letters become the prefix for auto-generated
product codes (e.g. category "Áo" → product codes `ÁO0001`, `ÁO0002`).

### 2.3 Products (`/products/`)

Create a product card for each distinct item you sell. Fields:

| Field | Purpose |
|---|---|
| **Product Code** | Auto-generated or enter your own (e.g. SKU from supplier) |
| **Name** | The item name as you'd describe it to a customer |
| **Category** | For filtering and reporting |
| **Sale Price** | Your typical selling price — **pre-fills orders** but can be overridden per order |
| **Cost Price** | Typical purchase price — **pre-fills intake forms** but can be overridden per lot |
| **Min Stock Level** | Alert threshold for low-stock warning on dashboard |

> **Important:** Sale price and cost price on the product card are just
> convenient defaults. They do not affect your financial reports. What actually
> drives revenue and profit are the prices you enter when creating an order and
> an intake lot.

### 2.4 Browsing Long Lists

Products, Categories, Customers, Inventory, Orders, Returns, and Refunds all
have search/sort controls and a **Rows** selector. Use fewer rows on a phone so
each page stays easy to scan with your finger; use more rows on desktop when
you want to compare many records at once. Pagination shows how many records are
visible and preserves your current search, filter, sort, and row-count choices.

---

## 3. Your Two Business Models

### Model A — Buy First, Sell Later

You spot items on sale (Facebook groups, markets, wholesale suppliers) and buy
them speculatively. You then list them and sell when a buyer appears.

```
[Source deal] → Record Intake → [Customer inquires / buys]
                                → Create Order → Complete → Collect Payment
```

### Model B — Take Order First, Source After

A customer wants something specific. You accept their order, go find the item,
then deliver it.

```
[Customer orders] → Create Order (Processing) → [Source item]
                                               → Record Intake (goods arrived)
                                               → Complete Order (allocates remaining stock) → Deliver
                                               → Collect Payment
```

Both models are fully supported. The key difference is whether you record the
intake **before or after** creating the order. Either way, FIFO deduction and
COGS calculation work correctly.

---

## 4. Recording Stock (Inventory Intake)

Go to **Inventory → New Intake** (or the `+` button on the Inventory page).

The Inventory page is organized by product first. Use search to find a product
by name, code, or barcode; use the status filter for In Stock, Low Stock,
Backorders, or Out of Stock. Open a product's active-lot section only when you
need the lot-level date, cost, note, or Write Off action.

### Required Fields

| Field | Notes |
|---|---|
| **Product** | Which item you're stocking |
| **Quantity** | Number of units received |
| **Cost Price (per unit)** | What you paid per unit to your supplier |
| **Intake Date** | When goods arrived — affects FIFO order. A warning appears if the date is more than 30 days in the past (see below). |

### Optional Fields

| Field | Notes |
|---|---|
| **Shipping / Freight Cost** | What it cost to get the goods to *you* (not to your customer). This is spread evenly across all units in the lot and folded into your COGS automatically. |
| **Currency** | If you paid in USD, select USD — the app records the VND equivalent |
| **Notes** | Supplier name, order reference, deal source, etc. |

### How Intake Shipping Cost Affects COGS

If you pay 100,000₫ freight to receive a batch of 10 items, each unit's COGS
is increased by 10,000₫. This is correct — the freight is a real cost of
acquiring the goods.

```
Example:
  Cost price per unit: 200,000₫
  Freight for lot: 100,000₫ ÷ 10 units = 10,000₫ per unit
  COGS per unit recorded: 210,000₫
```

### FIFO Date Warning

If you enter an **Intake Date more than 30 days in the past**, the form shows
an amber warning:

> *"Intake date is more than 30 days ago. This lot will be placed near the
> front of the FIFO queue, ahead of lots recorded after that date."*

This matters if you forgot to record a lot and are adding it late — the old
date means it will be consumed first (FIFO), even if newer lots have been
sitting in stock. Check that this is what you intend before saving.

### Negative Quantity — Backorder Marker (Optional)

If you have committed to sourcing an item but haven't received it yet, you can
create an intake with a **negative quantity** (e.g. `-3`). This shows up in
the Inventory list highlighted in amber, signalling "we owe 3 units." It does
not affect FIFO (FIFO only uses lots with quantity > 0). When the goods arrive,
create a normal positive intake on the same product.

### Stock Write-offs / Inventory Adjustments

Use **Write Off** when stock is damaged, unsellable, lost, used as a sample, or
you find a stock-count mistake. You can do this from the **Inventory** page for
any lot that still has available quantity.

What happens:

- Stock is reduced from that exact inventory lot.
- The app records an `AD######` adjustment with reason, notes, quantity, and cost.
- Sales revenue, refunds, customer debt, and order COGS are not changed.
- Dashboard and Profit Report show write-offs separately as **Inventory Write-offs**
  and subtract them in **Profit After Adjustments**.

Guardrail: you can only write off a positive quantity up to the lot's current
remaining quantity. Already-sold units cannot be written off.

---

## 5. Managing Orders

Go to **Orders → New Order**.

### 5.1 Creating an Order

| Field | Notes |
|---|---|
| **Customer** | Select existing or create one first |
| **Order Date** | When the order was placed (defaults to today) |
| **Items** | Add one row per product; unit price overrides the product default. Each product in the dropdown shows its current stock level (e.g. `Áo Thun Nam (Stock: 8)`). Zero-stock items are highlighted red; low-stock items are highlighted amber. |
| **Discount per Item** | A percentage discount on that line (e.g. 10% off for a sale) |
| **Order Discount** | A flat amount off the whole order |
| **Shipping Fee** | What the customer pays for delivery |
| **Shipping Paid By** | See below |
| **Notes** | For internal reference (not shown to customer) |

### 5.2 Shipping: Customer Pays vs Seller Pays

This controls whether shipping is revenue or just a cost.

| Setting | Customer owes | Effect on reports |
|---|---|---|
| **Customer pays** | Subtotal + shipping | Shipping is part of your gross revenue |
| **Seller pays** | Subtotal only | Shipping is your cost — reduces gross profit |

Use "Seller pays" when you offer free shipping as part of your deal.

### 5.3 Order Total Formula

```
Subtotal      = SUM(qty × unit_price × (1 − discount_pct/100))
Order Total   = Subtotal − order_discount + shipping_fee (if customer pays)
```

The customer owes the **Order Total**.

### 5.4 Order Status — What Each Step Does

**Draft → Processing**
- Use "Mark as Processing" when you've confirmed the order with the customer
  and are preparing / sourcing the item.
- Inventory is reserved by FIFO where stock exists. Revenue, COGS, and customer debt begin here.
- If stock is not available yet, the order remains active and any missing COGS is allocated when you complete it after receiving stock.

**Processing → Completed**
- Use "Mark as Completed" when the item has been handed to the customer or
  shipped out.
- If the order was still Draft, inventory is deducted here (FIFO).
- If the order was already Processing, the app only allocates any still-unreserved backorder quantity.

**Processing/Completed → Reopen to Draft**
- Use this if you need to correct an active order.
- Inventory deductions are **fully reversed** (restored to the exact lots
  they came from).
- Mark Processing/Completed again after editing to re-apply FIFO deduction.

**Cancel Order**
- Permanently voids the order.
- If it was Processing/Completed, inventory and accounting are restored/reversed.
- Cannot be uncancelled — create a new order if needed.

### 5.5 Editing an Active Order

You cannot edit a Processing or Completed order directly. The correct flow is:

1. Open the order → **Reopen to Draft**
2. Edit the order (items, prices, discount, shipping)
3. **Mark as Completed** again

This ensures inventory is deducted from the correct lots at the corrected quantities.

---

## 6. Collecting Payment

Payment is recorded **separately from the order** — because in practice customers
often pay in installments, pay late, or pay via different methods.

### 6.1 Adding a Payment

On any order detail page (in non-cancelled status), scroll to the Payments
section and fill in:

| Field | Notes |
|---|---|
| **Amount** | How much was received this time |
| **Method** | Cash, bank transfer, MoMo, ZaloPay, etc. |
| **Note** | Optional — transaction reference, date received, etc. |

Click **Add Payment**. The payment status updates automatically:

| Situation | Status |
|---|---|
| Total paid = 0 | `Not Paid` |
| 0 < Total paid < Order total | `Partially Paid` |
| Total paid = Order total | `Fully Paid` |

### 6.2 Editing or Deleting a Payment

Each payment row has **Edit** and **Delete** buttons. Editing updates the amount
and method in-place. Deleting recalculates the payment status automatically.

### 6.3 Typical Payment Patterns

**Payment on delivery:**
Add the full payment when completing the order.

**Deposit + final payment (installment):**
1. Add the deposit amount when order is placed (or when completed)
2. Add the remaining balance when received later

**Overpayment:**
The app blocks payments that would make total payments exceed the order total.
If a customer sends too much, record only the order balance here and handle the
extra amount outside the order, or refund/return the extra amount before closing
your books.

---

## 7. Returns and Refunds

Returns and refunds are separate operations in the app — a **return** is about
physical goods coming back; a **refund** is about money going back. They can
be linked together or handled independently.

### 7.1 Return (Physical Goods Come Back)

Use when the customer actually sends the item back and you want to record the
physical return.

Go to **Returns → New Return**:

1. Select the **original order**
2. Select which items (and how many) are being returned
3. For each selected item, leave **Add to Stock** checked if the item can be resold
   - It is checked by default and restocks the full return quantity
   - Lower **Restock Qty** if only part of the returned quantity is sellable
   - Leave **Optional resale cost** at 0 for normal returns
   - Enter **Returned Item Value / Unit** only if you want the new returned-goods lot to carry its own inventory value
   - Enter **Extra Restock Cost** only for repair, cleaning, repackaging, or return shipping you paid to make the item sellable
   - If unchecked or Restock Qty is 0: no inventory change for that item

For most returns, the cost fields are **not needed**. The original sale keeps
its real COGS, and the returned-goods lot starts at 0 cost so the original
purchase cost is not counted twice if the item is resold later.

> **Important:** You can only create a return against a **Completed** order.

If you forgot to restock a sellable return item, open the Return Detail page and use
**Restock Selected Items**. This creates the same returned-goods intake lot after
the fact and does not change revenue, refunds, COGS, or profit. It uses the same
optional resale cost fields: leave them at 0 for normal returns, and only enter
extra value/cost when you intentionally want the returned-goods lot to carry it.

If you later inspect a returned item and decide it is damaged or unsellable,
open the Return Detail page and use **Write Off** on the returned-goods lot. This
reduces only the available returned stock and records an adjustment cost; it does
not rewrite the original sale, refund, or COGS history.

### 7.2 Refund (Money Goes Back)

Use when you owe the customer money — regardless of whether goods came back.

Go to **Refunds → New Refund**:

| Field | Notes |
|---|---|
| **Order** | Which Processing or Completed order this refund relates to |
| **Linked Return** | Optional — link to a return if one was created |
| **Amount** | How much to refund. Total refunds for the order cannot exceed the order total. |
| **Method** | How you're sending the money back |
| **Reason** | Defective, customer request, price adjustment, etc. |

### 7.3 Partial Refund (Shipping Non-Refundable)

This is the standard "customer changed their mind" case:
- Customer paid 500,000₫ items + 50,000₫ shipping = 550,000₫ total
- They return the item but you keep the shipping fee
- **Refund = 500,000₫** (item price only)

Enter the item price. The app checks the cumulative refund total for the order,
so several partial refunds are fine as long as their combined amount does not
exceed what the customer originally owed.

### 7.4 Refund Without Return (Goodwill / Price Adjustment)

Sometimes you refund without taking goods back — e.g. an item arrived with a
small defect and you give a 10% discount after the fact.

Create a **Refund** only (no Return). Leave "Linked Return" blank.

### 7.5 How Refunds Affect Reports

Refunds reduce your **Net Revenue** on the dashboard:

```
Net Revenue = Gross Revenue (processing/completed orders) − Total Refunds
Gross Profit = Net Revenue − COGS − Seller Shipping
```

A refund does **not** reverse the original sale COGS. If the goods came back and
can be resold, the app creates a new returned-goods intake lot for the per-item
Restock Qty. The optional returned item value and extra restock cost both default
to 0. That keeps the failed sale's real cost visible and prevents the original
purchase cost from being counted twice when the item is resold later.

If returned stock later becomes unsellable, use **Write Off** instead of editing
the return. The original failed sale keeps its true COGS, and the later stock
loss is reported separately as an inventory adjustment.

---

## 8. Tracking Who Owes Money

### 8.1 Customer List

The **Customers** page shows an **Outstanding Debt** column for every active
customer. This is always computed live from Processing/Completed orders minus payments —
it is never stale.

- **Outstanding = 0**: Fully paid up
- **Outstanding > 0**: Has an unpaid balance (partially paid or no payment at all)

### 8.2 Customer Detail Page

Click any customer name to see:
- **Total Spent** — cumulative value of all Processing/Completed orders (lifetime)
- **Outstanding Debt** — what they currently owe (live calculation)
- **Order History** — every order with its status, amount, and link to the order

### 8.3 How Outstanding Debt is Calculated

```
Outstanding Debt = SUM(processing/completed order totals) − SUM(all payments on those orders)
```

Draft orders are not counted. Processing and Completed orders create debt.
Cancelled orders are excluded.

### 8.4 Clearing Debt

Record a payment against the relevant order(s). The outstanding balance on the
customer page will update immediately.

### 8.5 Customer Debt Aging Report (`/reports/debt-aging`)

When you need to see not just *who* owes money but *how long* they've owed it,
use the **Customer Debt Aging** report under **Reports**.

The report groups unpaid balances into four time buckets based on the order
date:

| Bucket | Meaning |
|---|---|
| **0–7 days** (green) | Recent — probably still expecting payment |
| **8–30 days** (yellow) | Short overdue — worth a gentle reminder |
| **31–90 days** (orange) | Significantly overdue — follow up now |
| **90+ days** (red) | Long overdue — consider escalating |

At the top you'll see the total outstanding amount split by bucket. Below
that, each customer is listed with a breakdown of the specific unpaid orders
— amount owed, date, and age in days.

> Only **Processing/Completed** orders with an unpaid balance appear here.
> Draft and Cancelled orders are excluded.

---

## 9. Dashboard and Financial Reports

### 9.1 Dashboard (`/dashboard/`)

The dashboard shows financial metrics for a selected time period. Use the
period selector (Today / This Week / This Month / This Year / All Time / Custom)
at the top.

#### Key Metrics

| Metric | What it means |
|---|---|
| **Gross Revenue** | Sum of all *Processing/Completed* order totals in the period |
| **Total Refunds** | Sum of all refunds linked to Processing/Completed orders |
| **Net Revenue** | Gross Revenue − Total Refunds |
| **COGS** | Cost of Goods Sold — the actual purchase price of items sold (FIFO per lot, including intake shipping amortisation) |
| **Seller Shipping** | Shipping fees you paid on behalf of customers (a cost, not revenue) |
| **Gross Profit** | Net Revenue − COGS − Seller Shipping |
| **Inventory Write-offs** | Damaged, lost, unsellable, sample, or count-correction stock removed from inventory |
| **Profit After Adjustments** | Gross Profit minus Inventory Write-offs |
| **Profit Margin** | Gross Profit ÷ Net Revenue × 100 |
| **Outstanding Collections** | Total unpaid balance on all Processing/Completed orders (across all time) — how much money customers still owe you |
| **Low Stock** | Products with stock between 0 and their min stock level — items running low |
| **Negative Stock** | Products with stock below 0 — backorder/pre-order markers (shown separately from low stock) |

#### Top Products and Top Customers

Below the main metrics, the dashboard shows the **top 5 products** and **top 5
customers** by revenue for the selected period. This helps you quickly see what
is selling and who your most valuable customers are.

#### Recent Orders

The Recent Orders table now includes a **Payment Status** column so you can see
at a glance which active orders are unpaid or only partially paid without
navigating to each order individually.

#### Understanding COGS

COGS is calculated from your actual intake costs via FIFO:

```
Example:
  Lot A (Jan): 5 units bought at 100,000₫  ← FIFO uses this first
  Lot B (Mar): 5 units bought at 120,000₫

  If you sell 7 units:
  COGS = (5 × 100,000) + (2 × 120,000) = 740,000₫
```

If you added an intake shipping cost, it's folded in per unit automatically.

#### Period Filter Tip

The period filter works by **order date**. An active order dated March appears
in March's numbers even if you changed its status later.

Refunds are filtered by **refund date**, not order date.

### 9.2 Reports (`/reports/`)

| Report | What it shows |
|---|---|
| **Sales Report** | Revenue and order counts by period, filterable by customer and product |
| **Inventory Report** | Current stock levels, remaining quantity per lot, and total stock value per lot (using actual intake cost, not product default) |
| **Profit Report** | Revenue vs COGS vs profit — overall, by month, and by product — using FIFO costs throughout |
| **Customer Debt Aging** | Outstanding balances bucketed by age: 0–7, 8–30, 31–90, and 90+ days (see Section 8.5 for details) |

Inventory write-offs also appear in the Inventory Report's recent adjustment
history and in the Profit Report as a separate line from sales COGS.

### 9.3 Currency Toggle

The **VND ↔ USD** button (top-right of any page) converts the display using
the exchange rate from Settings. All data is stored in VND — USD display is
for reference only.

---

## 10. Exporting Data

Go to **Export** in the sidebar (or `/exports/`).

You can download your data in two formats: **CSV** (plain text, opens in any
spreadsheet) and **XLSX** (Excel format, requires openpyxl on the server).

### 10.1 Individual Exports

| Export | What it contains |
|---|---|
| **Products** | All products with code, name, category, prices, stock level |
| **Customers** | All customers with contact info and outstanding debt |
| **Orders** | All orders with status, totals, payment status, and customer name |
| **Inventory** | All intake lots with remaining quantity, cost, and intake date |

Each export is available as `.csv` or `.xlsx`. Click the button for whichever
format you need.

### 10.2 Export All (Single XLSX file)

The **Download All as XLSX** button creates one Excel file with four sheets —
Products, Customers, Orders, Inventory — all in a single download. Useful for
a complete data backup or handoff to an accountant.

### 10.3 Use Cases

| Situation | What to export |
|---|---|
| Send product catalogue to a supplier | Products → CSV |
| Share customer list with a partner | Customers → CSV |
| Monthly financial review in Excel | Orders → XLSX or Export All |
| Stock count check | Inventory → CSV |
| Full data backup (not the DB file) | Export All → XLSX |

> **Note:** Export downloads a snapshot of the current data. It is not a
> database backup — use **Settings → Download Backup** for that.

---

## 11. Bulk Import via CSV

Go to **Import** in the sidebar (or `/import/`).

### 11.1 Import Products

```csv
name,category,sale_price,cost_price,barcode,min_stock_level
Áo Thun Nam,Áo,150000,80000,SKU001,5
Quần Jean,Quần,300000,160000,SKU002,3
```

- `category` must match an existing category name exactly (case-sensitive)
- `sale_price` and `cost_price` are in VND
- `barcode` and `min_stock_level` are optional

### 11.2 Import Customers

```csv
name,email,phone,address,region
Nguyen Van A,a@example.com,0901234567,123 Nguyen Hue,HCM
Tran Thi B,,0909090909,,Hanoi
```

- `email` is validated — invalid email format will be skipped with an error
- All fields except `name` are optional

### 11.3 Import Inventory

Use either `product_code` or `product_name` to identify the product:

```csv
product_code,quantity,cost_price,intake_date,notes
EL0001,10,8000000,2026-04-01,April restock
EL0002,5,150000,2026-04-01,
```

- `intake_date` format: `YYYY-MM-DD`
- Product must already exist (import creates lots, not products)
- Import results show success count and any row-level errors

---

## 12. Common Scenarios (Step-by-Step)

### Scenario 1 — You buy items speculatively and sell later

> *"I found 10 pairs of sneakers at a market for 200,000₫ each. I'll sell them
> on Facebook Marketplace for 350,000₫."*

1. **Create product**: Name="Nike Air (Fake)", Category="Giày", Sale=350,000₫, Cost=200,000₫
2. **Record intake**: Product=above, Qty=10, Cost=200,000₫/unit, Intake date=today
3. When a buyer appears: **Create order** → add item at 350,000₫ → **Complete**
4. **Record payment** when cash received

---

### Scenario 2 — Customer orders, you source and deliver

> *"A customer on Zalo wants a specific handbag. I don't have it but I'll find
> one for them."*

1. **Create the customer** (if not already in the system)
2. **Create order** for the customer → set status to **Processing**
3. Go find the handbag
4. **Record intake** when you have it (actual cost you paid)
5. **Complete the order** — FIFO deducts from the newly received lot
6. **Record payment** from the customer

> Your COGS is your actual purchase price, even if it differs from the
> product's default cost price.

---

### Scenario 3 — Bundle deal: you buy 20 items, sell over 2 months

> *"I bought 20 phone cases for 50,000₫ each (plus 100,000₫ shipping).
> Selling for 120,000₫ each."*

1. **Record intake**: Qty=20, Cost=50,000₫, Shipping cost=100,000₫
   - App records effective COGS = 50,000 + 100,000/20 = **55,000₫ per unit**
2. Each sale: **Create order**, **Complete**, **Record payment**
3. Dashboard COGS will use 55,000₫ per unit automatically

---

### Scenario 4 — Customer returns an item, you give partial refund

> *"Customer paid 420,000₫ (item 370,000₫ + 50,000₫ shipping). Item is
> defective. They return it; I refund the item cost but not the shipping."*

1. **Create Return**: select the order, select the item, qty=1, leave Add to Stock checked if sellable
2. **Create Refund**: select the order, link to the return just created,
   amount=370,000₫ (not 420,000₫), method=bank transfer
3. Net Revenue on dashboard decreases by 370,000₫

---

### Scenario 5 — Correcting an active order

> *"I entered the wrong price when completing an order."*

1. Open the order → **Reopen to Draft**
   - Inventory allocations are automatically restored to their original lots
2. **Edit Order**: fix the price
3. **Mark as Processing/Completed** again
   - FIFO re-deducts inventory at the corrected amounts

---

### Scenario 6 — Customer wants to pay in installments

> *"Customer paid a 500,000₫ deposit. Remaining 1,500,000₫ to be paid on delivery."*

1. Complete the order normally when you hand it over
2. **Add first payment**: 500,000₫, method=cash, note="deposit"
   - Payment status → `Partially Paid`
3. When balance is received: **Add second payment**: 1,500,000₫
   - Payment status → `Fully Paid`

---

### Scenario 7 — Tracking who still owes money

1. Go to **Customers**
2. Look at the **Outstanding Debt** column
3. Any customer showing a debt > 0 has unpaid Processing/Completed orders
4. Click the customer → see which specific orders are unpaid and the amounts

---

### Scenario 8 — Free shipping promotion ("seller absorbs shipping")

> *"I'm running a promotion: free shipping for orders over 500,000₫."*

On the order form:
- Enter the actual shipping cost in the **Shipping Fee** field
- Set **Shipping Paid By** = **Seller**
- The customer's order total will **not include** the shipping fee
- The shipping cost is recorded as your expense and reduces gross profit on the dashboard

---

### Scenario 9 — Pre-order with negative inventory marker

> *"5 customers have pre-ordered a product I haven't sourced yet."*

1. **Record intake**: Product=item, Qty=**-5**, Cost=expected purchase price
   - This appears in inventory as `-5` highlighted amber (a reminder/marker)
2. Create and accept each customer's order (set to Processing)
3. When you receive the goods: **Record intake** again with Qty=+5 (or however many you received), actual cost paid
4. **Complete** each customer's order → FIFO uses the positive lot (skips the negative marker)

---

### Scenario 10 — End-of-month check

1. **Dashboard** → set period to **This Month**
2. Note: Gross Revenue, Net Revenue, COGS, Gross Profit, Margin
3. **Reports → Inventory** → check current stock vs min levels
4. **Customers** → sort/scan for anyone with Outstanding Debt
5. **Orders** → filter by `Processing` to see what is still pending shipment

---

## 13. Glossary

| Term | Definition |
|---|---|
| **COGS** | Cost of Goods Sold — the purchase price of items sold, calculated via FIFO from intake lots |
| **FIFO** | First In, First Out — oldest inventory lot is sold first |
| **Intake** | A batch of goods received into your warehouse/stock, with a date and purchase cost |
| **Lot** | One intake record — a batch of units purchased at a specific cost on a specific date |
| **Gross Revenue** | Total of all Processing/Completed order values |
| **Net Revenue** | Gross Revenue minus refunds issued |
| **Gross Profit** | Net Revenue minus COGS minus seller-paid shipping |
| **Inventory Adjustment / Write-off** | An auditable stock reduction for damaged, lost, unsellable, sample, or count-correction stock; separate from sales COGS |
| **Profit After Adjustments** | Gross Profit minus Inventory Write-offs |
| **Profit Margin** | Gross Profit ÷ Net Revenue, as a percentage |
| **Outstanding Debt** | What a customer still owes: Processing/Completed order totals minus total payments received |
| **Seller Shipping** | Shipping cost you pay on behalf of the customer (a cost, excluded from order total) |
| **Customer Shipping** | Shipping fee added to the customer's invoice (included in order total) |
| **Backorder** | An order accepted before the goods are in stock |
| **Draft** | An editable/deletable order that has no stock or accounting effect |
| **Processing** | An active order that reserves stock and counts in revenue/debt/COGS where allocated |
| **Completed** | An active order that has been fulfilled and shipped |
| **Return** | Physical goods coming back from a customer — can create returned-goods intake |
| **Refund** | Money returned to a customer — reduces Net Revenue on reports |
| **Payment Status** | Auto-calculated: Not Paid / Partially Paid / Fully Paid |

---

## 14. Password Management and Access Control

### 14.1 Changing Your Password (While Logged In)

Go to **Settings → Change Password**. You need to know your current password to
do this.

### 14.2 Forgot Your Password — Recovery Code

The app has a built-in **password recovery code** system. Set it up once so
you're never locked out.

**One-time setup (do this now):**

1. Log in → go to **Settings → Password Recovery Code**
2. Click **Generate Recovery Code**
3. Write down the code shown (format: `XXXX-XXXX-XXXX-XXXX-XXXX`) and store it
   somewhere safe (printed note, password manager, etc.)

**When locked out:**

1. Go to the login page → click **Forgot password?**
2. Enter your recovery code and choose a new password
3. A new recovery code is generated automatically — save it again

> **Important:** The recovery code is shown only once at generation time.
> After you use it to reset your password, a new code is generated. Always
> save the new code shown after a successful recovery.

### 14.3 User Accounts and Limited Access

The app has an admin account and one optional limited-access account.

| Account type | What it can do |
|---|---|
| **Admin** | Full access to all screens, settings, exports, reports, revenue/profit, and data changes |
| **Limited** | View Products, Inventory, Customers, Orders, customer details, and order details |

The limited account cannot see Dashboard, Reports, Settings, Imports/Exports,
Returns, Refunds, revenue, profit, or cost-sensitive admin pages. It also cannot
submit POST actions, so it cannot create, edit, delete, reset, restore, or write
off data.

#### Creating or updating the limited account

Go to **Settings → Limited Access Account**. Set a username, password, and active
status. Leaving the password blank while editing keeps the existing limited
password.

#### If you need to revoke access

- For admin access: change the admin password via **Settings → Change Password**
  or use the recovery code if locked out.
- For limited access: go to **Settings → Limited Access Account** and deactivate
  the account or change its password.

Anyone previously logged in will be kicked out the next time their session
expires.

#### What this means in practice

| Situation | What to do |
|---|---|
| Just you using it | Use the admin account |
| Partner / spouse helps with everything | Give them admin access only if you trust them with financials and settings |
| Helper only needs lookup access | Use the limited account |
| Someone needs to create/edit orders but not see profit | Not supported yet; that would need a new role |

### 14.4 Security Notes

- The password is stored as a secure hash (bcrypt/scrypt) — not readable even
  if someone copies the database file
- Sessions expire when the browser is closed (no persistent "remember me")
- The app is designed for **trusted network use** — see Section 15 for the
  current Tailscale-based remote access setup
- Take regular backups: **Settings → Download Backup** saves a copy of
  `inventory.db`

---

## Quick Reference Card

```
BUY STOCK:          Inventory → New Intake → fill cost + qty + date
WRITE OFF STOCK:    Inventory or Return Detail → Write Off damaged/lost/unsellable qty
SELL (in stock):    Orders → New → Complete → record Payment
SELL (no stock):    Orders → New → Processing → get stock → Intake → Complete → Payment
CORRECT ORDER:      Order → Reopen to Draft → Edit → Complete again
CUSTOMER RETURNS:   Returns → New → select items → Add to Stock per item if sellable
GIVE REFUND:        Refunds → New → cumulative refunds cannot exceed order total
CHECK DEBT:         Customers → Outstanding Debt column
DEBT AGING:         Reports → Customer Debt Aging (who owes + how long overdue)
CHECK PROFIT:       Dashboard → set period → read Gross Profit + Margin
TOP CUSTOMERS:      Dashboard → scroll to "Top Customers" section
EXPORT DATA:        Export → choose Products / Customers / Orders / Inventory → CSV or XLSX
LOW STOCK ALERT:    Dashboard → Low Stock count (set min_stock_level on products)
NEGATIVE STOCK:     Dashboard → Negative Stock count (backorder/pre-order markers)
FORGOT PASSWORD:    Login page → "Forgot password?" (enter recovery code → set new password)
RECOVERY CODE:      Settings → Password Recovery Code → Generate (save the code shown)
```

---

*App runs at `http://localhost:5000` · Default login: `admin123` (change immediately)*

---

## 15. Local Windows Use Without Command Line

For a non-technical local-only user, the app can be packaged as a portable
Windows folder. The end user does not need to install Python or type commands.

The developer builds it once:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build_portable.ps1
```

Then share this folder:

```text
dist\TDResellerManager\
```

The user starts the app by double-clicking:

```text
Start TD Reseller Manager.bat
```

The app opens in the browser at:

```text
http://127.0.0.1:5000
```

All local data stays inside:

```text
TDResellerManager\data\
```

Copy that whole folder for backup or to move the local app to another Windows
PC. See `PACKAGING_WINDOWS.md` for build details.

---

## 16. Running on a Synology NAS (Home + Remote Access)

This section covers running the app on a **Synology DS218+** with DSM 7 so you
and your wife can access it from your phones, both at home and away from home.

The recommended deployment is now **Synology Container Manager/Docker**. It keeps
the app code separate from the live database and puts runtime data in a persistent
`data/` folder. See `DEPLOY_SYNOLOGY.md` for the full click-by-click deployment guide.

### 16.1 Is the App Suitable for This?

**Yes**, the app is already designed for this:

| Feature | Status |
|---|---|
| Container listens internally on `5000`, published on NAS port `5080` | Built-in |
| Sessions survive NAS reboots (persistent secret key in `.secret_key`) | Built-in |
| Hashed passwords (secure even if DB is copied) | Built-in |
| CSRF protection | Built-in |
| Works on Linux (NAS runs DSM on Linux) | Yes |

Three things to set up: **Container Manager**, **remote access**, and **in-app password recovery**.

---

### 16.2 Step 1 — Install Container Manager

1. In DSM, open **Package Center**.
2. Search for **Container Manager**.
3. If your DSM version still uses the older package name, search for **Docker**.
4. Install it.

The app includes these deployment files:

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app container |
| `docker-compose.synology.yml` | Container Manager project definition |
| `.dockerignore` | Prevents local DB/test files from being baked into the image |
| `DEPLOY_SYNOLOGY.md` | Full NAS setup guide |

Runtime data is stored in:

```
/volume1/docker/inventory_app_v5/data/
```

That folder contains the clean NAS `inventory.db`, `.secret_key`, backups, and product photos.

---

### 16.3 Step 2 — Create the Container Project

1. Create this folder on the NAS:
   ```
   /volume1/docker/inventory_app_v5/
   ```
2. Copy the app code there, but do **not** copy your local `inventory.db`,
   `.secret_key`, `backups/`, or test upload folders if you want a clean install.
3. Open **Container Manager → Project → Create**.
4. Project name: `inventory-app`
5. Path: `/volume1/docker/inventory_app_v5`
6. Use the contents of `docker-compose.synology.yml`.
7. Build and start the project.

Then visit `http://NAS-IP:5080` from a device on the same WiFi.

**Finding your NAS IP:** DSM → Control Panel → Network → Network Interface.
Or reserve the NAS IP in your router so the address stays stable.

---

### 16.4 Step 3 — Home Access (Local WiFi)

When you're at home on the same WiFi as the NAS:

```
http://192.168.1.xxx:5080        ← replace with your NAS IP
```

Bookmark this on both phones. No special setup required — the app already
listens on all interfaces.

**Tip:** Assign a static IP to your NAS in your router's DHCP settings so the
address never changes.

---

### 16.5 Step 4 — Remote Access (Away From Home)

Current deployment route: **Tailscale**.

Tailscale gives your phone/laptop a private network path to the NAS without
router port forwarding. This is the recommended approach for the current home
setup because the ISP modem does not allow normal inbound port forwarding.

Setup:
1. Install **Tailscale** on the Synology NAS from Package Center.
2. Open Tailscale on the NAS and sign in.
3. Install Tailscale on your phone/laptop and sign in to the same account.
4. Find the NAS Tailscale IP address, usually `100.x.y.z`.
5. When away from home, turn on Tailscale and open:

```text
http://100.x.y.z:5080
```

QuickConnect can still be useful for DSM itself, but it does not expose this
custom Flask/Container Manager app. Synology DDNS/reverse proxy can be revisited
later only if the network allows inbound HTTPS traffic.

---

### 16.6 Step 5 — In-App Password Recovery (Critical for Remote Use)

If you forget your password while away from home, SSH terminal access won't
be convenient. The app has a built-in recovery code for exactly this scenario.

#### First-time setup (do this once while logged in)

1. Go to **Settings → Password Recovery Code**
2. Click **Generate Recovery Code**
3. A 20-character code like `JBSW-Y3DP-EBZH-EZLT-AMYA` is displayed **once**
4. **Write it down on paper** and keep it somewhere safe (not on the NAS itself)
   — or store it in a password manager on your phone

#### When you're locked out (anywhere, any device)

1. Open the app URL on your phone → click **"Forgot password?"** on the login page
2. Enter the recovery code + your new password
3. The app resets your password and generates a **new recovery code** immediately
4. Save the new code before navigating away

#### Recovery code rules

- Each code can only be used **once** — a new one is generated on each use
- The code is never stored in plaintext (only its hash is in the database)
- If you lose the code: log in normally (if you remember the password), go to
  Settings, and generate a new code.

---

### 16.7 Recommended Setup Summary

For a household with two phone users:

| What | How |
|---|---|
| NAS auto-starts app on boot | Container Manager project restart policy |
| Home WiFi access | `http://NAS-IP:5080` bookmarked on both phones |
| Remote access | Tailscale, then `http://NAS-Tailscale-IP:5080` |
| Password recovery | Generate recovery code in Settings → write it down |
| User accounts | Admin for full access; optional limited account for lookup-only access |
| Regular backups | Settings → Download Backup (monthly or before big changes) |

---

### 16.8 Troubleshooting

**App not starting after reboot:**
- Check Container Manager → Project → `inventory-app` logs
- Confirm the project uses `docker-compose.synology.yml`
- Confirm `/volume1/docker/inventory_app_v5/data/` exists

**Login succeeds but the next page sends you back to login:**
- Rebuild the Container Manager project with the latest `Dockerfile` and `app.py`
- The container should run one Gunicorn worker and use `/data/.secret_key` for a persistent shared session key
- If this happened on an older build, stop the project, rebuild it, start it again, then log in fresh

**Can't reach the app from phone (home WiFi):**
- Confirm the NAS IP hasn't changed (set static IP in router)
- Check port 5080 isn't blocked by DSM firewall: Control Panel → Security → Firewall

**App forgets sessions after NAS reboot:**
- Should not happen — `/volume1/docker/inventory_app_v5/data/.secret_key` persists sessions across restarts
- If it does, confirm the compose file maps `./data:/data`

**WAL mode warning in logs:**
- The app prints a warning if SQLite WAL mode is unsupported by the filesystem
- This is safe to ignore — the app falls back gracefully and works correctly
*Database: `inventory.db` · Backup: Settings → Download Backup*
