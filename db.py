import aiosqlite
import os
from typing import Dict, List

# Database file path
DB_PATH = "trades.db"

# TradeRecord schema from CLAUDE.md:
# order_id TEXT, market_id TEXT, direction TEXT, size_usdc REAL,
# limit_price REAL, fill_price REAL, status TEXT, pnl_usdc REAL,
# paper_trade INTEGER, timestamp INTEGER, notes TEXT

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    order_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    size_usdc REAL NOT NULL,
    limit_price REAL NOT NULL,
    fill_price REAL,
    status TEXT NOT NULL,
    pnl_usdc REAL,
    paper_trade INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    notes TEXT
);
"""

async def init_db() -> None:
    """Create the trades table if it doesn't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()

async def insert_trade(record: Dict) -> None:
    """
    Insert a new trade record.
    Expected keys in record: order_id, market_id, direction, size_usdc, limit_price,
    fill_price, status, pnl_usdc, paper_trade, timestamp, notes
    """
    placeholders = ", ".join(["?"] * 11)
    columns = ", ".join(record.keys())
    sql = f"INSERT INTO trades ({columns}) VALUES ({placeholders})"
    values = tuple(record.values())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, values)
        await db.commit()

async def update_trade_status(order_id: str, status: str, fill_price: float, pnl: float) -> None:
    """Update the status, fill_price, and pnl for a trade."""
    sql = """
    UPDATE trades
    SET status = ?, fill_price = ?, pnl_usdc = ?
    WHERE order_id = ?
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, (status, fill_price, pnl, order_id))
        await db.commit()

async def get_daily_pnl(date_str: str) -> float:
    """
    Get total PNL for a given date (YYYY-MM-DD).
    Assumes timestamp is stored as Unix milliseconds.
    """
    # Convert date_str to timestamp range for the day
    # We'll do this in SQL for simplicity, but note: timestamp is in milliseconds
    # We need to compare the date part of the timestamp (in seconds) to the given date.
    # Since we don't have SQLite date functions for milliseconds, we'll convert:
    #   timestamp / 1000 gives seconds since epoch.
    # Then we can use date(timestamp/1000, 'unixepoch') to get the date string.
    sql = """
    SELECT COALESCE(SUM(pnl_usdc), 0.0)
    FROM trades
    WHERE date(timestamp/1000, 'unixepoch') = ?
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql, (date_str,)) as cursor:
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0

async def get_open_trades() -> List[Dict]:
    """Get all trades with status not equal to 'FILLED' or 'CANCELLED'?
    Actually, open trades are those that are not filled or cancelled?
    We'll assume status is one of: 'OPEN', 'FILLED', 'CANCELLED', etc.
    We'll consider trades with status != 'FILLED' and status != 'CANCELLED' as open.
    But let's follow the spec: get_open_trades -> list of dicts for trades that are open.
    We'll define open as status not in ('FILLED', 'CANCELLED', 'REJECTED')?
    However, the spec doesn't specify. We'll look at the update_trade_status: it updates status and fill_price.
    So when a trade is inserted, it might have status 'OPEN' or 'PENDING'.
    We'll return all trades where status is not 'FILLED' and not 'CANCELLED'.
    """
    sql = """
    SELECT order_id, market_id, direction, size_usdc, limit_price, fill_price, status, pnl_usdc, paper_trade, timestamp, notes
    FROM trades
    WHERE status NOT IN ('FILLED', 'CANCELLED')
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row  # This allows us to access columns by name
        async with db.execute(sql) as cursor:
            rows = await cursor.fetchall()
            # Convert each row to a dictionary
            return [dict(row) for row in rows]