# XAUUSD Trading Bot

A multi-strategy automated trading system for XAUUSD (Gold) using MetaTrader 5, supporting both trend-following and scalping strategies.

## Overview

- **Platform**: MetaTrader 5 (MT5)
- **Symbol**: XAUUSDm (Gold)
- **Strategies**: xau_trend (trend following), xau_scalper (scalping), xau_regime (adaptive)
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
   - Create a `.env` file in the project root with:
     - `TG_TOKEN=...`
     - `TG_CHAT_ID=...`
   - Or set those as environment variables

5. **Configure strategy parameters**:
   - Edit `config/strategy.yaml` for SL/TP multipliers
   - Edit `portfolio/config.py` for risk allocation per strategy

## Usage

### Start Live Trading (Portfolio Mode)

```bash
python main.py
```

This runs your configured portfolio strategies with portfolio risk management.

### Monitoring API (for external apps)

When you run `python main.py`, the bot also starts a local HTTP monitoring API by default:

- **Health**: `GET /health`
- **Status**: `GET /status`
- **Recent closed deals**: `GET /deals/recent?limit=50`
- **List logs**: `GET /logs/list`
- **Tail logs**: `GET /logs/tail?name=live_trading.log&lines=200`

Defaults:
- `MONITORING_API_HOST=127.0.0.1`
- `MONITORING_API_PORT=8000`

Recommended for remote access:
- Set `API_TOKEN=your-long-random-token` and send `Authorization: Bearer <token>`
- Set `MONITORING_API_HOST=0.0.0.0` (and secure it via firewall / reverse proxy)

To disable the API:
- `MONITORING_API_DISABLED=1`

### Key Configuration Files

| File | Purpose |
|------|---------|
| `config/strategy.yaml` | SL/TP multipliers, ATR settings |
| `portfolio/config.py` | Capital allocation, max risk |
| `.env` | Telegram + monitoring API env vars |

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
