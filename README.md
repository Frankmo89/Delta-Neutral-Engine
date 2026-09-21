# ⚡ Delta-Neutral Engine

[![tests](https://github.com/Frankmo89/Delta-Neutral-Engine/actions/workflows/tests.yml/badge.svg)](https://github.com/Frankmo89/Delta-Neutral-Engine/actions/workflows/tests.yml)

> **Automated Crypto Arbitrage & Funding Rate Capture System**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.0%2B-61DAFB?logo=react&logoColor=black)](https://reactjs.org/)
[![Vite](https://img.shields.io/badge/Vite-5.0%2B-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![SQLite](https://img.shields.io/badge/SQLite-WAL_Mode-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Bybit API](https://img.shields.io/badge/Bybit-V5_API-F7A600?logo=bybit&logoColor=black)](https://bybit-exchange.github.io/docs/v5/intro)

**Delta-Neutral Engine** is a high-performance quantitative trading bot built for the Bybit V5 API. It scans the cryptocurrency derivatives market for high-yield funding rates, automatically calculates break-even points, and executes concurrent trades (Spot Buy + Perpetual Short) to capture "rent" while strictly hedging market risk to zero.

> ⚠️ **Testnet Configuration:** This system is currently configured to run on the Bybit Testnet for demonstration and algorithmic validation.

---
> 🙋‍♂️ **Recruiters & Hiring Managers:** This project was developed using a strict **AI-Assisted Architecture Framework**. To see how I managed an LLM to write production-grade, multi-process code without hallucinations, please see my **[PORTFOLIO.md](./PORTFOLIO.md)**.
---

## ✨ Key Features

* **Concurrent Delta-Neutral Execution:** Fires Spot Buy and Linear Perpetual Short orders simultaneously to eliminate leg-exposure latency.
* **Live Arbitrage Scanner:** Connects to Bybit WebSockets to stream real-time funding rates, calculating Net APR and friction costs in milliseconds.
* **Asymmetric Rollback Protocol:** Intelligent safety net that immediately unwinds the successful leg if the exchange rejects the opposite leg (e.g., due to `maxOrderQty` limits).
* **Institutional Dashboard:** A React/Tailwind V4 dark-mode terminal surfacing Active Positions, Lifetime PnL, and live scanner metrics.
* **WAL-Mode SQLite Persistence:** Concurrent database architecture allowing the frontend to read PnL while the backend quant engine writes execution logs simultaneously without locks.

---

## Running tests

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..
pytest
```

`pytest.ini` sets `pythonpath = backend` and `testpaths = backend/tests`.

## Testing

The suite covers `PositionSizer` math and viability gates (`_gross_apr`, `_breakeven_periods`, `calculate_qty`, funding / min-notional / max-breakeven checks) and `FundingRateScanner._parse_tickers`, `_filter`, and `_rank`. Tests stub exchange clients and do not call Bybit.

---

## 🏛️ System Architecture

The platform operates across three completely decoupled layers:

### Layer 1 — Quant Engine (Python)
| Component | Task |
| :--- | :--- |
| **Data Scanner** | WebSocket cache maintaining real-time states for 300+ instruments. |
| **Risk Sizer** | Calculates strict Break-Even periods and clips order sizes to exchange limits. |
| **Order Manager** | Handles concurrent execution, asynchronous polling, and emergency rollbacks. |

### Layer 2 — API & Persistence (FastAPI & SQLite)
| Component | Task |
| :--- | :--- |
| **REST API** | Read-only endpoints for the UI, plus specific manual intervention hooks (Force DB Cleanup). |
| **State Store** | SQLite tracking `orderLinkId` signatures, realized PnL, and intervention locks. |

### Layer 3 — UI Terminal (React/Vite)
| Component | Task |
| :--- | :--- |
| **Portfolio Widget** | 15-second polling to update Total Equity and Lifetime PnL. |
| **Scanner Table** | Sorts live opportunities by funding signal (High Yield vs. Risk). |

---

## 🛡️ Financial Safety Guardrails

| Guardrail | Implementation |
| :--- | :--- |
| **Legging Risk Prevention** | Concurrent execution with sub-second polling verification. |
| **Exchange Limit Awareness** | Dynamic `maxOrderQty` and `qty_step` caching to mathematically clip oversized orders before HTTP requests are made. |
| **Idempotency** | Cryptographically signed UUIDs (`FBOT-symbol-uuid8`) to prevent duplicate fills on network timeouts. |
| **Orphaned Position Lock** | Ignores existing positions that do not carry the `FBOT` signature to protect human-placed trades. |

---

## 📂 Project Structure

```text
Delta-Neutral-Engine/
├── backend/
│   ├── config/             # Settings, limits, and environment vars
│   ├── core/               # Quant Engine: OrderManager, PositionMonitor, Exchange
│   ├── data/               # WebSocket streams and live Arbitrage Scanner
│   ├── api.py              # FastAPI server connecting SQLite to React
│   └── main.py             # The autonomous trading loop
├── frontend/
│   ├── src/                # React application, Tailwind CSS, App.jsx
│   └── vite.config.js      # Bundler configuration
├── claude.md               # Master AI Architecture Rules
├── pending_tasks.md        # Sprint Tracker
└── decisions.md            # Engineering Memory & Bug Fix Logs
```

## Installation & Deployment (Linux/Ubuntu)

Prerequisites: Python 3.10+, Node.js v20 LTS, Bybit Testnet API keys.

```bash
git clone https://github.com/Frankmo89/Delta-Neutral-Engine.git
cd Delta-Neutral-Engine/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Created by Francisco Molina (@Frankmo89)
