import argparse
import asyncio
import time
import httpx
import numpy as np
import pandas as pd
import pandas_ta as ta
from collections import deque
import os
import csv
from predictor import Predictor, FEATURE_COLUMNS
from price_feed import compute_features

BINANCE_REST_URL = "https://api.binance.com/api/v3/klines"

async def fetch_binance_data(symbol, interval, start_date, end_date):
    """Fetch Binance data for date range via REST API."""
    # Convert dates to timestamps
    start_timestamp = int(pd.Timestamp(start_date).timestamp() * 1000)
    end_timestamp = int(pd.Timestamp(end_date).timestamp() * 1000)

    all_candles = []
    current_start = start_timestamp

    async with httpx.AsyncClient() as client:
        while current_start < end_timestamp:
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": 1000,
                "startTime": current_start
            }

            try:
                response = await client.get(BINANCE_REST_URL, params=params)
                response.raise_for_status()
                klines = response.json()

                if not klines:
                    break

                for kline in klines:
                    timestamp = int(kline[0])
                    if timestamp > end_timestamp:
                        break

                    candle = {
                        "timestamp": timestamp,
                        "open": float(kline[1]),
                        "high": float(kline[2]),
                        "low": float(kline[3]),
                        "close": float(kline[4]),
                        "volume": float(kline[5]),
                        "closed": True
                    }
                    all_candles.append(candle)

                # Update start time for next batch
                if len(klines) < 1000:
                    break
                current_start = klines[-1][0] + 1  # Next candle after last one

            except Exception as e:
                print(f"Error fetching data: {e}")
                break

    # Sort by timestamp
    all_candles.sort(key=lambda x: x["timestamp"])
    print(f"Fetched {len(all_candles)} candles from {all_candles[0]['timestamp'] if all_candles else 'N/A'} to {all_candles[-1]['timestamp'] if all_candles else 'N/A'}")
    return all_candles

def load_or_fetch_data(symbol, interval, start_date, end_date):
    """Load data from CSV if exists, otherwise fetch from Binance."""
    csv_filename = f"data/{symbol}_{interval}_{start_date}_{end_date}.csv"

    # Check if CSV exists
    if os.path.exists(csv_filename):
        print(f"Loading data from {csv_filename}")
        df = pd.read_csv(csv_filename)
        candles = []
        for _, row in df.iterrows():
            candle = {
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "closed": True
            }
            candles.append(candle)
        return candles

    # Fetch fresh data
    print(f"Fetching fresh data for {symbol} {interval} from {start_date} to {end_date}")
    candles = asyncio.run(fetch_binance_data(symbol, interval, start_date, end_date))

    # Save to CSV for future use
    if candles:
        os.makedirs("data", exist_ok=True)
        df_data = []
        for candle in candles:
            df_data.append({
                "timestamp": candle["timestamp"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"]
            })
        df = pd.DataFrame(df_data)
        df.to_csv(csv_filename, index=False)
        print(f"Saved data to {csv_filename}")

    return candles

def backtest(candles, confidence_threshold=0.65, initial_bankroll=1000.0):
    """Run backtest on historical data."""
    predictor = Predictor()

    # Initialize tracking variables
    bankroll = initial_bankroll
    position = 0  # 0 = no position, 1 = long, -1 = short
    entry_price = 0.0
    entry_timestamp = 0

    trades = []  # List to store trade results
    equity_curve = [initial_bankroll]  # Track equity over time

    print(f"Starting backtest with {len(candles)} candles")
    print(f"Initial bankroll: ${initial_bankroll:.2f}")
    print(f"Confidence threshold: {confidence_threshold}")
    print("-" * 50)

    # Need at least 200 candles to compute features
    for i in range(199, len(candles) - 1):
        # Create window of 200 candles for feature computation
        window = candles[i-199:i+1]
        candle_deque = deque(window, maxlen=200)

        # Compute features
        features = compute_features(candle_deque)

        # Get prediction
        signal = predictor.predict(features)
        direction = signal["direction"]
        confidence = signal["confidence"]

        current_candle = candles[i]
        next_candle = candles[i + 1]

        # Current candle close price
        current_close = current_candle["close"]
        # Next candle open price (entry price)
        next_open = next_candle["open"]
        # Next candle close price (exit price)
        next_close = next_candle["close"]

        # Check if we should enter a position
        if position == 0 and confidence > confidence_threshold and direction != "HOLD":
            # Enter position
            if direction == "UP":
                position = 1  # Long
            else:  # DOWN
                position = -1  # Short

            entry_price = next_open
            entry_timestamp = next_candle["timestamp"]

            print(f"ENTRY: {direction} at {entry_price:.2f} (confidence: {confidence:.2f}) at {pd.to_datetime(entry_timestamp, unit='ms')}")

        # Check if we should exit position (at next candle close)
        elif position != 0:
            # Calculate P&L for binary market payoff
            # +1 if direction correct, -1 if wrong
            price_moved_up = next_close > entry_price
            direction_correct = (position == 1 and price_moved_up) or (position == -1 and not price_moved_up)
            pnl_points = 1.0 if direction_correct else -1.0

            # Update bankroll
            bankroll += pnl_points

            # Record trade
            trade = {
                "entry_timestamp": entry_timestamp,
                "exit_timestamp": next_candle["timestamp"],
                "entry_price": entry_price,
                "exit_price": next_close,
                "direction": "LONG" if position == 1 else "SHORT",
                "signal_direction": direction,
                "confidence": confidence,
                "pnl_points": pnl_points,
                "bankroll": bankroll
            }
            trades.append(trade)

            # Update equity curve
            equity_curve.append(bankroll)

            print(f"EXIT: {trade['direction']} at {next_close:.2f} | P&L: {pnl_points:+.1f} | Bankroll: ${bankroll:.2f}")

            # Reset position
            position = 0
            entry_price = 0.0
            entry_timestamp = 0

    # Calculate statistics
    if not trades:
        print("No trades executed!")
        return

    total_trades = len(trades)
    winning_trades = [t for t in trades if t["pnl_points"] > 0]
    win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
    total_pnl = sum(t["pnl_points"] for t in trades)

    # Calculate max drawdown
    peak = initial_bankroll
    max_drawdown = 0.0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak * 100
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    # Calculate Sharpe ratio (simplified, assuming daily returns)
    if len(equity_curve) > 1:
        returns = np.diff(equity_curve) / equity_curve[:-1]
        sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
    else:
        sharpe_ratio = 0

    # Print results
    print("-" * 50)
    print("BACKTEST RESULTS")
    print("-" * 50)
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.2%}")
    print(f"Total P&L Points: {total_pnl:+.1f}")
    print(f"Final Bankroll: ${bankroll:.2f}")
    print(f"Max Drawdown: {max_drawdown:.2f}%")
    print(f"Sharpe Ratio: {sharpe_ratio:.2f}")

    # Save trades to CSV
    os.makedirs("results", exist_ok=True)
    results_file = "results/backtest_results.csv"
    if trades:
        fieldnames = trades[0].keys()
        with open(results_file, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(trades)
        print(f"Detailed trade log saved to {results_file}")

    return {
        "total_trades": total_trades,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "final_bankroll": bankroll,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "trades": trades
    }

def main():
    parser = argparse.ArgumentParser(description='Backtest BTC/USDT 5m trading strategy')
    parser.add_argument('--start-date', type=str, required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, required=True, help='End date (YYYY-MM-DD)')
    parser.add_argument('--confidence', type=float, default=0.65, help='Confidence threshold (default: 0.65)')
    parser.add_argument('--bankroll', type=float, default=1000.0, help='Initial bankroll (default: 1000.0)')

    args = parser.parse_args()

    # Validate dates
    try:
        pd.Timestamp(args.start_date)
        pd.Timestamp(args.end_date)
    except Exception as e:
        print(f"Error parsing dates: {e}")
        return

    if args.start_date >= args.end_date:
        print("Error: start-date must be before end-date")
        return

    # Run backtest
    symbol = "BTCUSDT"
    interval = "5m"

    print(f"Backtesting {symbol} {interval} from {args.start_date} to {args.end_date}")
    print(f"Confidence threshold: {args.confidence}")
    print(f"Initial bankroll: ${args.bankroll:.2f}")
    print("=" * 60)

    # Load or fetch data
    candles = load_or_fetch_data(symbol, interval, args.start_date, args.end_date)

    if not candles:
        print("Error: No data available for backtesting")
        return

    # Run backtest
    results = backtest(candles, args.confidence, args.bankroll)

    print("=" * 60)
    print("Backtest completed!")

if __name__ == "__main__":
    main()