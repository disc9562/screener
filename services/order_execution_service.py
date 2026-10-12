import logging
import math
from binance import Client

logger = logging.getLogger(__name__)


class OrderExecutionService:
    """Executes orders on Binance Futures Testnet.

    All methods return the API response dict on success, or None on failure.
    Failures are logged but never raised, so they don't break CSV tracking.
    """

    def __init__(self, api_key: str, api_secret: str):
        self.client = Client(api_key, api_secret, testnet=True)
        self._symbol_info_cache = {}
        logger.info("Testnet order execution ENABLED")
        self._log_account_balance()

    def _log_account_balance(self):
        """Log testnet account balance at startup."""
        try:
            account = self.client.futures_account_balance()
            for asset in account:
                if float(asset.get('balance', 0)) > 0:
                    logger.info(f"Testnet balance: {asset['asset']} = {asset['balance']}")
        except Exception as e:
            logger.error(f"Failed to fetch testnet account balance: {e}")

    def get_symbol_info(self, symbol: str) -> dict | None:
        """Get symbol precision info, cached after first call."""
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]

        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    filters = {f['filterType']: f for f in s.get('filters', [])}
                    lot_size = filters.get('LOT_SIZE')
                    price_filter = filters.get('PRICE_FILTER')

                    result = {
                        'quantityPrecision': s.get('quantityPrecision', 3),
                        'pricePrecision': s.get('pricePrecision', 2),
                        'stepSize': float(lot_size['stepSize']) if lot_size else None,
                        'tickSize': float(price_filter['tickSize']) if price_filter else None,
                    }
                    self._symbol_info_cache[symbol] = result
                    return result
            logger.warning(f"Symbol {symbol} not found in futures exchange info")
            return None
        except Exception as e:
            logger.error(f"Failed to get symbol info for {symbol}: {e}")
            return None

    @staticmethod
    def _precision_from_size(size: float) -> int:
        """Derive decimal precision from a step/tick size (e.g. 0.001 -> 3)."""
        return int(round(-math.log10(size)))

    def _round_quantity(self, symbol: str, quantity: float) -> float:
        """Round quantity down to match symbol's step size."""
        info = self.get_symbol_info(symbol)
        if info and info.get('stepSize'):
            precision = self._precision_from_size(info['stepSize'])
            return round(math.floor(quantity * 10**precision) / 10**precision, precision)
        if info:
            return round(quantity, info['quantityPrecision'])
        return round(quantity, 3)

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to match symbol's tick size."""
        info = self.get_symbol_info(symbol)
        if info and info.get('tickSize'):
            return round(price, self._precision_from_size(info['tickSize']))
        if info:
            return round(price, info['pricePrecision'])
        return round(price, 2)

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict | None:
        """Place a market order on futures testnet."""
        qty = self._round_quantity(symbol, quantity)
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=qty,
            )
            logger.info(f"Market {side} order placed: {symbol} qty={qty} orderId={order.get('orderId')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place market {side} order for {symbol}: {e}")
            return None

    def _place_conditional_order(self, symbol: str, side: str, quantity: float,
                                 stop_price: float, order_type: str) -> dict | None:
        """Place a STOP_MARKET or TAKE_PROFIT_MARKET order."""
        qty = self._round_quantity(symbol, quantity)
        price = self._round_price(symbol, stop_price)
        label = "Stop loss" if order_type == 'STOP_MARKET' else "Take profit"
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=order_type,
                quantity=qty,
                stopPrice=price,
                closePosition='false',
            )
            logger.info(f"{label} order placed: {symbol} side={side} stopPrice={price} orderId={order.get('orderId')}")
            return order
        except Exception as e:
            logger.error(f"Failed to place {label.lower()} order for {symbol}: {e}")
            return None

    def place_stop_loss_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict | None:
        """Place a STOP_MARKET order (stop loss)."""
        return self._place_conditional_order(symbol, side, quantity, stop_price, 'STOP_MARKET')

    def place_take_profit_order(self, symbol: str, side: str, quantity: float, stop_price: float) -> dict | None:
        """Place a TAKE_PROFIT_MARKET order."""
        return self._place_conditional_order(symbol, side, quantity, stop_price, 'TAKE_PROFIT_MARKET')

    def cancel_open_orders(self, symbol: str) -> dict | None:
        """Cancel all open orders for a symbol."""
        try:
            result = self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"Cancelled all open orders for {symbol}: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel open orders for {symbol}: {e}")
            return None

    def close_position(self, symbol: str, quantity: float) -> dict | None:
        """Close a position: cancel pending orders then market sell."""
        self.cancel_open_orders(symbol)
        return self.place_market_order(symbol, 'SELL', quantity)

    def get_account_balance(self) -> list | None:
        """Get futures account balance."""
        try:
            return self.client.futures_account_balance()
        except Exception as e:
            logger.error(f"Failed to get account balance: {e}")
            return None
