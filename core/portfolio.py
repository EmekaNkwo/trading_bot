import MetaTrader5 as mt5


def calculate_lot_size(
    symbol: str,
    balance: float,
    risk_percent: float,
    entry_price: float,
    stop_loss: float,
):
    """
    Professional position sizing using MT5 contract specs.
    """

    symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        raise RuntimeError(f"Symbol not found: {symbol}")

    tick_size = symbol_info.trade_tick_size
    tick_value = symbol_info.trade_tick_value

    sl_distance = abs(entry_price - stop_loss)

    if sl_distance == 0:
        return 0

    risk_amount = balance * risk_percent

    cost_per_lot = (sl_distance / tick_size) * tick_value

    lot = risk_amount / cost_per_lot

    lot = round(lot, 2)

    return max(lot, symbol_info.volume_min)
