import asyncio
import logging
import time
from typing import Dict, List, Optional, Any

# Assuming py-clob-client is available
try:
    from clob_client.client import Client as ClobClient
except ImportError:
    ClobClient = None

# Import async_retry from bot.py
from bot import async_retry

logger = logging.getLogger(__name__)

class OrderManager:
    def __init__(self, db, risk_manager):
        """
        Initialize the OrderManager with database and risk manager references.
        Also initializes the py-clob-client.

        Args:
            db: The database module (db.py) instance.
            risk_manager: The risk manager instance.
        """
        self.db = db
        self.risk_manager = risk_manager

        # Import config inside method to avoid circular imports
        from config import POLYMARKET_PRIVATE_KEY, POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE, POLYMARKET_HOST

        if ClobClient is None:
            raise ImportError("py-clob-client is not installed. Install it to use this class.")

        self.client = ClobClient(
            host=POLYMARKET_HOST,
            key=POLYMARKET_PRIVATE_KEY,
            api_key=POLYMARKET_API_KEY,
            api_secret=POLYMARKET_API_SECRET,
            api_passphrase=POLYMARKET_API_PASSPHRASE
        )
        logger.info("OrderManager initialized with py-clob-client")

    @async_retry(max_attempts=5, base_delay=1.0,
                 exceptions=(ConnectionError, TimeoutError, Exception))
    async def _get_order_book(self, token_id: str) -> Dict:
        """
        Fetch order book for a given token ID.
        Wrapped with async_retry.
        """
        return self.client.get_order_book(token_id=token_id)

    @async_retry(max_attempts=5, base_delay=1.0,
                 exceptions=(ConnectionError, TimeoutError, Exception))
    async def _create_order(self, token_id: str, price: float, size: float, side: str) -> Dict:
        """
        Create an order via the CLOB client.
        side: 'buy' or 'sell'
        Wrapped with async_retry.
        """
        # Assuming the client method creates a limit order with GTC (Good Till Cancelled)
        # We need to check the actual py-clob-client API. For now, we assume:
        #   create_order(token_id, price, size, side)
        return self.client.create_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side
        )

    @async_retry(max_attempts=5, base_delay=1.0,
                 exceptions=(ConnectionError, TimeoutError, Exception))
    async def _cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an order by order ID.
        Wrapped with async_retry.
        """
        return self.client.cancel_order(order_id=order_id)

    @async_retry(max_attempts=5, base_delay=1.0,
                 exceptions=(ConnectionError, TimeoutError, Exception))
    async def _get_order_status(self, order_id: str) -> Dict:
        """
        Get the status of an order by order ID.
        Wrapped with async_retry.
        """
        return self.client.get_order_status(order_id=order_id)

    async def place_order(self, signal: Dict, market: Dict, bankroll: float) -> None:
        """
        Place an order based on a signal and market conditions.

        Args:
            signal: Signal dict from predictor (direction, confidence, etc.)
            market: Market dict from market_finder
            bankroll: Current bankroll in USDC
        """
        direction = signal.get('direction')
        confidence = signal.get('confidence', 0.0)

        # Skip if not UP or DOWN
        if direction not in ('UP', 'DOWN'):
            logger.info(f"Signal direction is {direction}. Skipping order placement.")
            return

        # Get trade size from risk manager
        size = await self.risk_manager.get_trade_size(confidence, bankroll)
        if size == 0.0:
            logger.info(f"Trade size calculated as 0.0 (confidence={confidence}, bankroll={bankroll}). Skipping order.")
            return

        # Determine which token to buy based on direction
        if direction == 'UP':
            token_id = market.get('token_id_yes')
            token_side = 'buy'  # buying YES token
        else:  # DOWN
            token_id = market.get('token_id_no')
            token_side = 'buy'  # buying NO token

        if not token_id:
            logger.error(f"Missing token ID for direction {direction}")
            return

        # Fetch order book for the token
        try:
            order_book = await self._get_order_book(token_id)
        except Exception as e:
            logger.error(f"Failed to fetch order book for token {token_id}: {e}")
            return

        # Extract best bid and ask
        bids = order_book.get('bids', [])
        asks = order_book.get('asks', [])
        if not bids or not asks:
            logger.warning(f"Order book for token {token_id} has no bids or asks. Skipping.")
            return

        best_bid = float(bids[0]['price'])
        best_ask = float(asks[0]['price'])
        mid_price = (best_bid + best_ask) / 2.0

        # Check spread
        spread = best_ask - best_bid
        if mid_price > 0:
            spread_pct = spread / mid_price
        else:
            spread_pct = float('inf')

        from config import MAX_SPREAD_PCT
        if spread_pct > MAX_SPREAD_PCT:
            logger.info(f"Spread too wide: {spread_pct:.4f} > {MAX_SPREAD_PCT}. Skipping order.")
            return

        # Calculate limit price: best_bid + 0.005 (0.5¢ inside spread)
        limit_price = best_bid + 0.005
        # Ensure limit price does not exceed best_ask (shouldn't by construction, but safety)
        if limit_price > best_ask:
            limit_price = best_ask

        logger.info(
            f"Placing {direction} order: token={token_id}, side={token_side}, "
            f"size={size} USDC, limit_price={limit_price:.4f}, "
            f"best_bid={best_bid:.4f}, best_ask={best_ask:.4f}, spread_pct={spread_pct:.4f}"
        )

        # Check if we are in paper trading mode
        from config import PAPER_TRADING
        if PAPER_TRADING:
            # Simulate fill at mid price
            fill_price = mid_price
            logger.info(f"PAPER TRADING: Simulated fill at {fill_price:.4f}")

            # Create a TradeRecord and insert into db
            trade_record = {
                "order_id": f"paper_{int(time.time() * 1000)}",  # fake order ID
                "market_id": market.get('market_id'),
                "direction": direction,
                "size_usdc": size,
                "limit_price": limit_price,
                "fill_price": fill_price,
                "status": "FILLED",
                "pnl_usdc": 0.0,  # PnL will be updated later when market resolves
                "paper_trade": 1,
                "timestamp": int(time.time() * 1000),
                "notes": "Paper trade simulation"
            }
            await self.db.insert_trade(trade_record)
            logger.info(f"Inserted paper trade record for order {trade_record['order_id']}")
        else:
            # Live trading: create the order
            try:
                order_response = await self._create_order(
                    token_id=token_id,
                    price=limit_price,
                    size=size,
                    side=token_side
                )
                order_id = order_response.get('order_id') or order_response.get('id')  # adjust based on actual response
                if not order_id:
                    logger.error(f"Order creation failed: no order ID in response: {order_response}")
                    return

                logger.info(f"Order placed successfully: order_id={order_id}")

                # Insert initial trade record (status OPEN or PENDING)
                trade_record = {
                    "order_id": order_id,
                    "market_id": market.get('market_id'),
                    "direction": direction,
                    "size_usdc": size,
                    "limit_price": limit_price,
                    "fill_price": 0.0,  # will be updated on fill
                    "status": "OPEN",
                    "pnl_usdc": 0.0,
                    "paper_trade": 0,
                    "timestamp": int(time.time() * 1000),
                    "notes": ""
                }
                await self.db.insert_trade(trade_record)
                logger.info(f"Inserted trade record for order {order_id}")

            except Exception as e:
                logger.error(f"Failed to place order: {e}")
                return

    async def cancel_stale_orders(self, older_than_seconds: int = 240) -> None:
        """
        Fetch open orders from db, cancel via API if older than threshold.

        Args:
            older_than_seconds: Orders older than this (in seconds) will be cancelled.
        """
        try:
            open_trades = await self.db.get_open_trades()
        except Exception as e:
            logger.error(f"Failed to fetch open trades: {e}")
            return

        now_ms = int(time.time() * 1000)
        for trade in open_trades:
            order_id = trade.get('order_id')
            timestamp_ms = trade.get('timestamp', 0)
            age_seconds = (now_ms - timestamp_ms) / 1000.0

            if age_seconds > older_than_seconds:
                logger.info(f"Cancelling stale order {order_id} (age: {age_seconds:.1f}s)")
                try:
                    await self._cancel_order(order_id)
                    # Update trade status to CANCELLED in db
                    await self.db.update_trade_status(order_id, "CANCELLED", 0.0, 0.0)
                    logger.info(f"Order {order_id} cancelled and updated in db.")
                except Exception as e:
                    logger.error(f"Failed to cancel order {order_id}: {e}")
            else:
                logger.debug(f"Order {order_id} is not stale (age: {age_seconds:.1f}s).")

    async def check_fills(self) -> List[Dict]:
        """
        Poll Polymarket for fill status of all open orders.
        Update db and call risk_manager.record_result for each fill.

        Returns:
            List of dictionaries representing the filled trades (with updated info).
        """
        filled_trades = []
        try:
            open_trades = await self.db.get_open_trades()
        except Exception as e:
            logger.error(f"Failed to fetch open trades for fill check: {e}")
            return filled_trades

        for trade in open_trades:
            order_id = trade.get('order_id')
            if not order_id:
                continue

            try:
                order_status = await self._get_order_status(order_id)
            except Exception as e:
                logger.error(f"Failed to get status for order {order_id}: {e}")
                continue

            # Assuming the order status has a 'status' field and possibly 'filled_size' or similar
            status = order_status.get('status', '').upper()
            if status in ('FILLED', 'PARTIALLY_FILLED'):
                # We consider it filled if status is FILLED. For PARTIALLY_FILLED, we might need to handle differently.
                # For simplicity, we'll treat PARTIALLY_FILLED as filled for now.
                filled_size = float(order_status.get('filled_size', trade.get('size_usdc')))
                avg_price = float(order_status.get('average_price', 0.0))

                # Update the trade in db with fill price and status
                # We set pnl to 0.0 for now - it will be updated later when market resolves
                new_status = "FILLED" if status == "FILLED" else "PARTIALLY_FILLED"
                fill_price = avg_price if status == "FILLED" else 0.0

                try:
                    await self.db.update_trade_status(order_id, new_status, fill_price, 0.0)
                    logger.info(f"Updated trade {order_id} status to {new_status} with fill_price {fill_price}")
                except Exception as e:
                    logger.error(f"Failed to update trade {order_id} in db: {e}")
                    continue

                # Call risk_manager.record_result for each fill (as per spec)
                # At fill time, we don't know the actual PnL yet (market hasn't resolved).
                # We pass pnl=0.0 as a placeholder. The actual PnL will be updated when the market resolves.
                try:
                    await self.risk_manager.record_result(order_id, 0.0)
                    logger.info(f"Recorded result for order {order_id} with pnl 0.0 (placeholder)")
                except Exception as e:
                    logger.error(f"Failed to record result for order {order_id}: {e}")

                # Add to filled_trades list to return
                filled_trades.append({
                    "order_id": order_id,
                    "market_id": trade.get('market_id'),
                    "direction": trade.get('direction'),
                    "size_usdc": trade.get('size_usdc'),
                    "limit_price": trade.get('limit_price'),
                    "fill_price": fill_price,
                    "status": new_status,
                    "pnl_usdc": 0.0,  # placeholder - will be updated when market resolves
                    "paper_trade": trade.get('paper_trade'),
                    "timestamp": trade.get('timestamp'),
                    "notes": trade.get('notes')
                })

        return filled_trades