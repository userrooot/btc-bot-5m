import aiosqlite
import logging
from datetime import datetime
from typing import Optional

from config import (
    CONFIDENCE_THRESHOLD,
    DAILY_LOSS_LIMIT_PCT,
    MAX_RISK_PER_TRADE_PCT,
    TRADE_SIZE_USDC,
)
from db import get_daily_pnl, update_trade_status

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, db):
        """
        Initialize the risk manager with a database reference.

        Args:
            db: The database module (db.py) module.
                We expect it to have get_daily_pnl and update_trade_status functions,
                and DB_PATH attribute.
        """
        self.db = db

    async def get_trade_size(self, confidence: float, bankroll: float) -> float:
        """
        Calculate the trade size in USDC based on confidence and bankroll.

        Returns 0.0 if the daily loss limit has been breached.

        Args:
            confidence: Prediction confidence (0.0 - 1.0)
            bankroll: Current bankroll in USDC

        Returns:
            Trade size in USDC, rounded to 2 decimal places
        """
        # Check if daily loss limit is breached
        if await self.is_daily_limit_breached(bankroll):
            logger.warning("Daily loss limit breached. Returning trade size 0.0")
            return 0.0

        # Base size is a percentage of bankroll
        base_size = bankroll * MAX_RISK_PER_TRADE_PCT

        # Scale by confidence: only trade when confidence > CONFIDENCE_THRESHOLD (0.65)
        # At confidence = CONFIDENCE_THRESHOLD, multiplier = 0
        # At confidence = 1.0, multiplier = 1.0
        if confidence <= CONFIDENCE_THRESHOLD:
            logger.debug(f"Confidence {confidence} <= threshold {CONFIDENCE_THRESHOLD}. No trade.")
            return 0.0

        confidence_multiplier = (confidence - CONFIDENCE_THRESHOLD) / (1.0 - CONFIDENCE_THRESHOLD)
        size = base_size * confidence_multiplier

        # Cap at maximum trade size
        size = min(size, TRADE_SIZE_USDC)

        # Round to 2 decimal places
        size = round(size, 2)

        logger.debug(
            f"Calculated trade size: bankroll={bankroll}, confidence={confidence}, "
            f"base_size={base_size}, multiplier={confidence_multiplier}, size={size}"
        )
        return size

    async def is_daily_limit_breached(self, bankroll: float) -> bool:
        """
        Check if the daily loss limit has been breached.

        Args:
            bankroll: Current bankroll in USDC

        Returns:
            True if daily loss limit is breached, False otherwise
        """
        # Get today's date in YYYY-MM-DD format (UTC)
        today_str = datetime.utcnow().date().isoformat()

        # Get today's PnL from the database
        daily_pnl = await self.db.get_daily_pnl(today_str)
        logger.debug(f"Today's PnL: {daily_pnl}")

        # Calculate the loss limit (negative value)
        loss_limit = bankroll * DAILY_LOSS_LIMIT_PCT
        # We consider the limit breached if PnL <= -loss_limit (i.e., losses exceed the limit)
        if daily_pnl <= -loss_limit:
            logger.warning(
                f"Daily loss limit breached: PnL={daily_pnl}, limit={-loss_limit}"
            )
            return True

        return False

    async def record_result(self, order_id: str, pnl: float) -> None:
        """
        Record the result of a trade in the database.

        Args:
            order_id: The order ID of the trade
            pnl: Profit and loss in USDC (can be negative)
        """
        # Get the current trade to get the fill_price and status
        try:
            async with aiosqlite.connect(self.db.DB_PATH) as conn:
                conn.row_factory = aiosqlite.Row
                async with conn.execute(
                    "SELECT status, fill_price FROM trades WHERE order_id = ?", (order_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row is None:
                        logger.error(f"Trade with order_id {order_id} not found.")
                        return
                    status = row["status"]
                    fill_price = row["fill_price"]
        except Exception as e:
            logger.error(f"Error fetching trade {order_id}: {e}")
            return

        # Update the trade: keep status and fill_price, set pnl
        try:
            await update_trade_status(order_id, status, fill_price, pnl)
            logger.info(f"Recorded result for order {order_id}: PnL={pnl}")
        except Exception as e:
            logger.error(f"Error updating trade {order_id}: {e}")