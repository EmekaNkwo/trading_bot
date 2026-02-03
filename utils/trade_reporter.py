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
