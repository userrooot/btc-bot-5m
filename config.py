import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Polymarket credentials
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE")

# Validate required keys
if not POLYMARKET_PRIVATE_KEY:
    raise ValueError("POLYMARKET_PRIVATE_KEY is required in .env file")
if not POLYMARKET_API_KEY:
    raise ValueError("POLYMARKET_API_KEY is required in .env file")

# Polymarket host
POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

# Binance URLs
BINANCE_WS_URL = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@kline_5m")
BINANCE_REST_URL = os.getenv("BINANCE_REST_URL", "https://api.binance.com/api/v3/klines")

# Trading parameters
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.65"))
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.03"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.10"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.05"))
TRADE_SIZE_USDC = float(os.getenv("TRADE_SIZE_USDC", "10.0"))
PAPER_TRADING = os.getenv("PAPER_TRADING", "True").lower() == "true"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")