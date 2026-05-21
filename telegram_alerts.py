import logging
from typing import List, Dict, Any
import asyncio
import os

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    Bot = None
    TelegramError = Exception

logger = logging.getLogger(__name__)

class TelegramAlerter:
    def __init__(self):
        self.bot = None
        self.chat_id = None
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if token and chat_id and Bot is not None:
            try:
                self.bot = Bot(token=token)
                self.chat_id = chat_id
                logger.info("TelegramAlerter initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
                self.bot = None
        else:
            logger.warning("Telegram credentials not set or telegram package not available. Alerts will be disabled.")

    async def send(self, message: str) -> None:
        if not self.bot or not self.chat_id:
            logger.debug(f"Telegram not configured. Would send: {message}")
            return
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=message)
            logger.debug(f"Telegram message sent: {message[:50]}...")
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")

    async def trade_filled(self, direction: str, size: float, price: float, pnl: float) -> None:
        direction_emoji = "📈" if direction == "UP" else "📉"
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        pnl_emoji = "💰" if pnl >= 0 else "💸"
        message = (
            f"{direction_emoji} Trade Filled {direction_emoji}\n"
            f"Direction: {direction}\n"
            f"Size: {size:.2f} USDC\n"
            f"Price: {price:.4f}\n"
            f"PnL: {pnl_str} USDC {pnl_emoji}"
        )
        await self.send(message)

    async def daily_summary(self, trades: List[Dict[str, Any]], pnl: float) -> None:
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        pnl_emoji = "💰" if pnl >= 0 else "💸"
        message = (
            f"📊 Daily Summary 📊\n"
            f"Total Trades: {len(trades)}\n"
            f"Total PnL: {pnl_str} USDC {pnl_emoji}\n"
        )
        if trades:
            wins = sum(1 for t in trades if t.get('pnl_usdc', 0) > 0)
            losses = len(trades) - wins
            win_rate = (wins / len(trades)) * 100 if trades else 0
            message += f"Win Rate: {win_rate:.1f}% ({wins}W/{losses}L)\n"
        await self.send(message)

    async def error_alert(self, module: str, error: Exception) -> None:
        message = (
            f"🚨 Bot Error 🚨\n"
            f"Module: {module}\n"
            f"Error: {type(error).__name__}: {str(error)}\n"
        )
        await self.send(message)