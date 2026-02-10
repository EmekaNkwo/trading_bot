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

        # Use real tick value for XAUUSDm (typically $0.01 per tick)
        tick_value = info.trade_tick_value or 0.01
        
        # More realistic SL estimate for scalping (50 ticks instead of 100)
        # XAUUSDm typically moves 20-50 ticks during normal scalping conditions
        estimated_sl_ticks = 50

        lot = risk_amount / (estimated_sl_ticks * tick_value)
        lot = max(min_lot, lot)

        # normalize to broker step
        lot = round(lot / step) * step

        # Safety cap for small accounts
        max_lot_for_small_account = 0.1  # Maximum 0.1 lots for accounts under $2000
        if balance < 2000:
            lot = min(lot, max_lot_for_small_account)

        # Log calculation details for verification
        self.logger.info(
            f"LOT CALC | Balance: ${balance:.2f} | Risk: {risk_pct*100:.1f}% | "
            f"Risk Amount: ${risk_amount:.2f} | Tick Value: ${tick_value:.4f} | "
            f"SL Ticks: {estimated_sl_ticks} | Calculated Lot: {lot:.3f}"
        )

        return lot

    def _validate_lot_size(self, lot):
        """Validate lot size against account constraints"""
        account = mt5.account_info()
        if not account:
            return {"valid": False, "reason": "No account info"}
        
        symbol_info = mt5.symbol_info(self.symbol)
        if not symbol_info:
            return {"valid": False, "reason": "No symbol info"}
        
        # Check broker constraints
        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        step_lot = symbol_info.volume_step
        
        if lot < min_lot:
            return {"valid": False, "reason": f"Lot {lot} below minimum {min_lot}"}
        
        if lot > max_lot:
            return {"valid": False, "reason": f"Lot {lot} above maximum {max_lot}"}
        
        # Check margin requirements
        margin_required = mt5.order_calc_margin(
            symbol=self.symbol,
            volume=lot,
            type=mt5.ORDER_TYPE_BUY,
            price=mt5.symbol_info_tick(self.symbol).ask
        )
        
        if margin_required is None:
            return {"valid": False, "reason": "Cannot calculate margin"}
        
        if margin_required > account.margin_free:
            return {"valid": False, "reason": f"Insufficient margin: need ${margin_required:.2f}, have ${account.margin_free:.2f}"}
        
        # Check position size limits (max 2% of account per trade)
        max_risk_per_trade = account.balance * 0.02
        position_value = lot * 100000  # XAUUSDm: 1 lot = 100,000 units
        
        if position_value > max_risk_per_trade * 10:  # 10x leverage consideration
            return {"valid": False, "reason": f"Position too large: ${position_value:.2f} > ${max_risk_per_trade * 10:.2f}"}
        
        return {"valid": True, "reason": "OK"}

    def _check_account_protection(self):
        """Check overall account protection levels"""
        account = mt5.account_info()
        if not account:
            return
        
        # Daily loss tracking
        if hasattr(self, '_daily_start_balance'):
            daily_pnl = account.balance - self._daily_start_balance
            max_daily_loss = account.balance * 0.05  # 5% daily loss limit
            
            if daily_pnl < -max_daily_loss:
                self.logger.error(f"DAILY LOSS LIMIT HIT | Loss: ${daily_pnl:.2f} | Limit: ${max_daily_loss:.2f}")
                return False
        else:
            self._daily_start_balance = account.balance
        
        # Equity protection
        equity_ratio = account.equity / account.balance if account.balance > 0 else 1
        if equity_ratio < 0.9:  # 10% equity drawdown
            self.logger.error(f"EQUITY PROTECTION | Equity ratio: {equity_ratio:.2f}")
            return False
        
        return True

    # -------------------------------------------------
    # EXECUTION
    # -------------------------------------------------

    def place_market_order(self, signal, lot=None):

        lot = lot if lot is not None else self.current_lot

        # Validate lot size before execution
        validation_result = self._validate_lot_size(lot)
        if not validation_result["valid"]:
            self.logger.error(f"LOT VALIDATION FAILED | {validation_result['reason']}")
            return None

        # Check account protection levels
        if not self._check_account_protection():
            self.logger.error("ACCOUNT PROTECTION BLOCKED | Trade execution prevented")
            return None

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
