from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from utils.indicators import atr, adx, rsi
from utils.logger import setup_logger
from utils.sweep_context import SWEEP_EVENTS


@dataclass(frozen=True)
class LiquidityBand:
    lo: float
    hi: float

    @property
    def center(self) -> float:
        return (self.lo + self.hi) / 2.0


@dataclass
class PendingSweep:
    direction: str  # "down" (took lows) or "up" (took highs)
    band: LiquidityBand
    extreme: float
    started_at: pd.Timestamp
    age_bars: int
    sweep_min: float
    atr: float
    sweep_wick_reject: bool


class XAUSweepStrategy:
    """
    Dual-mode liquidity sweep strategy (M5, minutes hold).

    Core event:
      - price sweeps a "liquidity band" (obvious high/low levels), then either:
        - FADE: reclaims back inside quickly (stop-hunt reversal)
        - CONTINUATION: accepts beyond the level and expands (breakout continuation)
    """

    def __init__(self, config: dict):
        cfg = (config or {}).get("sweep", {})

        self.atr_period = int(cfg.get("atr_period", 14))
        self.adx_period = int(cfg.get("adx_period", 14))
        self.rsi_period = int(cfg.get("rsi_period", 7))

        self.swing_left = int(cfg.get("swing_left", 3))
        self.swing_right = int(cfg.get("swing_right", 3))
        self.swing_lookback_bars = int(cfg.get("swing_lookback_bars", 400))
        self.swing_max_levels = int(cfg.get("swing_max_levels", 25))

        self.cluster_atr_mult = float(cfg.get("cluster_atr_mult", 0.15))
        self.sweep_min_atr = float(cfg.get("sweep_min_atr", 0.20))

        self.reclaim_bars = int(cfg.get("reclaim_bars", 3))
        self.accept_bars = int(cfg.get("accept_bars", 2))

        self.fade_adx_max = float(cfg.get("fade_adx_max", 22))
        self.cont_adx_min = float(cfg.get("cont_adx_min", 25))
        self.cont_body_atr_mult = float(cfg.get("cont_body_atr_mult", 0.50))

        self.wick_reject_ratio = float(cfg.get("wick_reject_ratio", 0.55))
        self.rsi_oversold = float(cfg.get("rsi_oversold", 30))
        self.rsi_overbought = float(cfg.get("rsi_overbought", 70))

        self.sl_buffer_atr = float(cfg.get("sl_buffer_atr", 0.10))
        self.tp_fade_atr = float(cfg.get("tp_fade_atr", 0.60))
        self.tp_cont_atr = float(cfg.get("tp_cont_atr", 0.80))

        # Minimum R:R enforcement (executor will push TP outward if needed)
        self.min_rr_fade = float(cfg.get("min_rr_fade", 1.15))
        self.min_rr_cont = float(cfg.get("min_rr_cont", 1.25))

        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 2))
        self.cooldown_minutes = int(cfg.get("cooldown_minutes", 30))
        self.band_key_step = float(cfg.get("band_key_step", 0.5))

        self.logger = setup_logger()
        self.symbol = str(cfg.get("symbol", "XAUUSDm"))

        self._pending: Optional[PendingSweep] = None
        self._last_signal_at: Optional[pd.Timestamp] = None
        self._band_cooldown: dict[float, pd.Timestamp] = {}

    def bind_symbol(self, symbol: str) -> None:
        self.symbol = str(symbol)

    # -------------------------
    # Liquidity level building
    # -------------------------

    def _band_key(self, band: LiquidityBand) -> float:
        step = self.band_key_step if self.band_key_step > 0 else 0.5
        return round(band.center / step) * step

    def _in_band_cooldown(self, band: LiquidityBand, now: pd.Timestamp) -> bool:
        k = self._band_key(band)
        last = self._band_cooldown.get(k)
        if last is None:
            return False
        mins = (now - last).total_seconds() / 60.0
        return mins < float(self.cooldown_minutes)

    def _record_band_trade(self, band: LiquidityBand, now: pd.Timestamp) -> None:
        self._band_cooldown[self._band_key(band)] = now

    def _reward_to_risk_ok(self, side: str, entry: float, sl: float, tp: float, min_rr: float) -> bool:
        risk = (entry - sl) if side == "buy" else (sl - entry)
        reward = (tp - entry) if side == "buy" else (entry - tp)
        if risk <= 0 or reward <= 0:
            return False
        return (reward / risk) >= float(min_rr)

    def _pivot_levels(self, df: pd.DataFrame) -> list[float]:
        sub = df.tail(self.swing_lookback_bars)
        if len(sub) < (self.swing_left + self.swing_right + 5):
            return []

        highs = sub["high"].to_numpy()
        lows = sub["low"].to_numpy()

        levels: list[float] = []
        L = self.swing_left
        R = self.swing_right

        for i in range(L, len(sub) - R):
            h = float(highs[i])
            l = float(lows[i])

            wh = highs[i - L : i + R + 1]
            wl = lows[i - L : i + R + 1]

            if h == float(wh.max()) and h > float(wh[:L].max()) and h > float(wh[L + 1 :].max()):
                levels.append(h)
            if l == float(wl.min()) and l < float(wl[:L].min()) and l < float(wl[L + 1 :].min()):
                levels.append(l)

        # Keep only the most recent-ish unique levels by rounding
        if not levels:
            return []
        # Preserve order (most recent last); dedupe coarsely
        seen = set()
        out: list[float] = []
        for p in reversed(levels):
            key = round(float(p), 2)
            if key in seen:
                continue
            seen.add(key)
            out.append(float(p))
            if len(out) >= self.swing_max_levels:
                break
        return list(reversed(out))

    def _day_levels(self, df: pd.DataFrame, now: pd.Timestamp) -> list[float]:
        levels: list[float] = []
        if df is None or df.empty:
            return levels

        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            return levels

        today = now.date()
        yday = (now - pd.Timedelta(days=1)).date()

        try:
            today_mask = idx.date == today
            yday_mask = idx.date == yday
        except Exception:
            return levels

        if yday_mask.any():
            y = df.loc[yday_mask]
            if not y.empty:
                levels.extend([float(y["high"].max()), float(y["low"].min())])

        if today_mask.any():
            t = df.loc[today_mask]
            if not t.empty:
                levels.extend([float(t["high"].max()), float(t["low"].min())])

        return levels

    def _cluster_levels(self, levels: list[float], band_width: float) -> list[LiquidityBand]:
        if not levels:
            return []
        levels = sorted(float(x) for x in levels if x is not None)
        if band_width <= 0:
            band_width = 0.1

        bands: list[LiquidityBand] = []
        cur_lo = cur_hi = levels[0]
        for p in levels[1:]:
            if abs(p - ((cur_lo + cur_hi) / 2.0)) <= band_width:
                cur_lo = min(cur_lo, p)
                cur_hi = max(cur_hi, p)
            else:
                bands.append(LiquidityBand(lo=cur_lo, hi=cur_hi))
                cur_lo = cur_hi = p
        bands.append(LiquidityBand(lo=cur_lo, hi=cur_hi))
        return bands

    def _next_band_above(self, bands: list[LiquidityBand], price: float) -> Optional[LiquidityBand]:
        above = [b for b in bands if b.center > price]
        if not above:
            return None
        return min(above, key=lambda b: b.center)

    def _next_band_below(self, bands: list[LiquidityBand], price: float) -> Optional[LiquidityBand]:
        below = [b for b in bands if b.center < price]
        if not below:
            return None
        return max(below, key=lambda b: b.center)

    # -------------------------
    # Signal generation
    # -------------------------

    def _cooldown_ok(self, now: pd.Timestamp) -> bool:
        if self._last_signal_at is None:
            return True
        bars = int((now - self._last_signal_at).total_seconds() / 300.0)
        return bars >= self.min_bars_between_signals

    def _wick_rejection(self, row: pd.Series, direction: str) -> bool:
        try:
            high = float(row.high)
            low = float(row.low)
            o = float(row.open)
            c = float(row.close)
        except Exception:
            return False

        rng = max(1e-9, high - low)
        if direction == "down":
            lower_wick = min(o, c) - low
            return (lower_wick / rng) >= self.wick_reject_ratio
        else:
            upper_wick = high - max(o, c)
            return (upper_wick / rng) >= self.wick_reject_ratio

    def _pick_swept_band(self, bands: list[LiquidityBand], *, low: float, high: float, price: float, direction: str) -> Optional[LiquidityBand]:
        if not bands:
            return None
        if direction == "down":
            candidates = [b for b in bands if b.center < price and b.center >= low]
            return max(candidates, key=lambda b: b.center) if candidates else self._next_band_below(bands, price)
        else:
            candidates = [b for b in bands if b.center > price and b.center <= high]
            return min(candidates, key=lambda b: b.center) if candidates else self._next_band_above(bands, price)

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty:
            return None

        # Need OHLC
        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                return None

        now = pd.Timestamp(df.index[-1])
        last = df.iloc[-1]
        price = float(last.close)

        # Cooldown between signals
        if not self._cooldown_ok(now):
            return None

        # Indicators
        atr_val = atr(df, self.atr_period).iloc[-1]
        if atr_val is None or pd.isna(atr_val) or float(atr_val) <= 0:
            return None
        atr_val = float(atr_val)

        adx_val, _, _ = adx(df, self.adx_period)
        if adx_val is None or pd.isna(adx_val.iloc[-1]):
            return None
        cur_adx = float(adx_val.iloc[-1])

        rsi_val = rsi(df["close"], self.rsi_period).iloc[-1]
        if rsi_val is None or pd.isna(rsi_val):
            return None
        cur_rsi = float(rsi_val)

        band_width = atr_val * self.cluster_atr_mult
        sweep_min = atr_val * self.sweep_min_atr

        # Build liquidity bands
        levels: list[float] = []
        levels.extend(self._day_levels(df, now))
        levels.extend(self._pivot_levels(df))
        bands = self._cluster_levels(levels, band_width=band_width)

        # -------------------------
        # Pending sweep handling
        # -------------------------
        if self._pending is not None:
            p = self._pending
            p.age_bars += 1

            # Discard stale pending sweeps
            if p.age_bars > self.reclaim_bars:
                self._pending = None
                return None

            # FADE: quick reclaim back into band
            if p.direction == "down":
                # Earlier reclaim = more trades. Using band center reduces missed reversals.
                reclaimed = float(last.close) >= float(p.band.center)
                if reclaimed and not self._in_band_cooldown(p.band, now):
                    if (cur_adx <= self.fade_adx_max) or (cur_rsi <= self.rsi_oversold) or p.sweep_wick_reject:
                        entry = float(last.close)
                        sl = float(p.extreme) - (atr_val * self.sl_buffer_atr)

                        tp_atr = entry + (atr_val * self.tp_fade_atr)
                        nxt = self._next_band_above(bands, entry)
                        tp = min(tp_atr, float(nxt.center)) if (nxt and nxt.center > entry) else tp_atr

                        if not self._reward_to_risk_ok("buy", entry, sl, tp, self.min_rr_fade):
                            self.logger.info(
                                f"SWEEP FADE BUY SKIP | {now} | Band={p.band.center:.3f} | "
                                f"RR too small for capped target | Entry={entry:.3f} SL={sl:.3f} TP={tp:.3f}"
                            )
                            self._pending = None
                            return None

                        self._pending = None
                        self._last_signal_at = now
                        self._record_band_trade(p.band, now)
                        self.logger.info(
                            f"SWEEP FADE BUY | {now} | Band={p.band.center:.3f} | ADX={cur_adx:.1f} RSI={cur_rsi:.1f}"
                        )
                        return {
                            "side": "buy",
                            "sl": round(sl, 3),
                            "tp": round(tp, 3),
                            "strategy": "xau_sweep",
                            "entry": float(last.close),
                            "min_rr": float(self.min_rr_fade),
                        }

                # CONTINUATION: accept below band and expand
                accepted = float(last.close) <= float(p.band.lo) - float(p.sweep_min)
                body = abs(float(last.close) - float(last.open))
                if (
                    accepted
                    and p.age_bars >= self.accept_bars
                    and cur_adx >= self.cont_adx_min
                    and body >= (atr_val * self.cont_body_atr_mult)
                    and (not self._in_band_cooldown(p.band, now))
                ):
                    entry = float(last.close)
                    sl = float(p.band.hi) + (atr_val * self.sl_buffer_atr)
                    tp = entry - (atr_val * self.tp_cont_atr)
                    self._pending = None
                    self._last_signal_at = now
                    self._record_band_trade(p.band, now)
                    self.logger.info(
                        f"SWEEP CONT SELL | {now} | Band={p.band.center:.3f} | ADX={cur_adx:.1f} RSI={cur_rsi:.1f}"
                    )
                    return {
                        "side": "sell",
                        "sl": round(sl, 3),
                        "tp": round(tp, 3),
                        "strategy": "xau_sweep",
                        "entry": float(last.close),
                        "min_rr": float(self.min_rr_cont),
                    }

            else:  # p.direction == "up"
                reclaimed = float(last.close) <= float(p.band.center)
                if reclaimed and not self._in_band_cooldown(p.band, now):
                    if (cur_adx <= self.fade_adx_max) or (cur_rsi >= self.rsi_overbought) or p.sweep_wick_reject:
                        entry = float(last.close)
                        sl = float(p.extreme) + (atr_val * self.sl_buffer_atr)

                        tp_atr = entry - (atr_val * self.tp_fade_atr)
                        nxt = self._next_band_below(bands, entry)
                        tp = max(tp_atr, float(nxt.center)) if (nxt and nxt.center < entry) else tp_atr

                        if not self._reward_to_risk_ok("sell", entry, sl, tp, self.min_rr_fade):
                            self.logger.info(
                                f"SWEEP FADE SELL SKIP | {now} | Band={p.band.center:.3f} | "
                                f"RR too small for capped target | Entry={entry:.3f} SL={sl:.3f} TP={tp:.3f}"
                            )
                            self._pending = None
                            return None

                        self._pending = None
                        self._last_signal_at = now
                        self._record_band_trade(p.band, now)
                        self.logger.info(
                            f"SWEEP FADE SELL | {now} | Band={p.band.center:.3f} | ADX={cur_adx:.1f} RSI={cur_rsi:.1f}"
                        )
                        return {
                            "side": "sell",
                            "sl": round(sl, 3),
                            "tp": round(tp, 3),
                            "strategy": "xau_sweep",
                            "entry": float(last.close),
                            "min_rr": float(self.min_rr_fade),
                        }

                accepted = float(last.close) >= float(p.band.hi) + float(p.sweep_min)
                body = abs(float(last.close) - float(last.open))
                if (
                    accepted
                    and p.age_bars >= self.accept_bars
                    and cur_adx >= self.cont_adx_min
                    and body >= (atr_val * self.cont_body_atr_mult)
                    and (not self._in_band_cooldown(p.band, now))
                ):
                    entry = float(last.close)
                    sl = float(p.band.lo) - (atr_val * self.sl_buffer_atr)
                    tp = entry + (atr_val * self.tp_cont_atr)
                    self._pending = None
                    self._last_signal_at = now
                    self._record_band_trade(p.band, now)
                    self.logger.info(
                        f"SWEEP CONT BUY | {now} | Band={p.band.center:.3f} | ADX={cur_adx:.1f} RSI={cur_rsi:.1f}"
                    )
                    return {
                        "side": "buy",
                        "sl": round(sl, 3),
                        "tp": round(tp, 3),
                        "strategy": "xau_sweep",
                        "entry": float(last.close),
                        "min_rr": float(self.min_rr_cont),
                    }

            return None

        # -------------------------
        # New sweep detection
        # -------------------------
        if not bands:
            return None

        low = float(last.low)
        high = float(last.high)

        down_band = self._pick_swept_band(bands, low=low, high=high, price=price, direction="down")
        up_band = self._pick_swept_band(bands, low=low, high=high, price=price, direction="up")

        # Down-sweep event
        if down_band and (low < (down_band.lo - sweep_min)) and (not self._in_band_cooldown(down_band, now)):
            SWEEP_EVENTS.record(
                symbol=self.symbol,
                direction="down",
                timestamp=now,
                band_center=down_band.center,
                extreme=low,
            )
            self._pending = PendingSweep(
                direction="down",
                band=down_band,
                extreme=low,
                started_at=now,
                age_bars=0,
                sweep_min=sweep_min,
                atr=atr_val,
                sweep_wick_reject=self._wick_rejection(last, "down"),
            )
            self.logger.info(f"SWEEP EVENT DOWN | {now} | Band={down_band.center:.3f} | Extreme={low:.3f}")
            return None

        # Up-sweep event
        if up_band and (high > (up_band.hi + sweep_min)) and (not self._in_band_cooldown(up_band, now)):
            SWEEP_EVENTS.record(
                symbol=self.symbol,
                direction="up",
                timestamp=now,
                band_center=up_band.center,
                extreme=high,
            )
            self._pending = PendingSweep(
                direction="up",
                band=up_band,
                extreme=high,
                started_at=now,
                age_bars=0,
                sweep_min=sweep_min,
                atr=atr_val,
                sweep_wick_reject=self._wick_rejection(last, "up"),
            )
            self.logger.info(f"SWEEP EVENT UP | {now} | Band={up_band.center:.3f} | Extreme={high:.3f}")
            return None

        return None

