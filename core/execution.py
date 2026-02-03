import MetaTrader5 as mt5
from utils.logger import setup_logger
from utils.telegram import TelegramNotifier
from config.telegram import TOKEN, CHAT_ID

from utils.trade_reporter import LiveTradeReporter


class MT5Executor:

    def __init__(self, symbol):
        self.symbol = symbol
        self.logger = setup_logger()
        self.reporter = LiveTradeReporter()
        self.notifier = TelegramNotifier(TOKEN, CHAT_ID)

        # portfolio-aware state
        self.current_lot = 0.01        # default fallback
        self.last_trade_pnl = None     # updated on execution

    # -------------------------------------------------
    # PORTFOLIO RISK OVERRIDE
    # -------------------------------------------------

    def override_risk(self, risk_pct):
        """
        risk_pct: fraction of account balance (e.g. 0.01 = 1%)
        Converts risk percentage into a tradable lot size.
        """
        self.current_lot = self._risk_to_lot(risk_pct)

    def _risk_to_lot(self, risk_pct):
        """
        Conservative risk-to-lot conversion.
        Keeps system safe if symbol metadata is imperfect.
        """

        account = mt5.account_info()
        if not account:
            return self.current_lot

        balance = account.balance
        risk_amount = balance * risk_pct

        info = mt5.symbol_info(self.symbol)
        if not info:
            return self.current_lot

        # broker constraints
        min_lot = info.volume_min
        step = info.volume_step

        # fallback assumptions (safe defaults)
        tick_value = info.trade_tick_value or 1.0
        estimated_sl_ticks = 100  # conservative estimate

        lot = risk_amount / (estimated_sl_ticks * tick_value)
        lot = max(min_lot, lot)

        # normalize to broker step
        lot = round(lot / step) * step

        return lot

    # -------------------------------------------------
    # EXECUTION
    # -------------------------------------------------

    def place_market_order(self, signal, lot=None):

        lot = lot if lot is not None else self.current_lot

        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            self.logger.error("NO TICK DATA")
            return None

        if signal["side"] == "buy":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "deviation": 20,
            "magic": 2601,
            "comment": "portfolio-bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        # ❌ FAILED ORDER
        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:

            self.last_trade_pnl = None

            retcode = result.retcode if result else "NO_RESULT"
            comment = result.comment if result else "NO_RESPONSE"

            self.logger.error(
                f"ORDER FAILED | {retcode} | {comment}"
            )

            self.notifier.send(
                f"ORDER FAILED\n"
                f"{self.symbol} {signal['side'].upper()}\n"
                f"Reason: {comment}"
            )

            self.reporter.record(
                symbol=self.symbol,
                side=signal["side"],
                lot=lot,
                price=price,
                sl=signal["sl"],
                tp=signal["tp"],
                ticket=None,
                retcode=retcode,
                comment=comment
            )

            return None

        # ✅ SUCCESSFUL ORDER
        self.last_trade_pnl = 0  # updated later on close

        self.logger.info(
            f"TRADE EXECUTED | "
            f"{self.symbol} | {signal['side'].upper()} | "
            f"LOT={lot} | PRICE={price} | "
            f"SL={signal['sl']} | TP={signal['tp']} | "
            f"TICKET={result.order}"
        )

        self.notifier.send(
            f"TRADE EXECUTED\n"
            f"{self.symbol} {signal['side'].upper()}\n"
            f"Lot: {lot}\n"
            f"Price: {price}\n"
            f"SL: {signal['sl']}\n"
            f"TP: {signal['tp']}\n"
            f"Ticket: {result.order}"
        )

        self.reporter.record(
            symbol=self.symbol,
            side=signal["side"],
            lot=lot,
            price=price,
            sl=signal["sl"],
            tp=signal["tp"],
            ticket=result.order,
            retcode=result.retcode,
            comment=result.comment
        )

        return result
