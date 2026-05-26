# 👨‍💻 Engineering Deep Dive: Delta-Neutral Engine

**Francisco Molina (Frank Mo)** | **System Architect & Full-Stack Developer**

Building a trading bot is easy; building a *resilient* trading system that handles network failures, exchange limits, and state reconciliation is hard. This document outlines the specific architectural decisions and engineering challenges I solved while building the Delta-Neutral Engine.

Furthermore, this project served as a masterclass in **AI Engineering Management**. Instead of writing raw code, I acted as the Senior Architect managing an LLM (Large Language Model) to build the system precisely to my specifications.

---

## 🧠 1. The AI Management Framework

The biggest mistake developers make with AI tools is prompting them to write code without establishing boundaries. To prevent spaghetti code and context hallucinations, I engineered a strict text-based control framework before generating a single script:

* **`claude.md` (The Source of Truth):** Defined the exact microservice architecture, strict coding rules, and tech stack. The AI was forced to read this file before writing any code.
* **`pending_tasks.md` (The Execution Engine):** Broke down the financial logic into highly atomic blocks. The AI was restricted to executing only one task at a time, enforcing test-driven progress.
* **`decisions.md` (The Memory Log):** A ledger of every technical decision and bug fix. When we encountered a race condition in SQLite, I logged the architectural fix here so the AI never repeated the mistake.

**Result:** A complex, multi-process financial system built with zero technical debt and a perfectly isolated directory structure.

---

## 🛡️ 2. The Asymmetric Rollback Protocol

**The Challenge:** Legging risk. In a Delta-Neutral strategy, you must buy Spot and short Perpetuals simultaneously. If the exchange accepts the Perpetual order but rejects the Spot order (due to liquidity, size limits, or API errors), the portfolio is suddenly exposed to massive, unhedged directional risk.

**My Solution (`OrderManager`):**
I implemented an automated `_rollback` protocol. If `open_delta_neutral` detects a desync (e.g., Spot fails with `ErrCode: 170381`, but Linear succeeds):
1. The engine triggers a `CRITICAL` alert.
2. It completely bypasses normal routing and sends a `reduceOnly=True` Market order strictly targeting the successful leg.
3. It utilizes asynchronous polling to verify the emergency exit.
4. The system locks the symbol in the database, throwing an `INTERVENTION` flag to the UI dashboard, preventing the bot from bleeding capital in a loop.

---

## ⏱️ 3. Real-Time Memory vs. API Limits

**The Challenge:** Bybit enforces strict API rate limits. Polling the exchange for funding rates of 300+ coins every 60 seconds would immediately result in an IP ban.

**My Solution (`PositionMonitor` & `DataScanner`):**
I moved data ingestion entirely to **WebSockets**. 
* The scanner maintains a silent, real-time cache in the RAM of `main.py`.
* When the bot needs to make a decision, it queries the local RAM cache instead of hitting Bybit's REST API. 
* I built a Fallback mechanism: if the WebSocket stream disconnects or goes stale for a specific coin, only *then* does the bot use a targeted REST call to fetch the missing data.

---

## 💾 4. Concurrency: The SQLite WAL Mode Fix

**The Challenge:** The system requires decoupled processes. `main.py` (The Bot) constantly writes order logs to the database, while `api.py` (The FastAPI Server) constantly reads from it to feed the React dashboard via polling. Default SQLite threw `OperationalError: database is locked` due to collision.

**My Solution (`store.py`):**
Instead of migrating to a heavier PostgreSQL instance (which ruins the lightweight nature of the bot), I injected `PRAGMA journal_mode=WAL` (Write-Ahead Logging) during the database initialization. This architectural shift allowed non-blocking concurrent reads and writes, completely eliminating the race condition while keeping the deployment footprint minimal.

---

## 📊 5. The UI: Institutional State Reconciliation

**The Challenge:** How does a disconnected React frontend know when an autonomous Python script running in the background closes a trade?

**My Solution (`App.jsx` & `api.py`):**
I implemented a robust state-reconciliation flow:
1. When the bot successfully closes a trade, it takes a snapshot of the realized PnL and updates the SQLite database.
2. The React frontend features a Portfolio Dashboard with a lightweight 15-second polling mechanism connected to a `/api/portfolio` endpoint.
3. The UI independently calculates the `TOTAL EQUITY` by querying the Bybit Wallet, and the `LIFETIME PNL` by querying the SQLite ledger.
4. This keeps the visual interface perfectly synchronized with the bot's internal memory without requiring complex WebSocket broadcasting from the FastAPI layer.