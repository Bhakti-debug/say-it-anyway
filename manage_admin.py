"""Grant or remove the moderator role for an existing account.

Run this only from the machine that owns the app/database:
    .venv\\Scripts\\python.exe manage_admin.py promote username
    .venv\\Scripts\\python.exe manage_admin.py demote username
"""

import argparse
import os
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR))).expanduser().resolve()
DATABASE_PATH = DATA_DIR / "quiet_space.db"


def main():
    parser = argparse.ArgumentParser(description="Manage Quiet Space moderator access.")
    parser.add_argument("action", choices=("promote", "demote"))
    parser.add_argument("username", help="An existing account username")
    arguments = parser.parse_args()

    with sqlite3.connect(DATABASE_PATH) as connection:
        cursor = connection.execute(
            "UPDATE users SET is_moderator = ? WHERE username = ? COLLATE NOCASE",
            (1 if arguments.action == "promote" else 0, arguments.username.strip()),
        )
        if cursor.rowcount == 0:
            parser.error(f"No account named @{arguments.username.strip()} exists yet.")

    role = "moderator" if arguments.action == "promote" else "regular user"
    print(f"@{arguments.username.strip()} is now a {role}. Refresh the app to update access.")


if __name__ == "__main__":
    main()
