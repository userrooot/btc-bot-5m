# BTC Polymarket Bot

Automated trading bot that predicts Bitcoin price movements and places trades on Polymarket prediction markets.

## Overview

This project implements an automated trading system that watches Bitcoin/USDT 5-minute candles from Binance, computes technical indicators, and uses an XGBoost machine learning model to predict whether Bitcoin will go up or down in the next 5-minute period. The bot then places corresponding YES/NO orders on Polymarket's BTC "Up or Down" 5-minute prediction markets.

The system is designed with paper trading as the default mode for safe testing, with configurable risk management, trade logging to SQLite, and Telegram notifications for important events. All trading decisions are based on rigorous technical analysis and machine learning predictions, with explicit confidence thresholds to filter low-quality signals.

## Architecture Diagram

```
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   Binance WS    │───►│   Price Feed     │───►│   Feature Queue  │
└─────────────────┘    └──────────────────┘    └──────────────────┘
                                   │
                                   ▼
                           ┌──────────────────┐
                           │   Predictor      │◄─┐
                           └──────────────────┘  │
                                   │              │
                                   ▼              │
                           ┌──────────────────┐  │
                           │   Signal Queue   │──┘
                           └──────────────────┘
                                   │
                                   ▼
                           ┌──────────────────┐
                           │   Order Manager  │
                           └──────────────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │   Market Finder  │  │   Risk Manager   │  │      DB          │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
                                   │
                                   ▼
                           ┌──────────────────┐
                           │ Telegram Alerts  │
                           └──────────────────┘
```

## Quick Start

### 1. Clone & Install

```bash
git clone <repository-url>
cd btc_bot
pip install -r requirements.txt
```

### 2. Configure .env

Copy the example environment file and fill in your configuration:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:
- Polymarket API credentials (for live trading)
- Binance WebSocket and REST URLs (usually pre-configured)
- Confidence threshold (default 0.65)
- Risk management parameters
- Telegram bot token and chat ID (for notifications)
- Set PAPER_TRADING=true for safe testing

### 3. Train the Model

Before running the bot, you need to train the machine learning model:

```bash
python train_model.py
```

This will:
- Fetch 90 days of BTC/USDT 5m OHLCV data from Binance
- Compute technical indicators (RSI, MACD, Bollinger Bands, EMAs, etc.)
- Label each candle based on the next candle's close price
- Train an XGBoost binary classifier with time-based 80/20 split
- Print classification report, confusion matrix, and feature importances
- Save the model to `model/btc_5m_model.json`
- Save feature importance plot to `model/feature_importance.png`
- Save training metrics to `model/training_report.txt`

### 4. Backtest

Test your strategy on historical data:

```bash
python backtest.py --start-date 2024-01-01 --end-date 2024-01-31 --confidence 0.65 --bankroll 1000
```

This will:
- Load Binance 5m data for the specified date range
- For each closed candle, compute features and run the predictor
- Simulate trades when confidence exceeds threshold and signal is not HOLD
- Track performance metrics including win rate, total P&L, max drawdown, and Sharpe ratio
- Print a formatted results table
- Save detailed trade logs to `results/backtest_results.csv`

### 5. Paper Trading (default)

Run the bot in paper trading mode (no real money at risk):

```bash
python bot.py
```

The bot will:
- Connect to Binance WebSocket for real-time price data
- Compute features and generate predictions
- Log all signals and trades to SQLite database
- Send Telegram notifications (if configured)
- Display live trading statistics in the console
- All trades are simulated - no actual orders are placed

### 6. Live Trading ⚠️ (with strong warnings)

⚠️ **WARNING: Live trading involves real financial risk. Only enable after thorough backtesting and paper trading.**

To enable live trading:
1. Set `PAPER_TRADING=false` in your `.env` file
2. Ensure you have funded your Polymarket account
3. Start with small position sizes
4. Monitor closely, especially during volatile market conditions

```bash
python bot.py
```

## Module Reference

- **bot.py**: Main orchestrator that connects all modules and runs the trading loop
- **config.py**: Central configuration management loading from environment variables
- **price_feed.py**: Handles WebSocket connection to Binance, computes technical indicators
- **predictor.py**: Loads trained XGBoost model and makes UP/DOWN/HOLD predictions
- **order_manager.py**: Handles placing, checking, and canceling orders on Polymarket CLOB
- **risk_manager.py**: Implements position sizing, daily loss limits, and risk controls
- **market_finder.py**: Automatically finds the correct Polymarket markets for BTC 5m predictions
- **telegram_alerts.py**: Sends notifications for trades, errors, and system status
- **db.py**: SQLite database for persistent storage of trades, signals, and system state
- **train_model.py**: Standalone script for training the machine learning model
- **backtest.py**: Standalone script for historical strategy backtesting

## Risk Management

The bot implements multiple layers of risk protection:

1. **Position Sizing**: Configurable maximum risk per trade as percentage of bankroll
2. **Daily Loss Limits**: Trading halts if daily loss exceeds configured percentage
3. **Signal Confidence Threshold**: Only trades when prediction confidence exceeds threshold (default 0.65)
4. **Maximum Spread Protection**: Refuses to trade if bid-ask spread exceeds configured percentage
5. **Automatic Market Finder**: Dynamically finds active Polymarket markets to avoid expired or illiquid ones
6. **Paper Trading Default**: System defaults to paper trading mode to prevent accidental live trading

All risk parameters are configurable via environment variables in the `.env` file.

## ⚠️ Legal Notice

**Important Regulatory Disclaimer for India-based Users:**

The legal status of prediction markets like Polymarket in India is currently unclear and subject to evolving regulations. Participation in such platforms may involve regulatory risks depending on your jurisdiction and applicable laws.

Before engaging in live trading on Polymarket or any similar platform:
- Consult with a qualified legal professional familiar with Indian financial regulations
- Review the latest guidelines from regulatory bodies such as SEBI, RBI, and FEMA
- Ensure compliance with all relevant foreign exchange and securities laws
- Consider the tax implications of trading profits under Indian tax law

This software is provided for educational and informational purposes only. The developers assume no liability for any financial losses, legal consequences, or regulatory issues arising from its use. Trade at your own risk.

## Troubleshooting

### Common Issues

**1. Model File Not Found**
- Ensure you've run `train_model.py` successfully
- Check that `model/btc_5m_model.json` exists
- Verify file permissions allow reading

**2. WebSocket Connection Errors**
- Check your internet connection
- Verify Binance API accessibility in your region
- Increase reconnect delay in price_feed.py if needed

**3. Missing Technical Indicators**
- Ensure you have sufficient historical data (at least 200 candles)
- Check that pandas-ta is properly installed
- Verify column names match between price_feed.py and predictor.py

**4. Empty Trade Logs in Backtest**
- Adjust confidence threshold (try lowering it)
- Check date range has sufficient volatility
- Verify data fetching is working correctly

**5. Telegram Notifications Not Sending**
- Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
- Check that your bot can send messages to the specified chat
- Ensure you've started a chat with your bot

### Logs and Debugging

- All modules use structured logging with `logging.getLogger(__name__)`
- Adjust LOG_LEVEL in .env to DEBUG for detailed tracing
- Check console output for real-time status updates
- Review SQLite database for persistent trade records
- Examine results/ and model/ directories for output files

For persistent issues, consider:
- Running with `--help` flags on scripts to verify usage
- Checking requirements.txt matches installed package versions
- Verifying Python version compatibility (3.8+ recommended)