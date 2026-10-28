"""Generate 100 raw records for every table in shopease.db.

    python generate_seed_data.py            # writes CSV files to seed_data/ (does not touch the DB)
    python generate_seed_data.py --load     # also backs up shopease.db and appends the records to it

Records follow the same rules as mcp_server.py (30-day refund window, 200.00 auto-approval limit,
one non-rejected refund per order), and dates are relative to today. Output is repeatable: the same
day always produces the same data (fixed random seed).
"""

import argparse
import csv
import random
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "seed_data"
DB_PATH = HERE / "shopease.db"
COUNT = 100
AUTO_REFUND_LIMIT = 200.00  # keep in sync with mcp_server.py

random.seed(42)
NOW = datetime.now().replace(second=0, microsecond=0)


def ts(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


# ---------------------------------------------------------------- inventory
CATALOGUE = {
    # prefix: (products, (min price, max price))
    "HP": (["Wireless Headphones", "Noise-Cancelling Headphones", "Gaming Headset", "Earbuds", "Sport Earbuds"], (30, 220)),
    "SW": (["Smart Watch", "Fitness Band", "Smart Watch Lite", "Kids Smart Watch"], (25, 320)),
    "KB": (["Mechanical Keyboard", "Wireless Keyboard", "Compact Keyboard", "Backlit Keyboard"], (25, 140)),
    "MS": (["Wireless Mouse", "Gaming Mouse", "Ergonomic Mouse", "Travel Mouse"], (12, 90)),
    "LT": (["UltraBook 13", "UltraBook 15", "Gaming Laptop", "Student Laptop", "Business Laptop"], (350, 1900)),
    "MN": (["24-inch Monitor", "27-inch Monitor", "Curved Monitor", "Portable Monitor"], (110, 520)),
    "SP": (["Bluetooth Speaker", "Soundbar", "Party Speaker", "Mini Speaker"], (20, 260)),
    "CH": (["Fast Charger", "Power Bank", "USB-C Cable Pack", "Wireless Charging Pad"], (8, 70)),
    "CM": (["Webcam HD", "Action Camera", "Security Camera", "Ring Light"], (18, 240)),
    "TB": (["Tablet 10-inch", "Tablet 8-inch", "Drawing Tablet", "E-Reader"], (70, 600)),
}
VARIANTS = ["", " Pro", " Max", " Mini", " SE", " Plus"]
WAREHOUSES = ["Chennai-WH1", "Bengaluru-WH2", "Mumbai-WH3", "Delhi-WH4", "Hyderabad-WH5"]


def make_inventory():
    rows, prices, seen = [], {}, set()
    n = 401  # clear of the 4 demo SKUs (HP-200, SW-450, KB-310, LT-900)
    while len(rows) < COUNT:
        prefix = random.choice(list(CATALOGUE))
        products, (lo, hi) = CATALOGUE[prefix]
        name = random.choice(products) + random.choice(VARIANTS)
        if name in seen:
            continue
        seen.add(name)
        sku = f"{prefix}-{n}"
        n += 1
        prices[sku] = round(random.uniform(lo, hi), 2)
        out_of_stock = random.random() < 0.15
        stock = 0 if out_of_stock else random.choice([random.randint(1, 10), random.randint(11, 250)])
        restock = (NOW + timedelta(days=random.randint(3, 30))).date().isoformat() if out_of_stock else None
        rows.append((sku, name, stock, random.choice(WAREHOUSES), restock))
    return rows, prices


# ------------------------------------------------------------------- orders
FIRST = ["aarav", "diya", "kabir", "ananya", "rohan", "ishita", "vihaan", "saanvi", "aditya", "myra",
         "karthik", "lakshmi", "nikhil", "pooja", "rahul", "sneha", "suresh", "divya", "manoj", "kavya",
         "arvind", "nisha", "varun", "tara", "dev", "ritu", "harish", "meghna", "sanjay", "leela"]
CUSTOMERS = [f"{name}@example.com" for name in FIRST]  # deliberately none of the 3 demo customers


def make_orders(inventory, prices):
    statuses = ["delivered"] * 80 + ["in_transit"] * 11 + ["processing"] * 9
    random.shuffle(statuses)
    rows, tracking = [], 9100
    for i, status in enumerate(statuses):
        order_id = f"ORD-{2001 + i}"
        sku = random.choice(inventory)[0]
        qty = random.choices([1, 2, 3], weights=[75, 18, 7])[0]
        total = round(prices[sku] * qty, 2)
        tracking_id = delivered_at = None
        if status == "delivered":
            placed = NOW - timedelta(days=random.randint(7, 90), hours=random.randint(0, 23), minutes=random.randint(0, 59))
            delivered_at = ts(min(NOW, placed + timedelta(days=random.randint(2, 5), hours=random.randint(0, 8))))
        elif status == "in_transit":
            placed = NOW - timedelta(days=random.randint(1, 4), hours=random.randint(0, 12))
        else:
            placed = NOW - timedelta(hours=random.randint(0, 30))
        if status != "processing":
            tracking_id = f"TRK-{tracking}"
            tracking += 1
        rows.append((order_id, random.choice(CUSTOMERS), sku, qty, total, status, tracking_id, ts(placed), delivered_at))
    return rows


# ------------------------------------------------------------------ refunds
REFUND_REASONS = [
    "Arrived damaged", "Stopped working after a few days", "Wrong item received", "Not as described",
    "Missing accessories", "Screen cracked on arrival", "Battery drains too fast", "Changed my mind",
    "Defective on arrival", "Item arrived late and no longer needed",
]


def make_refunds(orders):
    delivered = [o for o in orders if o[5] == "delivered"]
    non_rejected = random.sample(delivered, 66)  # one live refund per order, like the server enforces
    rows = []
    for o in non_rejected:
        order_id, total, delivered_at = o[0], o[4], datetime.fromisoformat(o[8])
        amount = total if random.random() < 0.6 else round(total * random.uniform(0.3, 0.9), 2)
        status = "approved" if amount <= AUTO_REFUND_LIMIT else "pending_human_review"
        created = min(NOW, delivered_at + timedelta(days=random.randint(0, 20), hours=random.randint(1, 20)))
        rows.append((order_id, amount, random.choice(REFUND_REASONS), status, created.isoformat(timespec="seconds")))
    for o in random.choices(orders, k=COUNT - len(rows)):  # rejected requests (can repeat per order)
        base = datetime.fromisoformat(o[8] or o[7])
        amount = round(o[4] * random.choice([1, 1, 0.5]), 2)
        created = min(NOW, base + timedelta(days=random.randint(1, 45), hours=random.randint(1, 20)))
        rows.append((o[0], amount, random.choice(REFUND_REASONS), "rejected", created.isoformat(timespec="seconds")))
    rows.sort(key=lambda r: r[4])
    return rows


# ------------------------------------------------------------------ tickets
TICKET_SUMMARIES = [
    ("Customer reports {item} on {oid} arrived damaged; refund of {total} needs manual review.", "high"),
    ("Refund for {oid} ({item}) is above the auto-approval limit; awaiting human approval.", "high"),
    ("Customer says {item} from {oid} stopped working; asked for replacement or refund.", "medium"),
    ("Tracking for {oid} has not updated for several days; please contact the carrier.", "medium"),
    ("Customer received the wrong item in {oid}; expected {item}.", "medium"),
    ("Refund request on {oid} was rejected (outside the 30-day window); customer is disputing it.", "medium"),
    ("Customer wants to change the delivery address for {oid} before dispatch.", "low"),
    ("Customer asks for an invoice copy for {oid}.", "low"),
    ("Customer asks when {item} will be back in stock.", "low"),
    ("Package for {oid} marked delivered but customer says it never arrived.", "high"),
]
TICKET_STATUS_WEIGHTS = {"open": 30, "in_progress": 25, "resolved": 45}


def make_tickets(orders, inventory):
    names = {sku: name for sku, name, *_ in inventory}
    rows = []
    for _ in range(COUNT):
        o = random.choice(orders)
        summary, priority = random.choice(TICKET_SUMMARIES)
        summary = summary.format(oid=o[0], item=names[o[2]], total=f"{o[4]:.2f}")
        status = random.choices(list(TICKET_STATUS_WEIGHTS), weights=TICKET_STATUS_WEIGHTS.values())[0]
        created = NOW - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23), minutes=random.randint(0, 59))
        rows.append((o[1], summary, priority, status, created.isoformat(timespec="seconds")))
    rows.sort(key=lambda r: r[4])
    return rows


# --------------------------------------------------------------------- main
TABLES = {
    "inventory": ("sku", "name", "stock", "warehouse", "restock_date"),
    "orders": ("order_id", "email", "sku", "qty", "total", "status", "tracking_id", "placed_at", "delivered_at"),
    "refunds": ("order_id", "amount", "reason", "status", "created_at"),
    "tickets": ("email", "summary", "priority", "status", "created_at"),
}


def write_csv(name: str, header: tuple, rows: list) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def load(data: dict) -> None:
    if not DB_PATH.exists():
        raise SystemExit("shopease.db not found. Run `python mcp_server.py` once to create it, then retry.")
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM orders WHERE order_id = 'ORD-2001'").fetchone():
            raise SystemExit("These records are already loaded (ORD-2001 exists). Nothing changed.")
    backup = DB_PATH.with_suffix(".db.bak")
    shutil.copy2(DB_PATH, backup)
    with sqlite3.connect(DB_PATH) as conn:
        for table, header in TABLES.items():
            marks = ",".join("?" * len(header))
            conn.executemany(f"INSERT INTO {table} ({','.join(header)}) VALUES ({marks})", data[table])
    print(f"Loaded {COUNT} rows into each table. Backup of the previous DB: {backup.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--load", action="store_true", help="append the records to shopease.db (after a backup)")
    args = parser.parse_args()

    inventory, prices = make_inventory()
    orders = make_orders(inventory, prices)
    data = {
        "inventory": inventory,
        "orders": orders,
        "refunds": make_refunds(orders),
        "tickets": make_tickets(orders, inventory),
    }
    for table, header in TABLES.items():
        write_csv(table, header, data[table])
        print(f"{table}: {len(data[table])} records -> seed_data/{table}.csv")
    if args.load:
        load(data)


if __name__ == "__main__":
    main()
