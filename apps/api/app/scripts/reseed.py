from __future__ import annotations

from app.db.init_db import reset_database


def main() -> None:
    print("Resetting database schema...")
    reset_database()
    print("Database reset complete.")


if __name__ == "__main__":
    main()
