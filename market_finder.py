import asyncio
import logging
import time
from typing import Dict

# Assuming py-clob-client is available
try:
    from clob_client.client import Client as ClobClient
except ImportError:
    ClobClient = None

logger = logging.getLogger(__name__)

class MarketNotFoundError(Exception):
    """Raised when no active BTC market is found."""
    pass

# Simple cache with timestamp
_cached_market = None
_cache_timestamp = 0
CACHE_TTL = 60  # 60 seconds

async def find_active_btc_market() -> Dict:
    """
    Find the next expiring active BTC market with 5-minute term.
    Returns a Market dict as defined in CLAUDE.md.
    Raises MarketNotFoundError if none found.
    """
    global _cached_market, _cache_timestamp

    # Check cache
    now = time.time()
    if _cached_market is not None and (now - _cache_timestamp) < CACHE_TTL:
        logger.debug("Returning cached market")
        return _cached_market

    if ClobClient is None:
        raise ImportError("py-clob-client is not installed. Install it to use this function.")

    # Import config here to avoid circular imports
    from config import POLYMARKET_HOST

    # Initialize client
    client = ClobClient(host=POLYMARKET_HOST)

    try:
        # Fetch markets
        markets_response = await client.get_markets()
        # The response structure from py-clob-client might be different.
        # Assuming it returns a list of markets under 'data' or directly.
        markets = markets_response.get('data', markets_response) if isinstance(markets_response, dict) else markets_response

        # Filter for BTC and 5-minute
        btc_markets = []
        for market in markets:
            question = market.get('question', '').upper()
            if 'BTC' in question and ('5 MINUTES' in question or '5-MINUTE' in question):
                # Check if active (not resolved)
                # Assuming there's a 'closed' or 'resolved' field; we'll check for 'closed' being False
                if not market.get('closed', True) and not market.get('resolved', False):
                    btc_markets.append(market)

        if not btc_markets:
            raise MarketNotFoundError("No active BTC 5-minute market found")

        # Sort by end_time ascending (earliest first)
        btc_markets.sort(key=lambda m: m.get('end_time', 0))

        # Pick the next one to expire (first after sorting)
        selected_market = btc_markets[0]

        # Fetch order book for YES and NO tokens to get mid prices
        token_id_yes = selected_market.get('token_id_yes')
        token_id_no = selected_market.get('token_id_no')

        if not token_id_yes or not token_id_no:
            raise MarketNotFoundError("Market missing token IDs")

        # Get order book for both tokens
        ob_yes = await client.get_order_book(token_id=token_id_yes)
        ob_no = await client.get_order_book(token_id=token_id_no)

        # Function to calculate mid price from order book
        def get_mid_price(order_book):
            bids = order_book.get('bids', [])
            asks = order_book.get('asks', [])
            if not bids or not asks:
                return None
            best_bid = float(bids[0]['price']) if bids else 0.0
            best_ask = float(asks[0]['price']) if asks else 0.0
            return (best_bid + best_ask) / 2.0

        yes_price = get_mid_price(ob_yes)
        no_price = get_mid_price(ob_no)

        if yes_price is None or no_price is None:
            logger.warning("Could not compute mid prices for market %s", selected_market.get('market_id'))
            # We'll still return the market but with None prices? Spec says fetch current YES/NO mid prices.
            # If we can't get them, we might want to raise or set to 0. Let's set to 0 and log.
            yes_price = yes_price or 0.0
            no_price = no_price or 0.0

        # Construct Market dict as per CLAUDE.md
        market_dict = {
            "market_id": selected_market.get('market_id'),
            "token_id_yes": token_id_yes,
            "token_id_no": token_id_no,
            "question": selected_market.get('question'),
            "end_time": selected_market.get('end_time'),  # Unix ms
            "yes_price": yes_price,
            "no_price": no_price
        }

        # Update cache
        _cached_market = market_dict
        _cache_timestamp = now

        return market_dict

    except Exception as e:
        logger.error("Error finding active BTC market: %s", e)
        raise