import matplotlib.pyplot as plt


def plot_equity(equity_curve):

    plt.figure(figsize=(12, 5))
    plt.plot(equity_curve, linewidth=2)

    plt.title("Equity Curve")
    plt.xlabel("Trades")
    plt.ylabel("Balance")

    plt.grid(True)
    plt.tight_layout()
    plt.show()
