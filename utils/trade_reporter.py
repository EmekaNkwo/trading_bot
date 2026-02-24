import csv
import os
from datetime import datetime


class LiveTradeReporter:

    def __init__(self, filename="reports/live_trades.csv"):
        self.filename = filename
        os.makedirs("reports", exist_ok=True)

        # create file + header once
        if not os.path.exists(self.filename):
            with open(self.filename, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "symbol",
                    "side",
                    "lot",
                    "price",
                    "sl",
                    "tp",
                    "ticket",
                    "retcode",
                    "comment"
                ])

    def record(self, *, symbol, side, lot, price, sl, tp, ticket, retcode, comment):

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with open(self.filename, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp,
                symbol,
                side.upper(),
                lot,
                round(price, 5),
                round(sl, 5),
                round(tp, 5),
                ticket,
                retcode,
                comment
            ])


class ClosedDealReporter:
    """
    Records realized PnL from *closed* MT5 deals.

    This file is used by performance guards and reporting, and is intentionally
    separate from `live_trades.csv` (which is mostly order-send outcomes).
    """

    def __init__(self, filename="reports/live_deals.csv"):
        self.filename = filename
        os.makedirs("reports", exist_ok=True)

        if not os.path.exists(self.filename):
            with open(self.filename, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "symbol",
                    "side",
                    "volume",
                    "price",
                    "pnl",
                    "balance",
                    "magic",
                    "deal_ticket",
                    "order_ticket",
                    "comment",
                ])

    def record(
        self,
        *,
        timestamp,
        symbol,
        side,
        volume,
        price,
        pnl,
        balance,
        magic,
        deal_ticket,
        order_ticket,
        comment,
    ):
        ts = timestamp
        if hasattr(timestamp, "strftime"):
            ts = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        with open(self.filename, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                ts,
                symbol,
                str(side).upper(),
                volume,
                round(float(price), 5),
                round(float(pnl), 2),
                round(float(balance), 2) if balance is not None else "",
                magic if magic is not None else "",
                deal_ticket if deal_ticket is not None else "",
                order_ticket if order_ticket is not None else "",
                comment,
            ])
