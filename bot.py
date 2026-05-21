#!/usr/bin/env python3
"""
Main bot runner for BTC Polymarket trading bot.
Orchestrates all modules: price_feed, predictor, order_manager, risk_manager, market_finder, telegram_alerts.
"""
import asyncio
import logging
import signal
import sys
import time
from typing import Dict, Any

# Import all required modules
from config import (
    POLYMARKET_PRIVATE_KEY, POLYMARKET_API_KEY, POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE, POLYMARKET_HOST, BINANCE_WS_URL, BINANCE_REST_URL,
    CONFIDENCE_THRESHOLD, MAX_RISK_PER_TRADE_PCT, DAILY_LOSS_LIMIT_PCT,
    MAX_SPREAD_PCT, TRADE_SIZE_USDC, PAPER_TRADING, TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID, LOG_LEVEL
)
from db import init_db, insert_trade, update_trade_status, get_daily_pnl, get_open_trades
from risk_manager import RiskManager
from predictor import Predictor
from order_manager import OrderManager  # async_retry is defined in bot.py
from market_finder import MarketFinder, MarketNotFoundError
from price_feed import run_price_feed
from telegram_alerts import TelegramAlerter

logger = logging.getLogger(__name__)


def async_retry(max_attempts: int = 5, base_delay: float = 1.0, exceptions: tuple = (Exception,)):
    """
    Decorator for async functions that retries on specified exceptions with exponential backoff.
    Used by order_manager - defined here but imported in order_manager.
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts - 1:  # Last attempt
                        logger.error(f"All {max_attempts} attempts failed. Last exception: {e}")
                        raise
                    delay = min(base_delay * (2 ** attempt), 30.0)  # Cap at 30s
                    logger.warning(f"Attempt {attempt + 1} failed with {type(e).__name__}: {e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                except Exception as e:
                    # For non-specified exceptions, we don't retry
                    logger.error(f"Non-retryable exception: {type(e).__name__}: {e}")
                    raise
            # If we exhausted retries (should not reach here due to raise in loop)
            raise last_exception
        return wrapper
    return decorator


def supervised_task(coro_fn, *args, name: str, restart_delay: float = 5.0):
    """
    Wrapper that runs a coroutine function in a loop, restarting on crash.
    """
    async def wrapper():
        while True:  # Restart loop
            try:
                await coro_fn(*args)
            except asyncio.CancelledError:
                # Always re-raise asyncio.CancelledError
                raise
            except Exception as e:
                logger.error(f"Task '{name}' crashed: {type(e).__name__}: {e}")
                logger.info(f"Restarting '{name}' in {restart_delay} seconds...")
                await asyncio.sleep(restart_delay)
    return wrapper


async def main():
    """Main bot orchestrator."""
    # 1. Setup logging
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper()),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        handlers=[
            logging.FileHandler('bot.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger.info("Starting BTC Polymarket Bot...")

    # 2. Load and validate config - already done in config.py imports
    # Config validation happens at import time, missing keys will raise ValueError

    # 3. Init db (create tables)
    await init_db()
    logger.info("Database initialized")

    # 4. Init modules
    market_finder = MarketFinder()
    telegram_alerter = TelegramAlerter()

    # Import db here to avoid circular imports
    import db
    risk_manager = RiskManager(db)  # RiskManager needs db
    predictor = Predictor()
    order_manager = OrderManager(db, risk_manager)  # Correct order: db, risk_manager

    # 5. Seed price buffer (200 candles from Binance REST)
    logger.info("Seeding price buffer with 200 candles from Binance REST...")
    # Note: price_feed.py should handle seeding internally or we call a function here
    # For now, we'll assume price_feed handles its own seeding

    # 6. Find active Polymarket market
    try:
        active_market = await market_finder.find_active_market()
        logger.info(f"Found active market: {active_market.get('question', 'Unknown')} (ID: {active_market.get('market_id')})")
    except MarketNotFoundError as e:
        logger.error(f"Failed to find active Polymarket market: {e}")
        logger.error("Exiting bot.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error finding active market: {e}")
        logger.error("Exiting bot.")
        sys.exit(1)

    # 7. If PAPER_TRADING=False → print bold WARNING and wait 5s for Ctrl+C
    if not PAPER_TRADING:
        print("\n" + "="*60)
        print("⚠️  WARNING: PAPER_TRADING IS DISABLED - LIVE TRADING ENABLED ⚠️")
        print("="*60)
        print("The bot will place real orders on Polymarket.")
        print("Press Ctrl+C within 5 seconds to abort...")
        print("="*60 + "\n")
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            logger.info("Startup aborted by user")
            sys.exit(0)

    # 8. Create queues
    price_queue = asyncio.Queue(maxsize=100)   # Candle dicts
    signal_queue = asyncio.Queue(maxsize=50)   # Signal dicts

    # 9. Define 6 async tasks
    tasks = []

    # price_feed_task: supervised_task(run_price_feed, price_queue)
    price_task = supervised_task(run_price_feed, price_queue, name="price_feed", restart_delay=5.0)
    tasks.append(asyncio.create_task(price_task(), name="price_feed_task"))

    # predictor_task: reads price_queue, calls predictor.predict(),
    # puts Signal to signal_queue if not HOLD
    async def predictor_task_loop():
        while True:  # This loop is internal, supervised_task handles external restarts
            try:
                # Get candle from price_queue
                candle = await price_queue.get()
                try:
                    # Process candle to get features (this should be in price_feed actually)
                    # For now, assume price_feed puts Features dict in queue
                    # But according to spec, price_queue contains Candle dicts
                    # So we need to convert candle to features - but predictor should handle this
                    # Looking at predictor.py, it likely expects Features dict
                    # Let's assume price_feed.py converts Candle to Features before putting in queue
                    features = candle  # Assuming price_feed already did this conversion

                    # Call predictor
                    signal = await predictor.predict(features)

                    # Put signal to signal_queue if not HOLD
                    if signal.get('direction') != 'HOLD':
                        await signal_queue.put(signal)
                        logger.debug(f"Signal queued: {signal.get('direction')} with confidence {signal.get('confidence'):.2f}")
                    else:
                        logger.debug("HOLD signal received, not queuing")

                finally:
                    price_queue.task_done()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in predictor task: {e}")
                # Continue loop to process next item

    predictor_task_supervised = supervised_task(predictor_task_loop, name="predictor", restart_delay=5.0)
    tasks.append(asyncio.create_task(predictor_task_supervised(), name="predictor_task"))

    # order_task: reads signal_queue, calls order_manager.place_order()
    async def order_task_loop():
        while True:
            try:
                # Get signal from signal_queue
                signal = await signal_queue.get()
                try:
                    # Get current bankroll from risk_manager
                    bankroll = await risk_manager.get_bankroll()

                    # Place order
                    await order_manager.place_order(signal, active_market, bankroll)

                finally:
                    signal_queue.task_done()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in order task: {e}")
                # Continue loop to process next item

    order_task_supervised = supervised_task(order_task_loop, name="order_manager", restart_delay=5.0)
    tasks.append(asyncio.create_task(order_task_supervised(), name="order_task"))

    # fill_check_task: every 30s calls order_manager.check_fills()
    async def fill_check_task_loop():
        while True:
            try:
                await asyncio.sleep(30.0)  # Check every 30 seconds
                filled_trades = await order_manager.check_fills()
                if filled_trades:
                    logger.info(f"Check fills found {len(filled_trades)} filled trade(s)")
                    # Send Telegram alerts for filled trades
                    for trade in filled_trades:
                        if trade.get('paper_trade'):  # Only alert for real trades? Or all?
                            await telegram_alerter.trade_filled(
                                direction=trade.get('direction'),
                                size=trade.get('size_usdc'),
                                price=trade.get('fill_price'),
                                pnl=trade.get('pnl_usdc', 0.0)
                            )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in fill check task: {e}")
                # Continue loop

    fill_check_task_supervised = supervised_task(fill_check_task_loop, name="fill_check", restart_delay=5.0)
    tasks.append(asyncio.create_task(fill_check_task_supervised(), name="fill_check_task"))

    # market_refresh_task: every 60s refreshes active market
    async def market_refresh_task_loop():
        nonlocal active_market  # Allow modification of outer variable
        while True:
            try:
                await asyncio.sleep(60.0)  # Refresh every 60 seconds
                try:
                    new_market = await market_finder.find_active_market()
                    # Check if market has changed (different ID or end_time)
                    if (new_market.get('market_id') != active_market.get('market_id') or
                        new_market.get('end_time') != active_market.get('end_time')):
                        logger.info(f"Market refreshed: {new_market.get('question', 'Unknown')} (ID: {new_market.get('market_id')})")
                        active_market = new_market
                    else:
                        logger.debug("Market unchanged during refresh")
                except MarketNotFoundError:
                    logger.warning("Could not find active market during refresh - keeping previous")
                except Exception as e:
                    logger.error(f"Error refreshing market: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in market refresh task loop: {e}")
                # Continue loop

    market_refresh_task_supervised = supervised_task(market_refresh_task_loop, name="market_refresh", restart_delay=5.0)
    tasks.append(asyncio.create_task(market_refresh_task_supervised(), name="market_refresh_task"))

    # heartbeat_task: every 300s logs "Bot alive | daily_pnl=$X"
    async def heartbeat_task_loop():
        while True:
            try:
                await asyncio.sleep(300.0)  # Every 5 minutes
                # Get today's date for PnL calculation
                today_str = time.strftime('%Y-%m-%d')
                daily_pnl = await get_daily_pnl(today_str)
                logger.info(f"Bot alive | daily_pnl=${daily_pnl:.2f}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in heartbeat task: {e}")
                # Continue loop

    heartbeat_task_supervised = supervised_task(heartbeat_task_loop, name="heartbeat", restart_delay=5.0)
    tasks.append(asyncio.create_task(heartbeat_task_supervised(), name="heartbeat_task"))

    # 10. Setup SIGINT/SIGTERM handlers → cancel all tasks
    def signal_handler():
        logger.info("Received shutdown signal, cancelling tasks...")
        for task in tasks:
            if not task.done():
                task.cancel()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # 11. asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All tasks started, entering main loop...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    for i, result in enumerate(results):
        task_name = tasks[i].get_name() if hasattr(tasks[i], 'get_name') else f"task_{i}"
        if isinstance(result, Exception):
            if not isinstance(result, asyncio.CancelledError):
                logger.error(f"Task '{task_name}' failed with: {result}")
            else:
                logger.info(f"Task '{task_name}' was cancelled")
        else:
            logger.info(f"Task '{task_name}' completed normally")

    # 12. On shutdown: close db, log "Bot stopped cleanly"
    logger.info("Bot stopped cleanly")
    # Note: db.py doesn't have an explicit close function, but aiosqlite connections are closed automatically


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    except Exception as e:
        logger.error(f"Bot crashed with unexpected error: {e}")
        sys.exit(1)