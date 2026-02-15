# XAUUSD Trading Bot

A multi-strategy automated trading system for XAUUSD (Gold) using MetaTrader 5, supporting both trend-following and scalping strategies.

## Overview

- **Platform**: MetaTrader 5 (MT5)
- **Symbol**: XAUUSDm (Gold)
- **Strategies**: xau_trend (trend following), xau_scalper (scalping)
- **Portfolio Mode**: Multiple strategies running concurrently with capital allocation
- **Risk Management**: Dynamic lot sizing, max drawdown protection, daily trade limits

## Requirements

- Python 3.10+
- MetaTrader 5 terminal (open with XAUUSDm market watch)
- Telegram credentials (for alerts)

## Installation

1. **Clone/Navigate to the project**:
   ```bash
   cd trading_bot
   ```

2. **Create and activate virtual environment**:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Telegram (optional)**:
   - Edit `config/telegram.py`
   - Add your bot token and chat ID for trade alerts

5. **Configure strategy parameters**:
   - Edit `config/strategy.yaml` for SL/TP multipliers
   - Edit `portfolio/config.py` for risk allocation per strategy

## Usage

### Start Live Trading (Portfolio Mode)

```bash
python main.py
```

This runs both strategies (trend + scalper) with portfolio risk management.

### Key Configuration Files

| File | Purpose |
|------|---------|
| `config/strategy.yaml` | SL/TP multipliers, ATR settings |
| `portfolio/config.py` | Capital allocation, max risk |
| `config/telegram.py` | Alert notifications |

### Logs

Live trading logs are saved to:
```
logs/live_trading.log
```

## Risk Controls

- Max 3 trades per day per strategy
- Max 1 open position at a time
- 2% daily loss limit (kill switch)
- 2% symbol drawdown protection
- Dynamic lot sizing based on SL distance

## Project Structure

```
trading_bot/
├── core/           # Engine, execution, broker
├── strategy/       # Trading strategies
├── portfolio/      # Capital allocation, risk
├── config/         # Strategy and telegram config
├── utils/          # Logger, indicators
├── backtest/       # Backtesting framework
├── logs/           # Trading logs
└── main.py         # Entry point
```
