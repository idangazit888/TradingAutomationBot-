# theSecondBot — Real-Time Event-Contract Trading System

An asynchronous trading and market-research platform for short-horizon binary
("up/down") markets on crypto assets. Built in Python around live WebSocket
market data, with strict paper-trading isolation, circuit-breaker risk controls,
and a large automated test suite.

> **Note on scope.** This is a **public showcase** of the system's architecture
> and engineering. The proprietary parts — the signal math, the tuned entry
> parameters, the data-collection code, and the strategy research — are
> **intentionally excluded** (see [What's not here](#whats-not-here-and-why)).
> Everything published here is real, working code; the pieces that constitute
> the trading *edge* are kept private by design.

---

## What this system does

Each asset trades a fresh 5-minute binary market: buy the side you believe wins
by window close, hold to resolution, get paid \$1/share if right. The engine:

- ingests **live order-book and trade-flow data** over WebSockets from a
  prediction-market exchange and a crypto spot exchange, concurrently;
- synthesizes per-asset price/VWAP state from a 1-minute candle engine;
- runs **multiple strategy "arms" in parallel**, each with its own isolated
  paper wallet, so competing approaches can be compared head-to-head on
  identical market conditions;
- sizes positions, applies taker fees, and enforces risk limits;
- **resolves** each window against the true outcome and books P&L;
- streams entries, exits, and daily reports to a **Telegram** channel.

Everything runs in **paper mode** (simulated capital) — the platform exists to
measure whether a statistical edge is real *before* any money is at risk.

---

## Architecture (what's in this repo)

| Area | Modules | Responsibility |
|------|---------|----------------|
| **Live data feeds** | `feeds.py` | Async WebSocket clients (order books + trade stream), auto-reconnect, subscription pruning, feed watchdog |
| **Candle / VWAP engine** | `vwap_engine.py`, `ud_engine.py`, `volatility.py`, `ud_indicators.py` | Per-asset 1-minute candle synthesis, session VWAP, volatility indicators |
| **Paper accounting** | `paper_account.py`, `ud_ledger.py`, `ud_portfolio.py`, `trade_journal.py` | Isolated wallets, append-only trade ledger, per-arm P&L, persistence |
| **Position lifecycle** | `ud_position.py`, `ud_pricing.py`, `ud_confirm.py` | Sizing, taker-fee model, entry confirmation, resolution |
| **Risk controls** | `risk_manager.py` | Concurrency caps, circuit breakers (daily-loss / consecutive-loss halts) |
| **Execution** | `execution.py`, `live_startup.py`, `preflight.py` | Order placement abstraction, startup checks |
| **Signal scaffolding** | `vwap_signal.py`, `ud_config.py`* | Setup/trigger structure (the *tuned thresholds* live in the private layer) |
| **Monitoring** | `telegram_notifier.py`, `telegram_commands.py`, `vwap_telegram.py`, `notifications.py`, `slippage_logger.py` | Real-time alerts, remote control commands, execution-quality logging |
| **Config** | `vwap_config.py` | Runtime configuration (non-sensitive) |
| **Analysis tools** | `analyze_*.py`, `diagnose_trades.py` | Post-hoc ledger analysis |

\* The strategy-parameter file is private; the config *structure* is shown.

### Design highlights

- **Fully asynchronous** (`asyncio`): multiple live feeds, market discovery, and
  trading loops run concurrently on a single event loop.
- **Isolated risk arms**: every strategy variant has a separate wallet, ledger,
  and portfolio, so results never cross-contaminate.
- **Defense-in-depth risk**: circuit breakers halt trading automatically on daily
  or consecutive-loss thresholds.
- **Test-covered**: a representative sample of the suite is included
  (`test_*.py`) — covering async multi-asset feeds, feed-failure watchdogs,
  and paper-account accounting + persistence. (The full system carries a much
  larger suite; a focused subset is shown here.)
- **Deployed for 24/7 operation**: runs on a Linux VPS under PM2 process
  management with backups and feed watchdogs.

---

## What's *not* here (and why)

To protect the trading strategy, these are deliberately kept out of the public
repo:

- **Signal math** — the probability model, velocity/flow filters, and the exact
  entry logic.
- **Tuned parameters** — per-asset entry thresholds, price bands, and tier gates.
- **Data-collection pipeline** — the tick/trade collectors that feed the research.
- **Strategy research** — backtests, out-of-sample validation studies, and the
  full strategy spec.
- **All data & secrets** — market data, trade history, logs, credentials
  (`.env`).

This isn't missing work — it's the core IP. The architecture above is fully
functional; the private layer plugs into it through small, well-defined
interfaces (a signal module and a parameter file). The split itself reflects a
deliberate boundary between *reusable infrastructure* and *proprietary edge*.

---

## Tech stack

**Python** · `asyncio` · WebSockets · `pandas` / `NumPy` · `pytest` ·
Linux VPS · PM2 · Telegram Bot API

## Engineering practices demonstrated

- Real-time, concurrent data processing over unreliable network connections
  (reconnect logic, watchdogs, stale-feed detection)
- Financial-grade correctness: ledger-to-wallet reconciliation, taker-fee
  modeling, resolution accounting
- Statistical validation discipline (out-of-sample testing — in the private
  research layer)
- Test-first development across the codebase
- Operating a long-running system in production (deployment, monitoring, alerting)
