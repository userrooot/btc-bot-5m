import asyncio
import json
import logging
import time
from collections import deque
from typing import Dict

import httpx
import pandas as pd
import pandas_ta as ta
import websockets

from config import BINANCE_REST_URL, BINANCE_WS_URL

logger = logging.getLogger(__name__)


async def fetch_initial_candles() -> deque:
    """Fetch last 200 closed 5m candles from Binance REST API."""
    params = {
        "symbol": "BTCUSDT",
        "interval": "5m",
        "limit": 200
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(BINANCE_REST_URL, params=params)
        response.raise_for_status()
        klines = response.json()

    candles = deque(maxlen=200)
    for kline in klines:
        candle: Dict = {
            "timestamp": int(kline[0]),  # Open time
            "open": float(kline[1]),
            "high": float(kline[2]),
            "low": float(kline[3]),
            "close": float(kline[4]),
            "volume": float(kline[5]),
            "closed": True  # All historical candles are closed
        }
        candles.append(candle)

    logger.info(f"Fetched {len(candles)} initial candles for seeding")
    return candles


def compute_features(candles: deque) -> Dict:
    """Compute technical indicators from candles deque."""
    if len(candles) < 2:
        # Not enough data to compute indicators
        latest = candles[-1] if candles else {}
        return {
            "timestamp": latest.get("timestamp", 0),
            "close": latest.get("close", 0.0),
            "rsi": 0.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "macd_hist": 0.0,
            "bb_upper": 0.0,
            "bb_mid": 0.0,
            "bb_lower": 0.0,
            "ema9": 0.0,
            "ema21": 0.0,
            "volume_change_pct": 0.0,
            "candle_body_pct": 0.0,
            "upper_wick_pct": 0.0,
            "lower_wick_pct": 0.0
        }

    # Convert deque to DataFrame for pandas-ta
    df = pd.DataFrame(list(candles))

    # Compute indicators
    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.ema(length=9, append=True)
    df.ta.ema(length=21, append=True)

    # Get latest values
    latest = df.iloc[-1]

    # Compute candle body and wick percentages
    latest_candle = candles[-1]
    open_price = latest_candle["open"]
    high_price = latest_candle["high"]
    low_price = latest_candle["low"]
    close_price = latest_candle["close"]

    # Avoid division by zero
    if high_price == low_price:
        candle_body_pct = 0.0
        upper_wick_pct = 0.0
        lower_wick_pct = 0.0
    else:
        candle_body = abs(close_price - open_price)
        total_range = high_price - low_price
        candle_body_pct = (candle_body / total_range) * 100

        upper_wick = high_price - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low_price
        upper_wick_pct = (upper_wick / total_range) * 100 if total_range > 0 else 0.0
        lower_wick_pct = (lower_wick / total_range) * 100 if total_range > 0 else 0.0

    # Volume change percentage (current vs previous)
    volume_change_pct = 0.0
    if len(candles) >= 2:
        prev_volume = candles[-2]["volume"]
        curr_volume = latest_candle["volume"]
        if prev_volume > 0:
            volume_change_pct = ((curr_volume - prev_volume) / prev_volume) * 100

    features: Dict = {
        "timestamp": int(latest_candle["timestamp"]),
        "close": float(latest_candle["close"]),
        "rsi": float(latest.get("RSI_14", 0.0)),
        "macd": float(latest.get("MACD_12_26_9", 0.0)),
        "macd_signal": float(latest.get("MACDs_12_26_9", 0.0)),
        "macd_hist": float(latest.get("MACDh_12_26_9", 0.0)),
        "bb_upper": float(latest.get("BBU_20_2.0", 0.0)),
        "bb_mid": float(latest.get("BBM_20_2.0", 0.0)),
        "bb_lower": float(latest.get("BBL_20_2.0", 0.0)),
        "ema9": float(latest.get("EMA_9", 0.0)),
        "ema21": float(latest.get("EMA_21", 0.0)),
        "volume_change_pct": float(volume_change_pct),
        "candle_body_pct": float(candle_body_pct),
        "upper_wick_pct": float(upper_wick_pct),
        "lower_wick_pct": float(lower_wick_pct)
    }

    return features


async def run_price_feed(price_queue: asyncio.Queue) -> None:
    """Main price feed loop that fetches candles and puts features into queue."""
    reconnect_delay = 1.0  # Start with 1 second
    max_reconnect_delay = 60.0  # Cap at 60 seconds

    # Seed with initial historical data
    candles = await fetch_initial_candles()

    while True:  # Reconnect loop
        try:
            logger.info(f"Connecting to Binance WebSocket: {BINANCE_WS_URL}")

            async with websockets.connect(
                BINANCE_WS_URL,
                ping_interval=20,
                ping_timeout=10
            ) as websocket:
                logger.info("Connected to Binance WebSocket")
                reconnect_delay = 1.0  # Reset delay on successful connection

                async for message in websocket:
                    try:
                        data = json.loads(message)

                        # Handle kline data
                        if "kline" in data:
                            kline = data["kline"]

                            # Only process closed candles
                            if kline.get("x", False):  # kline is closed
                                candle: Dict = {
                                    "timestamp": int(kline["t"]),  # Start time
                                    "open": float(kline["o"]),
                                    "high": float(kline["h"]),
                                    "low": float(kline["l"]),
                                    "close": float(kline["c"]),
                                    "volume": float(kline["v"]),
                                    "closed": True
                                }

                                candles.append(candle)

                                # Compute features and put in queue
                                features = compute_features(candles)

                                # Non-blocking put - skip if queue is full
                                if price_queue.full():
                                    logger.warning("Price queue is full, skipping feature put")
                                else:
                                    await price_queue.put(features)

                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse WebSocket message: {message}")
                    except KeyError as e:
                        logger.error(f"Missing expected key in kline data: {e}")
                    except Exception as e:
                        logger.error(f"Error processing kline: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")

        # Reconnect with exponential backoff
        logger.info(f"Reconnecting in {reconnect_delay} seconds...")
        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)