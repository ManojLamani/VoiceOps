"""Reset and seed the VoiceOps database.

Usage (from backend/):  python scripts/seed.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.seed import seed_database  # noqa: E402
from app.db.database import close_db  # noqa: E402


async def main():
    await seed_database(reset=True)
    await close_db()
    print("Database reset and seeded.")
    print("  Admin: admin@voiceops.ai / admin123")
    print("  Agent: agent@voiceops.ai / agent123")


if __name__ == "__main__":
    asyncio.run(main())
