import MetaTrader5 as mt5
import os
import time
from utils.logger import setup_logger, log_separator
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials

from utils.trade_reporter import LiveTradeReporter
from utils.indicators import atr
import pandas as pd
from typing import Any


def _strategy_code(strategy: str) -> str:
    return {
        "xau_trend": "xt",
        "xau_scalper": "xs",
        "xau_regime": "xr",
        "xau_sweep": "xw",
        "xau_liquidity_reclaim": "xl",
        "xau_opening_range_displacement": "xo",
    }.get(strategy, "uk")


def _build_order_comment(strategy: str, risk_pct: float | None) -> str:
    # MT5 order comments have small length limits; keep this compact.
    # Format: pb|<strat>|r=<risk>
    if risk_pct is None:
        return f"pb|{_strategy_code(strategy)}"

    risk_str = f"{float(risk_pct):.4f}"
    return f"pb|{_strategy_code(strategy)}|r={risk_str}"

def _tf(tf: str):
    return {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
    }.get(tf, mt5.TIMEFRAME_M5)


def _parse_strategy_from_comment(comment: str | None) -> str:
    if not comment:
        return "unknown"
    c = str(comment)
    if not c.startswith("pb|"):
        return "unknown"
    parts = c.split("|")
    if len(parts) < 2:
        return "unknown"
    code = parts[1]
    return {
        "xt": "xau_trend",
        "xs": "xau_scalper",
        "xr": "xau_regime",
        "xw": "xau_sweep",
        "xl": "xau_liquidity_reclaim",
        "xo": "xau_opening_range_displacement",
    }.get(code, "unknown")


def _retcode_name(retcode: Any) -> str:
    if not isinstance(retcode, int):
        return str(retcode)
    m = getattr(_retcode_name, "_map", None)
    if m is None:
        m = {}
        for n in dir(mt5):
            if not n.startswith("TRADE_RETCODE_"):
                continue
            try:
                v = getattr(mt5, n)
            except Exception:
                continue
            if isinstance(v, int):
                m[v] = n
        setattr(_retcode_name, "_map", m)
    return m.get(retcode, str(retcode))


def _human_failure_reason(
    *,
    symbol: str,
    side: str,
    lot: float | None,
    price: float | None,
    sl: float | None,
    tp: float | None,
    deviation_points: int | None,
    retcode: Any,
    comment: str | None,
) -> str:
    """
    Produces a concise, actionable explanation for MT5 order failures.
    """
    lines: list[str] = []

    rname = _retcode_name(retcode)
    lines.append(f"retcode_name={rname}")

    try:
        le = mt5.last_error()
        if le:
            lines.append(f"mt5_last_error={le}")
    except Exception:
        pass

    info = None
    tick = None
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
    except Exception:
        info = None
        tick = None

    if info is not None:
        try:
            point = float(getattr(info, "point", 0.0) or 0.0)
        except Exception:
            point = 0.0
        try:
            tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
        except Exception:
            tick_size = 0.0
        if point <= 0:
            point = tick_size if tick_size > 0 else 0.01

        stops_level_points = int(getattr(info, "trade_stops_level", 0) or 0)
        freeze_level_points = int(getattr(info, "trade_freeze_level", 0) or 0)
        min_stop = float(stops_level_points) * point
        freeze = float(freeze_level_points) * point

        if min_stop > 0:
            lines.append(f"min_stop_distance={min_stop:.6f} (stops_level={stops_level_points} points)")
        if freeze > 0:
            lines.append(f"freeze_distance={freeze:.6f} (freeze_level={freeze_level_points} points)")

        if deviation_points is not None and deviation_points >= 0:
            lines.append(f"deviation={deviation_points} points (~{deviation_points * point:.6f} price)")

        if price is not None:
            if sl is not None:
                sl_dist = abs(float(price) - float(sl))
                lines.append(f"sl_distance={sl_dist:.6f}")
                if min_stop > 0 and sl_dist < min_stop:
                    lines.append("diagnosis=SL too close (increase SL distance or use wider ATR)")
            if tp is not None:
                tp_dist = abs(float(price) - float(tp))
                lines.append(f"tp_distance={tp_dist:.6f}")
                if min_stop > 0 and tp_dist < min_stop:
                    lines.append("diagnosis=TP too close (increase TP distance)")

    if tick is not None:
        try:
            bid = float(getattr(tick, "bid", 0.0) or 0.0)
            ask = float(getattr(tick, "ask", 0.0) or 0.0)
            if bid > 0 and ask > 0:
                lines.append(f"spread={ask - bid:.6f}")
        except Exception:
            pass

    c = (comment or "").lower()
    if "market closed" in c or "market is closed" in c:
        lines.append("diagnosis=Market closed / trading disabled for symbol")
    if "invalid stops" in c or "stops" in c and ("invalid" in c or "wrong" in c):
        lines.append("diagnosis=Invalid stops (SL/TP violates broker stop level/freeze level)")
    if "not enough money" in c or "no money" in c:
        lines.append("diagnosis=Insufficient margin/funds (reduce lot or increase free margin)")
    if "off quotes" in c or "requote" in c or "price changed" in c:
        lines.append("diagnosis=Requote/off-quotes (consider higher deviation or retry logic)")

    # If we still don't have a diagnosis, hint by retcode name
    if not any(s.startswith("diagnosis=") for s in lines):
        if "INVALID_STOPS" in rname:
            lines.append("diagnosis=Invalid stops (SL/TP too close or inside freeze level)")
        elif "NO_MONEY" in rname:
            lines.append("diagnosis=Insufficient margin/funds")
        elif "MARKET_CLOSED" in rname:
            lines.append("diagnosis=Market closed / symbol not tradable now")
        elif "PRICE_CHANGED" in rname or "REQUOTE" in rname:
            lines.append("diagnosis=Price moved too fast (requote/off-quotes)")

    # Summary line first, then key/value lines for scanability
    summary = next((s for s in lines if s.startswith("diagnosis=")), "diagnosis=Unknown (see details)")
    details = "\n".join(f"  {x}" for x in lines if not x.startswith("diagnosis="))
    return f"{summary}\n{details}".strip()


def _round_price(symbol: str, value: float) -> float:
    info = None
    try:
        info = mt5.symbol_info(symbol)
    except Exception:
        info = None
    digits = 3
    if info is not None:
        try:
            digits = int(getattr(info, "digits", digits))
        except Exception:
            digits = digits
    try:
        return round(float(value), int(digits))
    except Exception:
        return float(value)


def _apply_min_stop_distance(symbol: str, *, price: float, side: str, sl: float, tp: float) -> tuple[float, float, list[str]]:
    """
    Ensures SL/TP are not closer than broker's stop level (best-effort).
    """
    notes: list[str] = []
    info = None
    try:
        info = mt5.symbol_info(symbol)
    except Exception:
        info = None
    if info is None:
        return sl, tp, notes

    try:
        point = float(getattr(info, "point", 0.0) or 0.0)
    except Exception:
        point = 0.0
    if point <= 0:
        try:
            point = float(getattr(info, "trade_tick_size", 0.0) or 0.01)
        except Exception:
            point = 0.01

    stops_level_points = int(getattr(info, "trade_stops_level", 0) or 0)
    if stops_level_points <= 0:
        return sl, tp, notes

    min_dist = float(stops_level_points) * point
    # Give a tiny safety cushion
    min_dist *= 1.05

    if side == "buy":
        if (price - sl) < min_dist:
            sl = price - min_dist
            notes.append("sl_widened_to_meet_stops_level")
        if (tp - price) < min_dist:
            tp = price + min_dist
            notes.append("tp_widened_to_meet_stops_level")
    else:
        if (sl - price) < min_dist:
            sl = price + min_dist
            notes.append("sl_widened_to_meet_stops_level")
        if (price - tp) < min_dist:
            tp = price - min_dist
            notes.append("tp_widened_to_meet_stops_level")

    return sl, tp, notes


def _dynamic_deviation_points(symbol: str) -> int:
    """
    Computes a deviation (in points) based on live spread.
    Keeps it bounded so we don't accept wildly bad fills.
    """
    try:
        base = int(os.getenv("DEVIATION_BASE_POINTS", "20"))
    except Exception:
        base = 20
    try:
        mult = float(os.getenv("DEVIATION_SPREAD_MULT", "1.5"))
    except Exception:
        mult = 1.5
    try:
        min_pts = int(os.getenv("DEVIATION_MIN_POINTS", "10"))
    except Exception:
        min_pts = 10
    try:
        max_pts = int(os.getenv("DEVIATION_MAX_POINTS", "120"))
    except Exception:
        max_pts = 120

    base = max(0, base)
    min_pts = max(0, min_pts)
    max_pts = max(min_pts, max_pts)

    info = None
    tick = None
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
    except Exception:
        info = None
        tick = None

    if info is None or tick is None:
        return max(min_pts, min(max_pts, base))

    try:
        point = float(getattr(info, "point", 0.0) or 0.0)
    except Exception:
        point = 0.0
    if point <= 0:
        try:
            point = float(getattr(info, "trade_tick_size", 0.0) or 0.01)
        except Exception:
            point = 0.01

    try:
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
    except Exception:
        bid = 0.0
        ask = 0.0

    if bid <= 0 or ask <= 0 or ask < bid:
        return max(min_pts, min(max_pts, base))

    spread_points = int(round((ask - bid) / point)) if point > 0 else base
    dyn = int(round(base + (spread_points * mult)))
    return max(min_pts, min(max_pts, dyn))


def _should_retry_retcode(retcode: Any) -> bool:
    name = _retcode_name(retcode)
    # Be conservative: only retry transient quote/price issues.
    transient = ("REQUOTE", "PRICE_CHANGED", "OFF_QUOTES")
    return any(t in name for t in transient)


def _normalize_levels_for_execution(
    *,
    symbol: str,
    side: str,
    price: float,
    sl: float,
    tp: float,
    entry_ref: float | None,
    min_rr: float | None,
) -> tuple[float, float, list[str]]:
    """
    1) Shift SL/TP by delta between signal entry_ref and actual entry price
    2) Enforce min R:R by pushing TP outward (never inward)
    3) Best-effort ensure stops_level distance
    """
    notes: list[str] = []

    # Step 1: re-anchor to actual entry price (spread-safe)
    if entry_ref is not None:
        try:
            delta = float(price) - float(entry_ref)
            sl2 = float(sl) + delta
            tp2 = float(tp) + delta

            # Keep levels on correct side of market
            if side == "buy":
                if sl2 < price:
                    sl = sl2
                else:
                    notes.append("sl_anchor_skipped_wrong_side")
                if tp2 > price:
                    tp = tp2
                else:
                    notes.append("tp_anchor_skipped_wrong_side")
            else:
                if sl2 > price:
                    sl = sl2
                else:
                    notes.append("sl_anchor_skipped_wrong_side")
                if tp2 < price:
                    tp = tp2
                else:
                    notes.append("tp_anchor_skipped_wrong_side")

            notes.append(f"anchored_delta={delta:.6f}")
        except Exception:
            pass

    # Step 2: enforce min R:R (push TP outward only)
    if min_rr is None:
        try:
            min_rr = float(os.getenv("MIN_RR_DEFAULT", "1.2"))
        except Exception:
            min_rr = 1.2
    try:
        min_rr = float(min_rr)
    except Exception:
        min_rr = 1.2
    min_rr = max(0.8, min(5.0, min_rr))

    sl_dist = abs(float(price) - float(sl))
    tp_dist = abs(float(tp) - float(price))
    if sl_dist > 0 and tp_dist < (sl_dist * min_rr):
        need = sl_dist * min_rr
        if side == "buy":
            tp = float(price) + need
        else:
            tp = float(price) - need
        notes.append(f"tp_pushed_for_min_rr={min_rr:.2f}")

    # Step 3: best-effort minimum stop distance
    sl, tp, stop_notes = _apply_min_stop_distance(symbol, price=price, side=side, sl=sl, tp=tp)
    notes.extend(stop_notes)

    return _round_price(symbol, sl), _round_price(symbol, tp), notes


def _entry_drift_too_large(
    *,
    side: str,
    entry_ref: float | None,
    actual_price: float,
    raw_sl: float,
) -> tuple[bool, str | None]:
    if entry_ref is None:
        return False, None

    try:
        entry_ref_f = float(entry_ref)
        actual_f = float(actual_price)
        raw_sl_f = float(raw_sl)
    except Exception:
        return False, None

    planned_risk = abs(entry_ref_f - raw_sl_f)
    drift = abs(actual_f - entry_ref_f)
    if planned_risk <= 0 or drift <= 0:
        return False, None

    try:
        max_drift_r = float(os.getenv("MAX_ENTRY_DRIFT_R", "0.35"))
    except Exception:
        max_drift_r = 0.35

    max_drift_r = max(0.05, min(2.0, max_drift_r))
    drift_r = drift / planned_risk

    moved_against_signal = (
        (side == "buy" and actual_f > entry_ref_f) or
        (side == "sell" and actual_f < entry_ref_f)
    )
    if moved_against_signal and drift_r > max_drift_r:
        return True, (
            f"Entry drift too large: ref={entry_ref_f:.3f} actual={actual_f:.3f} "
            f"drift={drift:.3f} ({drift_r:.2f}R > {max_drift_r:.2f}R)"
        )

    return False, None


class MT5Executor:

    def __init__(self, symbol):
        self.symbol = symbol
        self.logger = setup_logger()
        self.reporter = LiveTradeReporter()
        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)

        # portfolio-aware state
        self.current_lot = 0.01        # default fallback
        self.last_trade_pnl = None     # updated on execution
        self.last_risk_pct = None       # cache last risk percentage
        self.last_balance = None         # cache last balance for change detection
        self.last_recalc_time = None     # track last recalculation time
        self.recalc_interval = 60       # recalculate at most every 60 seconds

    # -------------------------------------------------
    # PORTFOLIO RISK OVERRIDE
    # -------------------------------------------------

    def override_risk(self, risk_pct, strategy="unknown"):
        """
        risk_pct: fraction of account balance (e.g. 0.01 = 1%)
        strategy: strategy name for strategy-aware lot sizing
        Converts risk percentage into a tradable lot size.
        """
        # Get current account info
        account = mt5.account_info()
        if not account:
            return
        
        current_balance = account.balance
        from datetime import datetime
        current_time = datetime.utcnow()
        
        # Only recalculate if:
        # 1. Risk percentage changed
        # 2. Balance changed significantly (>1%)
        # 3. Time interval passed (60 seconds)
        balance_change_threshold = 0.01  # 1% balance change threshold
        
        should_recalculate = False
        reason = "Unknown"
        
        if self.last_risk_pct != risk_pct:
            should_recalculate = True
            reason = "Risk percentage changed"
        elif (self.last_balance is not None and 
              abs(current_balance - self.last_balance) / self.last_balance > balance_change_threshold):
            should_recalculate = True
            reason = "Balance changed significantly"
        elif (self.last_recalc_time is None or 
              (current_time - self.last_recalc_time).seconds > self.recalc_interval):
            should_recalculate = True
            reason = "Time interval passed"
        
        if not should_recalculate:
            # No significant change - use cached lot size
            return
        
        # Significant change detected - recalculate
        prev_str = f"${self.last_balance:.2f}" if self.last_balance is not None else "N/A"
        log_separator(self.logger, "LOT RECALCULATION", char="-", width=50)
        self.logger.info(
            f"  Reason: {reason}\n"
            f"  Risk: {risk_pct*100:.1f}%\n"
            f"  Balance: ${current_balance:.2f}\n"
            f"  Previous: {prev_str}"
        )
        
        self.current_lot = self._risk_to_lot(risk_pct, strategy=strategy)
        self.last_risk_pct = risk_pct
        self.last_balance = current_balance
        self.last_recalc_time = current_time

    # -------------------------------------------------
    # TRAILING STOP MANAGEMENT (post-entry)
    # -------------------------------------------------

    def manage_trailing_stop(
        self,
        *,
        timeframe: str,
        atr_period: int,
        trailing_atr_multiplier: float,
        trailing_step: float,
        strategy: str | None = None,
        magic: int = 2601,
        bars: int = 200,
    ):
        """
        True trailing stop: modifies SL on open positions.
        Only tightens SL (never loosens).

        trailing_step: fraction of ATR required before sending another SL update
        """
        try:
            positions = mt5.positions_get(symbol=self.symbol)
        except Exception:
            return

        if not positions:
            return

        # Get ATR from recent candles
        try:
            rates = mt5.copy_rates_from_pos(self.symbol, _tf(timeframe), 0, bars)
            if rates is None:
                return
            df = pd.DataFrame(rates)
            if df.empty:
                return
            atr_val = atr(df.rename(columns={"tick_volume": "tick_volume"}), atr_period).iloc[-1]
            if atr_val is None or pd.isna(atr_val) or float(atr_val) <= 0:
                return
            atr_val = float(atr_val)
        except Exception:
            return

        # Current market price
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return

        trail_dist = atr_val * float(trailing_atr_multiplier)
        step_dist = atr_val * float(trailing_step)
        if step_dist <= 0:
            step_dist = atr_val * 0.25

        for p in positions:
            if getattr(p, "magic", None) != magic:
                continue

            p_strategy = _parse_strategy_from_comment(getattr(p, "comment", None))
            if strategy and p_strategy != strategy:
                continue

            # Only apply to scalper by default
            if strategy is None and p_strategy != "xau_scalper":
                continue

            sl = float(getattr(p, "sl", 0.0) or 0.0)
            tp = float(getattr(p, "tp", 0.0) or 0.0)
            ticket = getattr(p, "ticket", None)
            if ticket is None:
                continue

            # Determine direction: MT5 uses 0 buy / 1 sell for position.type typically
            ptype = getattr(p, "type", None)
            is_buy = (ptype == mt5.POSITION_TYPE_BUY) if hasattr(mt5, "POSITION_TYPE_BUY") else (ptype == 0)

            if is_buy:
                price = float(tick.bid)
                new_sl = price - trail_dist
                if sl > 0 and new_sl <= sl + step_dist:
                    continue
                if sl > 0:
                    new_sl = max(new_sl, sl)
            else:
                price = float(tick.ask)
                new_sl = price + trail_dist
                if sl > 0 and new_sl >= sl - step_dist:
                    continue
                if sl > 0:
                    new_sl = min(new_sl, sl)

            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": int(ticket),
                "symbol": self.symbol,
                "sl": float(new_sl),
                "tp": float(tp) if tp > 0 else 0.0,
                "magic": magic,
                "comment": "pb|trail",
            }

            try:
                res = mt5.order_send(req)
                if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                    self.logger.info(f"TRAILING SL UPDATED | {self.symbol} | SL={new_sl:.3f}")
            except Exception:
                continue

    # -------------------------------------------------
    # BREAKEVEN STOP MANAGEMENT (post-entry)
    # -------------------------------------------------

    def manage_breakeven_stop(
        self,
        *,
        trigger_r: float = 0.6,
        offset_points: int = 10,
        offset_spread_mult: float = 1.0,
        min_move_points: int = 30,
        strategy: str | None = None,
        magic: int = 2601,
    ):
        """
        Moves SL to (near) entry once trade has moved favorably by trigger_r * R.
        Only moves SL in the profitable direction (never loosens).
        """
        try:
            positions = mt5.positions_get(symbol=self.symbol)
        except Exception:
            return
        if not positions:
            return

        info = None
        try:
            info = mt5.symbol_info(self.symbol)
        except Exception:
            info = None
        if info is None:
            return

        point = float(getattr(info, "point", 0.0) or 0.0)
        if point <= 0:
            point = float(getattr(info, "trade_tick_size", 0.0) or 0.01)

        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            return

        spread = max(0.0, ask - bid)

        try:
            trigger_r = float(trigger_r)
        except Exception:
            trigger_r = 0.6
        trigger_r = max(0.1, min(5.0, trigger_r))

        try:
            offset_points = int(offset_points)
        except Exception:
            offset_points = 10
        offset_points = max(0, min(5000, offset_points))

        try:
            min_move_points = int(min_move_points)
        except Exception:
            min_move_points = 30
        min_move_points = max(0, min(50000, min_move_points))

        try:
            offset_spread_mult = float(offset_spread_mult)
        except Exception:
            offset_spread_mult = 1.0
        offset_spread_mult = max(0.0, min(10.0, offset_spread_mult))

        # Broker stop level (distance from current price)
        stops_level_points = int(getattr(info, "trade_stops_level", 0) or 0)
        min_stop_dist = float(stops_level_points) * point

        offset_price = max(offset_points * point, spread * offset_spread_mult)

        for p in positions:
            if getattr(p, "magic", None) != magic:
                continue

            p_strategy = _parse_strategy_from_comment(getattr(p, "comment", None))
            if strategy and p_strategy != strategy:
                continue

            sl = float(getattr(p, "sl", 0.0) or 0.0)
            tp = float(getattr(p, "tp", 0.0) or 0.0)
            entry = float(getattr(p, "price_open", 0.0) or 0.0)
            ticket = getattr(p, "ticket", None)
            if ticket is None or entry <= 0:
                continue

            ptype = getattr(p, "type", None)
            is_buy = (ptype == mt5.POSITION_TYPE_BUY) if hasattr(mt5, "POSITION_TYPE_BUY") else (ptype == 0)

            # Need an initial SL on the loss side to define R and to avoid repeated BE moves
            if sl <= 0:
                continue
            if is_buy and sl >= entry:
                continue
            if (not is_buy) and sl <= entry:
                continue

            risk_dist = abs(entry - sl)
            if risk_dist <= 0:
                continue

            move = (bid - entry) if is_buy else (entry - ask)
            if move <= 0:
                continue

            # Extra minimum move
            if min_move_points > 0 and (move / point) < float(min_move_points):
                continue

            if move < (risk_dist * trigger_r):
                continue

            new_sl = (entry + offset_price) if is_buy else (entry - offset_price)

            # Only tighten
            if is_buy:
                if new_sl <= sl:
                    continue
                # Ensure SL is below current bid by stop distance
                if min_stop_dist > 0 and (bid - new_sl) < (min_stop_dist * 1.05):
                    continue
            else:
                if new_sl >= sl:
                    continue
                if min_stop_dist > 0 and (new_sl - ask) < (min_stop_dist * 1.05):
                    continue

            new_sl = _round_price(self.symbol, new_sl)

            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": int(ticket),
                "symbol": self.symbol,
                "sl": float(new_sl),
                "tp": float(tp) if tp > 0 else 0.0,
                "magic": magic,
                "comment": "pb|be",
            }

            try:
                res = mt5.order_send(req)
                if res and getattr(res, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                    self.logger.info(
                        f"BREAKEVEN SL UPDATED | {self.symbol} | strat={p_strategy} | SL={new_sl:.3f} | "
                        f"trigger_r={trigger_r:.2f}"
                    )
            except Exception:
                continue

    def _risk_to_lot(self, risk_pct, sl_ticks=None, strategy="unknown"):
        """
        Calculate lot size based on actual SL distance.
        Strategy-aware: uses different defaults and limits for trend vs scalper.
        If sl_ticks is provided, uses actual stop distance.
        Otherwise falls back to conservative default based on strategy type.
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

        # Use appropriate tick value for XAUUSDm
        if self.symbol == "XAUUSDm":
            tick_value = 1.0  # $1.00 per pip (100 ticks)
            tick_size = 0.01   # 1 tick = $0.01
        else:
            tick_value = info.trade_tick_value or 0.01
            tick_size = info.trade_tick_size or 0.00001

        # Strategy-specific defaults and limits
        if strategy == "xau_trend":
            default_sl_ticks = 300  # Trend strategies use wider stops
            max_lot_strategy = 0.3    # Lower max lot for trend (wider stops)
        elif strategy == "xau_scalper":
            default_sl_ticks = 50   # Scalpers use tight stops
            max_lot_strategy = 0.5  # Higher max lot for scalping (tight stops)
        else:
            default_sl_ticks = 100  # Unknown strategy - conservative default
            max_lot_strategy = 0.3  # Conservative max lot

        # Use actual SL distance if provided, otherwise strategy-specific default
        if sl_ticks is not None and sl_ticks > 0:
            actual_sl_ticks = sl_ticks
        else:
            actual_sl_ticks = default_sl_ticks

        # Calculate lot size: risk_amount / (sl_ticks * tick_value)
        raw_lot = risk_amount / (actual_sl_ticks * tick_value)

        # If broker min lot is larger than the risk-based lot, we may exceed intended risk.
        if raw_lot < min_lot:
            implied_risk = float(min_lot) * float(actual_sl_ticks) * float(tick_value)
            implied_mult = (implied_risk / float(risk_amount)) if float(risk_amount) > 0 else 9999.0
            try:
                max_mult = float(os.getenv("MIN_LOT_RISK_MULT_MAX", "3.5"))
            except Exception:
                max_mult = 2.0
            max_mult = max(1.0, min(10.0, max_mult))

            if implied_mult > max_mult:
                self.logger.warning(
                    f"LOT BLOCKED (MIN LOT) | Strategy: {strategy} | "
                    f"Requested risk={risk_pct*100:.2f}% (${risk_amount:.2f}) but broker min lot {min_lot} "
                    f"implies risk=${implied_risk:.2f} ({implied_mult:.2f}x). "
                    f"Increase balance, tighten SL, or raise MIN_LOT_RISK_MULT_MAX."
                )
                return 0.0

            self.logger.warning(
                f"LOT CLAMPED TO MIN | Strategy: {strategy} | "
                f"Requested risk=${risk_amount:.2f} but min lot {min_lot} implies risk=${implied_risk:.2f} "
                f"({implied_mult:.2f}x)."
            )
            lot = float(min_lot)
        else:
            lot = float(raw_lot)

        # normalize to broker step
        lot = round(lot / step) * step

        # Safety caps - never exceed these regardless of calculation
        max_lot_for_small_account = 0.1  # Maximum 0.1 lots for accounts under $2000
        if balance < 2000:
            lot = min(lot, max_lot_for_small_account)
        
        # Strategy-specific absolute max lot cap
        lot = min(lot, max_lot_strategy)

        # Log calculation details for verification
        log_separator(self.logger, f"LOT CALC - {strategy.upper()}", char="-", width=50)
        self.logger.info(
            f"  Balance: ${balance:.2f}\n"
            f"  Risk: {risk_pct*100:.1f}% (${risk_amount:.2f})\n"
            f"  SL Ticks: {actual_sl_ticks}\n"
            f"  Tick Value: ${tick_value:.4f}\n"
            f"  Calculated Lot: {lot:.3f}\n"
            f"  Max Lot: {max_lot_strategy}"
        )
        log_separator(self.logger, char="-", width=50)

        return lot

    def _validate_lot_size(self, lot):
        """Validate lot size against account constraints"""
        try:
            lot = float(lot)
        except Exception:
            return {"valid": False, "reason": "Invalid lot value"}

        if lot <= 0:
            return {"valid": False, "reason": "Lot is zero (blocked by risk/min-lot rules)"}

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
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick:
            return {"valid": False, "reason": "No tick data for margin calc"}
            
        margin_required = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY,
            self.symbol,
            lot,
            tick.ask
        )
        
        if margin_required is None:
            return {"valid": False, "reason": "Cannot calculate margin"}
        
        if margin_required > account.margin_free:
            return {"valid": False, "reason": f"Insufficient margin: need ${margin_required:.2f}, have ${account.margin_free:.2f}"}
        
        # Check per-trade margin usage cap (prevents oversized exposure).
        # Use MT5's own margin calculation (symbol/leverage-aware) instead of hardcoded contract assumptions.
        try:
            max_margin_pct = float(os.getenv("MAX_MARGIN_PCT_PER_TRADE", "0.30"))
        except Exception:
            max_margin_pct = 0.30
        max_margin_pct = max(0.01, min(0.95, max_margin_pct))

        max_margin_allowed = float(account.balance) * max_margin_pct
        if float(margin_required) > max_margin_allowed:
            return {
                "valid": False,
                "reason": f"Margin too high: need ${margin_required:.2f} > ${max_margin_allowed:.2f} ({max_margin_pct*100:.0f}% of balance)",
            }
        
        return {"valid": True, "reason": "OK"}

    def _check_account_protection(self):
        """Check overall account protection levels"""
        self._last_protection_block_reason = None
        account = mt5.account_info()
        if not account:
            self._last_protection_block_reason = "No account info"
            return False
        
        # Daily loss tracking
        if hasattr(self, '_daily_start_balance'):
            daily_pnl = account.balance - self._daily_start_balance
            max_daily_loss = account.balance * 0.05  # 5% daily loss limit
            
            if daily_pnl < -max_daily_loss:
                self._last_protection_block_reason = (
                    f"Daily loss limit hit: pnl=${daily_pnl:.2f} < -${max_daily_loss:.2f}"
                )
                self.logger.error(f"DAILY LOSS LIMIT HIT | Loss: ${daily_pnl:.2f} | Limit: ${max_daily_loss:.2f}")
                return False
        else:
            self._daily_start_balance = account.balance
        
        # Equity protection
        equity_ratio = account.equity / account.balance if account.balance > 0 else 1
        if equity_ratio < 0.9:  # 10% equity drawdown
            self._last_protection_block_reason = f"Equity protection: equity/balance={equity_ratio:.2f} < 0.90"
            self.logger.error(f"EQUITY PROTECTION | Equity ratio: {equity_ratio:.2f}")
            return False
        
        return True

    # -------------------------------------------------
    # EXECUTION
    # -------------------------------------------------

    def place_market_order(self, signal, lot=None, risk_pct=None):
        """
        Execute market order with proper lot sizing based on actual SL distance.
        If lot not provided, calculates it dynamically using the signal's SL.
        """
        
        strategy_name = signal.get("strategy", "unknown")
        self.logger.info(f"[EXECUTE START] {strategy_name} {signal['side']}")
        
        # Parse raw levels once; we will re-normalize per attempt (price can change).
        side = str(signal.get("side", "")).lower()
        if side not in {"buy", "sell"}:
            self.logger.error("INVALID SIDE IN SIGNAL")
            return None

        try:
            raw_sl = float(signal.get("sl"))
            raw_tp = float(signal.get("tp"))
        except Exception:
            self.logger.error("INVALID SL/TP IN SIGNAL")
            return None

        entry_ref = signal.get("entry")
        try:
            entry_ref_f = float(entry_ref) if entry_ref is not None else None
        except Exception:
            entry_ref_f = None

        min_rr = signal.get("min_rr")
        try:
            min_rr_f = float(min_rr) if min_rr is not None else None
        except Exception:
            min_rr_f = None
        
        # We need a reference price for lot sizing/logs (and for distance calcs).
        tick0 = mt5.symbol_info_tick(self.symbol)
        if not tick0:
            self.logger.error("NO TICK DATA")
            return None
        price0 = float(tick0.ask) if side == "buy" else float(tick0.bid)

        drift_blocked, drift_reason = _entry_drift_too_large(
            side=side,
            entry_ref=entry_ref_f,
            actual_price=price0,
            raw_sl=raw_sl,
        )
        if drift_blocked:
            self.logger.warning(f"[EXECUTE BLOCKED] {drift_reason}")
            return None

        # Calculate lot size if not explicitly provided
        if lot is None:
            # Get symbol info for tick size
            symbol_info = mt5.symbol_info(self.symbol)
            if not symbol_info:
                self.logger.error("NO SYMBOL INFO")
                return None
            
            tick_size = symbol_info.trade_tick_size or 0.01
            
            # Calculate SL distance in ticks
            sl_price = signal.get("sl")
            if sl_price:
                sl_distance = abs(price0 - float(sl_price))
                sl_ticks = int(sl_distance / tick_size)
            else:
                sl_ticks = None
            
            # Get strategy name for strategy-aware lot sizing
            strategy_name = signal.get("strategy", "unknown")
            
            # Calculate lot based on actual SL distance and strategy type
            calc_risk = risk_pct if risk_pct is not None else (self.last_risk_pct or 0.005)
            lot = self._risk_to_lot(calc_risk, sl_ticks, strategy_name)
            
            self.logger.info(
                f"DYNAMIC LOT CALC | Strategy: {strategy_name} | Price: {price0} | SL: {sl_price} | "
                f"Distance: {sl_distance if sl_price else 'N/A'} | Ticks: {sl_ticks} | Lot: {lot}"
            )

        # Validate lot size before execution
        self.logger.info(f"[EXECUTE] Validating lot: {lot}")
        validation_result = self._validate_lot_size(lot)
        if not validation_result["valid"]:
            self.logger.error(f"[EXECUTE BLOCKED] Lot validation failed: {validation_result['reason']}")
            return None
        self.logger.info("[EXECUTE] Lot validation passed")

        # Check account protection levels
        self.logger.info("[EXECUTE] Checking account protection...")
        if not self._check_account_protection():
            reason = getattr(self, "_last_protection_block_reason", None) or "Account protection prevented trade"
            self.logger.error(f"[EXECUTE BLOCKED] {reason}")
            return None
        self.logger.info("[EXECUTE] Account protection passed")

        if signal["side"] == "buy":
            order_type = mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL

        # Use compact comment to allow exposure/risk tracking from open positions.
        comment = _build_order_comment(strategy_name, risk_pct if risk_pct is not None else self.last_risk_pct)

        # Prepare order request (price + sl/tp computed per attempt)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": order_type,
            "price": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": _dynamic_deviation_points(self.symbol),
            "magic": 2601,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        try:
            retries = int(os.getenv("ORDER_SEND_RETRIES", "2"))
        except Exception:
            retries = 2
        retries = max(0, min(5, retries))

        try:
            sleep_ms = int(os.getenv("ORDER_SEND_RETRY_SLEEP_MS", "200"))
        except Exception:
            sleep_ms = 200
        sleep_ms = max(0, min(2000, sleep_ms))

        result = None
        last_level_log = None
        last_sl_adj = None
        last_tp_adj = None
        last_price = None
        for attempt in range(retries + 1):
            tick = mt5.symbol_info_tick(self.symbol)
            if not tick:
                self.logger.error("NO TICK DATA")
                return None

            price = float(tick.ask) if side == "buy" else float(tick.bid)
            last_price = float(price)

            sl_adj, tp_adj, lvl_notes = _normalize_levels_for_execution(
                symbol=self.symbol,
                side=side,
                price=float(price),
                sl=raw_sl,
                tp=raw_tp,
                entry_ref=entry_ref_f,
                min_rr=min_rr_f,
            )

            request["price"] = float(price)
            request["sl"] = float(sl_adj)
            request["tp"] = float(tp_adj)
            request["deviation"] = _dynamic_deviation_points(self.symbol)
            last_sl_adj = float(sl_adj)
            last_tp_adj = float(tp_adj)

            # Log level adjustment only when it changes materially (avoid spam across retries)
            lvl_log = (sl_adj, tp_adj, ",".join(lvl_notes))
            if lvl_log != last_level_log:
                if sl_adj != raw_sl or tp_adj != raw_tp:
                    self.logger.info(
                        f"LEVEL ADJUST | {signal.get('strategy','unknown')} | side={side} | "
                        f"entry_ref={entry_ref_f} actual={float(price):.6f} | "
                        f"sl {raw_sl:.6f}->{sl_adj:.6f} | tp {raw_tp:.6f}->{tp_adj:.6f} | notes={','.join(lvl_notes)}"
                    )
                last_level_log = lvl_log

            result = mt5.order_send(request)
            self.logger.info(f"[EXECUTE] Order send result (attempt {attempt+1}/{retries+1}): {result}")

            if result and getattr(result, "retcode", None) == mt5.TRADE_RETCODE_DONE:
                break

            retcode = getattr(result, "retcode", None) if result else "NO_RESULT"
            if attempt < retries and _should_retry_retcode(retcode):
                if sleep_ms > 0:
                    time.sleep(sleep_ms / 1000.0)
                continue
            break

        # ❌ FAILED ORDER
        if not result or result.retcode != mt5.TRADE_RETCODE_DONE:

            self.last_trade_pnl = None

            retcode = result.retcode if result else "NO_RESULT"
            comment = result.comment if result else "NO_RESPONSE"
            human = _human_failure_reason(
                symbol=self.symbol,
                side=str(signal.get("side", "")).lower(),
                lot=float(lot) if lot is not None else None,
                price=float(last_price) if last_price is not None else None,
                sl=float(last_sl_adj) if last_sl_adj is not None else (float(signal.get("sl")) if signal.get("sl") is not None else None),
                tp=float(last_tp_adj) if last_tp_adj is not None else (float(signal.get("tp")) if signal.get("tp") is not None else None),
                deviation_points=int(request.get("deviation", 0)) if isinstance(request.get("deviation", 0), int) else None,
                retcode=retcode,
                comment=str(comment) if comment is not None else None,
            )

            strategy_name = signal.get("strategy", "unknown")
            log_separator(self.logger, f"ORDER FAILED - {strategy_name.upper()}", char="-", width=50)
            self.logger.error(
                f"  Symbol: {self.symbol}\n"
                f"  Side: {signal['side'].upper()}\n"
                f"  Retcode: {retcode} ({_retcode_name(retcode)})\n"
                f"  Reason: {comment}\n"
                f"  {human}"
            )
            log_separator(self.logger, char="-", width=50)

            self.notifier.send(
                f"ORDER FAILED | {strategy_name.upper()}\n"
                f"{self.symbol} {signal['side'].upper()}\n"
                f"Reason: {human.splitlines()[0]}"
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
        strategy_name = signal.get("strategy", "unknown")
        exec_price = float(request.get("price", 0.0) or 0.0)
        exec_sl = float(request.get("sl", 0.0) or 0.0)
        exec_tp = float(request.get("tp", 0.0) or 0.0)
        log_separator(self.logger, f"TRADE EXECUTED - {strategy_name.upper()}", char="=", width=60)
        self.logger.info(
            f"  Symbol: {self.symbol}\n"
            f"  Side: {signal['side'].upper()}\n"
            f"  Lot: {lot}\n"
            f"  Price: {exec_price}\n"
            f"  SL: {exec_sl}\n"
            f"  TP: {exec_tp}\n"
            f"  Ticket: {result.order}"
        )
        log_separator(self.logger, char="=", width=60)

        self.notifier.send(
            f"TRADE EXECUTED | {strategy_name.upper()}\n"
            f"{self.symbol} {signal['side'].upper()}\n"
            f"Lot: {lot}\n"
            f"Price: {exec_price}\n"
            f"SL: {exec_sl}\n"
            f"TP: {exec_tp}\n"
            f"Ticket: {result.order}"
        )

        self.reporter.record(
            symbol=self.symbol,
            side=signal["side"],
            lot=lot,
            price=exec_price,
            sl=exec_sl,
            tp=exec_tp,
            ticket=result.order,
            retcode=result.retcode,
            comment=result.comment
        )

        return result
