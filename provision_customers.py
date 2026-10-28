"""Create login accounts for every customer that has orders but no account yet.

    python provision_customers.py                    # new accounts get passcode 12345678
    python provision_customers.py --reset-existing   # also set 12345678 on customers who already have one
    python provision_customers.py --passcode S0mething-Else

The default passcode is deliberately weak: use it for demos only. The demo accounts defined in
.streamlit/secrets.toml (including staff) are never touched. Customers found in shopease.db and in
seed_data/orders.csv are both covered.
"""

import argparse
import csv
import sqlite3
import tomllib
from contextlib import closing
from pathlib import Path

import auth

HERE = Path(__file__).resolve().parent
SEED_ORDERS = HERE / "seed_data" / "orders.csv"
DEFAULT_PASSCODE = "12345678"


def customer_emails() -> set[str]:
    emails = set()
    if auth.SHOP_DB.exists():
        with closing(sqlite3.connect(f"file:{auth.SHOP_DB}?mode=ro", uri=True)) as c:
            emails |= {r[0].lower() for r in c.execute("SELECT DISTINCT email FROM orders")}
    if SEED_ORDERS.exists():
        with open(SEED_ORDERS, encoding="utf-8") as f:
            emails |= {row["email"].lower() for row in csv.DictReader(f)}
    return emails


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passcode", default=DEFAULT_PASSCODE, help=f"passcode to set (default {DEFAULT_PASSCODE})")
    parser.add_argument("--reset-existing", action="store_true", help="also overwrite passcodes of existing customer accounts")
    args = parser.parse_args()

    # Load the pre-provisioned demo accounts first so they keep their own passcodes.
    secrets_file = HERE / ".streamlit" / "secrets.toml"
    seed_users = tomllib.loads(secrets_file.read_text(encoding="utf-8")).get("users", []) if secrets_file.exists() else []
    auth.init(seed_users)
    protected = {u["email"] for u in seed_users}

    created, reset = [], []
    for email in sorted(customer_emails() - protected):
        if auth.provision(email, args.passcode):
            created.append(email)
        elif args.reset_existing:
            auth.set_passcode(email, args.passcode)
            reset.append(email)

    print(f"Created {len(created)} accounts, reset {len(reset)} existing. Passcode: {args.passcode}")
    for email in created + reset:
        print(" ", email)


if __name__ == "__main__":
    main()
