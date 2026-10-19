import logging
import math
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
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
        self._cross_margin_done = set()
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
            for s in info.get('symbols', []):
                filters = {f['filterType']: f for f in s.get('filters', [])}
                lot_size = filters.get('LOT_SIZE')
                price_filter = filters.get('PRICE_FILTER')
                self._symbol_info_cache[s['symbol']] = {
                    'quantityPrecision': s.get('quantityPrecision', 3),
                    'pricePrecision': s.get('pricePrecision', 2),
                    'stepSize': float(lot_size['stepSize']) if lot_size else None,
                    'tickSize': float(price_filter['tickSize']) if price_filter else None,
                }
            if symbol in self._symbol_info_cache:
                return self._symbol_info_cache[symbol]
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
            step = Decimal(str(info['stepSize']))
            if step > 0:
                q = (Decimal(str(quantity)) // step) * step
                return float(q)
        if info:
            return round(quantity, info['quantityPrecision'])
        return round(quantity, 3)

    def _round_price(self, symbol: str, price: float) -> float:
        """Round price to match symbol's tick size."""
        info = self.get_symbol_info(symbol)
        if info and info.get('tickSize'):
            tick = Decimal(str(info['tickSize']))
            if tick > 0:
                p = (Decimal(str(price)) / tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick
                return float(p)
        if info:
            return round(price, info['pricePrecision'])
        return round(price, 2)

    def place_market_order(self, symbol: str, side: str, quantity: float, reduce_only: bool = False) -> dict | None:
        """Place a market order on futures testnet. reduce_only=True can never open/flip a position."""
        qty = self._round_quantity(symbol, quantity)
        if qty <= 0:
            logger.error(f"Cannot place market {side} order for {symbol}: rounded quantity is {qty} (raw={quantity})")
            return None
        params = dict(symbol=symbol, side=side, type='MARKET', quantity=qty,
                      newOrderRespType='RESULT')      # 回應帶實際成交均價 avgPrice 與成交量
        if reduce_only:
            params["reduceOnly"] = "true"
        try:
            order = self.client.futures_create_order(**params)
            logger.info(f"Market {side} order placed: {symbol} qty={qty} reduceOnly={reduce_only} orderId={order.get('orderId')}")
            return order
        except Exception as e:
            if reduce_only and getattr(e, 'code', None) == -2022:
                logger.info(f"ReduceOnly {side} order for {symbol} rejected (-2022): position already closed on exchange")
                return {"status": "ALREADY_CLOSED", "symbol": symbol}
            logger.error(f"Failed to place market {side} order for {symbol}: {e}")
            return None

    def _place_conditional_order(self, symbol: str, side: str, quantity: float,
                                 stop_price: float, order_type: str) -> dict | None:
        """Place a STOP_MARKET or TAKE_PROFIT_MARKET order."""
        qty = self._round_quantity(symbol, quantity)
        if qty <= 0:
            logger.error(f"Cannot place {order_type} order for {symbol}: rounded quantity is {qty} (raw={quantity})")
            return None
        price = self._round_price(symbol, stop_price)
        label = "Stop loss" if order_type == 'STOP_MARKET' else "Take profit"
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=order_type,
                quantity=qty,
                stopPrice=price,
                reduceOnly='true',
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
        """Cancel all open orders for a symbol, including conditional (algo) orders:
        python-binance routes STOP_MARKET / TAKE_PROFIT_MARKET to the algo endpoint,
        which allOpenOrders does not touch."""
        result = None
        for extra in ({}, {"conditional": True}):
            try:
                result = self.client.futures_cancel_all_open_orders(symbol=symbol, **extra)
                logger.info(f"Cancelled open {'conditional ' if extra else ''}orders for {symbol}: {result}")
            except Exception as e:
                logger.error(f"Failed to cancel open {'conditional ' if extra else ''}orders for {symbol}: {e}")
        return result

    def close_position(self, symbol: str, quantity: float, attempts: int = 3) -> dict | None:
        """Close a position: reduce-only market sell first (safe to retry: it can never open a short),
        then cancel the remaining orders. If every sell fails, the disaster stop is left in place."""
        for _ in range(attempts):
            order = self.place_market_order(symbol, 'SELL', quantity, reduce_only=True)
            if order:
                self.cancel_open_orders(symbol)
                return order
        logger.error(f"Close failed for {symbol} after {attempts} attempts; stop order left in place")
        return None

    def is_one_way_mode(self) -> bool:
        """reduceOnly orders only work in one-way position mode (dualSidePosition = false)."""
        try:
            return not bool(self.client.futures_get_position_mode().get('dualSidePosition'))
        except Exception as e:
            logger.error(f"Failed to read position mode: {e}")
            return False

    def ensure_cross_margin(self, symbol: str) -> bool:
        """Set CROSSED margin once per symbol (-4046 = already crossed). Isolated margin with the
        default leverage would liquidate long before the −20% disaster stop."""
        if symbol in self._cross_margin_done:
            return True
        try:
            self.client.futures_change_margin_type(symbol=symbol, marginType='CROSSED')
        except Exception as e:
            if getattr(e, 'code', None) != -4046:
                logger.error(f"Failed to set CROSSED margin for {symbol}: {e}")
                return False
        self._cross_margin_done.add(symbol)
        return True

    def get_account_balance(self) -> list | None:
        """Get futures account balance."""
        try:
            return self.client.futures_account_balance()
        except Exception as e:
            logger.error(f"Failed to get account balance: {e}")
            return None
