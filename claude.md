# Claude.md — Cerebro Arquitectónico del Funding Bot

> Este archivo es la fuente de verdad para el agente. **Leerlo antes de proponer cualquier cambio de código.**

---

## Visión Global

Bot de **arbitraje Delta-Neutral** (Spot vs Perpetuos) en Bybit.

**Lógica central:** Cuando el Funding Rate de un perpetuo es positivo y suficientemente alto, compramos el activo en Spot y vendemos el mismo notional en el contrato perpetuo. El resultado es una posición con Delta ≈ 0 que **cobra el funding** cada 8 horas sin exposición direccional al precio.

**Exchange:** Bybit V5 API (Testnet → Mainnet)  
**Stack:** Python 3.11+, pybit (cliente primario REST/WS), ccxt (fallback/utilidades), asyncio, pandas, loguru

---

## Reglas Críticas de Negocio

### 1. Ejecución Simultánea con `asyncio.gather` — OBLIGATORIO
Las dos patas (Spot BUY + Perp SELL) **deben dispararse en el mismo `asyncio.gather`**. Nunca ejecutarlas de forma secuencial. Esto es la defensa principal contra el **Legging Risk** (que el precio se mueva entre la primera y segunda orden, destruyendo la cobertura).

```python
# CORRECTO — siempre así
resultados = await asyncio.gather(tarea_spot, tarea_perp)

# PROHIBIDO
await tarea_spot
await tarea_perp
```

### 2. Validación de Capital Obligatoria Antes de Ejecutar
Antes de llamar a `OrderManager.open_delta_neutral()`, el flujo **debe pasar por `PositionSizer.evaluate()`**. Ninguna orden puede enviarse a Bybit si `ViabilityReport.is_viable == False`.

### 3. Protocolo de Rollback ante Descalce
Si `asyncio.gather` devuelve que una pata falló y la otra tuvo éxito, se debe ejecutar inmediatamente una orden opuesta en la pata exitosa para volver a Delta ≈ 0. El bot nunca debe quedar con una posición unilateral.

### 4. Capital Máximo por Operación
Definido en `.env` como `MAX_POSITION_USDT`. El `PositionSizer` lo lee desde `settings.max_position_usdt`. No hardcodear montos en `OrderManager`.

### 5. Leverage Conservador
`LEVERAGE` máximo permitido: 10x (validado en `Settings.__post_init__`). Para la estrategia Delta-Neutral se recomienda 1x (sin apalancamiento) en el perp para igualar el notional del spot.

## Protocolo de Trabajo del Agente (OBLIGATORIO)

1. Antes de cualquier cambio: leer `claude.md` completo y `pending_tasks.md`.
2. Un cambio lógico = un commit. No mezclar tareas distintas en el mismo commit.
3. Al terminar cada tarea: actualizar `claude.md` si cambian contratos públicos, firmas de métodos, arquitectura o decisiones de diseño; y marcar `[x]` o añadir la tarea en `pending_tasks.md`.
4. Nunca tocar `.env`. Nunca hardcodear credenciales ni montos: todo parámetro va en `config/settings.py` leído desde `.env`.
5. Todo I/O con el exchange es async; métodos internos con prefijo `_`; `from __future__ import annotations` en todos los módulos.
6. Si un cambio rompe un contrato documentado, actualizar `claude.md` en el MISMO commit.

---

## Estado de la Arquitectura (Monorepo)

```
funding_bot/
│
├── backend/
│   ├── .env                # Credenciales y parámetros del bot (Bybit/API).
│   ├── requirements.txt    # Dependencias Python del backend.
│   ├── config/
│   │   └── settings.py         # Singleton `settings` — carga .env, valida parámetros.
│   │                           # Expone: api_key, api_secret, testnet, max_position_usdt,
│   │                           #          max_retries, max_network_retries,
│   │                           #          backoff_factor, bot_order_prefix,
│   │                           #          telegram_bot_token, telegram_chat_id,
│   │                           #          cors_origins, api_access_key,
│   │                           #          ws_ticker_max_symbols_per_connection,
│   │                           #          ws_ticker_stale_seconds, leverage
│   │
│   ├── core/
│   │   ├── exchange.py         # BybitExchange — wrapper pybit (HTTP) + ccxt.
│   │   │                       # Provee: self.session (pybit HTTP), self._ccxt_client.
│   │   │                       # Detecta testnet/mainnet automáticamente.
│   │   │                       # Lecturas REST con retry + backoff exponencial
│   │   │                       # (get_tickers, get_positions, get_instruments_info,
│   │   │                       #  get_wallet_balance total_usdt, get_orderbook).
│   │   │
│   │   ├── order_manager.py    # OrderManager — ejecución Delta-Neutral completa. ✅
│   │   │                       # open_delta_neutral(symbol, current_price,
│   │   │                       #                   funding_rate_pct, max_capital_usdt)
│   │   │                       #   1. PositionSizer.evaluate() — abortar si not is_viable
│   │   │                       #   2. calculate_qty() — tamaño en tokens
│   │   │                       #   3. asyncio.gather(spot_buy, perp_sell) — paralelo real
│   │   │                       #      via run_in_executor (pybit es síncrono)
│   │   │                       #   3.1 Todas las órdenes se firman con orderLinkId:
│   │   │                       #       {BOT_ORDER_PREFIX}-{symbol}-{uuid8}
│   │   │                       #   3.2 place_order NO reintenta a ciegas:
│   │   │                       #       verifica idempotencia por orderLinkId
│   │   │                       #       (open_orders/order_history) antes de reenvío
│   │   │                       #   4. _rollback() si falla una pata
│   │   │                       #      - Spot OK + Perp FAIL: vende balance real Spot UNIFIED
│   │   │                       #      - BUY linear de cierre usa reduceOnly=True
│   │   │                       # close_delta_neutral(symbol, qty)
│   │   │                       #   1. asyncio.gather([get_qty_step spot, linear], spot_balance)
│   │   │                       #      spot_balance = walletBalance de base_coin en UNIFIED
│   │   │                       #   2. ASIMETRÍA DE CIERRE (Fase 4.5)
│   │   │                       #      qty_spot   = _apply_qty_step(spot_balance, spot_step)
│   │   │                       #      qty_linear = _apply_qty_step(qty,          linear_step)
│   │   │                       #   3. asyncio.gather(spot_sell qty_spot, linear_buy qty_linear)
│   │   │                       #      linear_buy usa reduceOnly=True
│   │   │                       #   4. Si falla una pata: retry SOLO de la pata fallida
│   │   │                       #      hasta settings.max_retries con qty/balance fresco
│   │   │                       #   5. log critical accionable si persiste el fallo
│   │   │
│   │   ├── store.py            # SQLiteStore — persistencia local SQLite (P8).
│   │   │                       # Tablas:
│   │   │                       #   - bot_positions(symbol, status, open_timestamp, ...)
│   │   │                       #     + requires_manual_intervention, intervention_reason,
│   │   │                       #       realized_pnl
│   │   │                       #   - trades(order_link_id, symbol, side, qty, ...)
│   │   │
│   │   ├── notifier.py         # TelegramNotifier — alertas push opcionales (P19).
│   │   │                       # Degradación elegante si faltan credenciales.
│   │   │                       # Eventos críticos: rollback, cierre fallido, startup.
│   │   │
│   │   └── position_monitor.py # PositionMonitor — ciclo de vida de posiciones. ✅
│   │                           # check_active_positions()
│   │                           #   1. get_positions(category="linear") → posiciones Sell > 0
│   │                           #   1.1 Filtra SOLO símbolos gestionados en memoria
│   │                           #       en SQLite: store.get_open_symbols(status='open')
│   │                           #   2. get_current_funding_rate(symbol)
│   │                           #   3. PositionSizer.evaluate_existing_position() con break-even
│   │                           #   4. Si no es viable o funding deja de compensar la fricción:
│   │                           #      → order_manager.close_delta_neutral(symbol, qty)
│   │                           #   5. Si close devuelve False: símbolo queda bloqueado
│   │                           #      para intervención manual (no reabrir)
│   │                           # get_open_symbols() → list[str] de símbolos con short activo
│   │
│   ├── data/
│   │   ├── scanner.py          # FundingRateScanner — prioriza WS cache (Push) y
│   │   │                       # fallback REST (Pull) si cache vacío/stale.
│   │   └── websockets.py       # FundingTickerCache — stream WS linear por lotes.
│   │                           # Caché in-memory por símbolo con fundingRate/lastPrice/
│   │                           # nextFundingTime/volume24h + timestamp.
│   │
│   ├── risk/
│   │   └── position_sizer.py   # PositionSizer — validación por break-even de periodos.
│   │                           # roundtrip_cost = (taker_fee + slippage) * 2 patas * 2
│   │                           # breakeven_periods = roundtrip_cost / abs(funding_rate)
│   │                           # is_viable si breakeven_periods <= settings.max_breakeven_periods
│   │                           # y position_size_usdt >= settings.min_notional_usdt
│   │
│   ├── scripts/
│   │   ├── calcular_viabilidad.py   # Script manual para testear PositionSizer.
│   │   └── test_conexion.py         # Script de smoke test: REST + WS en Testnet.
│   │
│   ├── api.py                  # Servidor FastAPI read-only (P10). ✅
│   │                           # Lee estado desde SQLite + scanner read-only
│   │                           # NO arranca el motor de trading
│   │                           # REST: GET /api/status, GET /api/positions,
│   │                           #       GET /api/portfolio
│   │                           # /api/positions expone unrealized_pnl,
│   │                           # breakeven_periods y flags de intervención manual
│   │                           # /api/portfolio expone total_balance + lifetime_pnl
│   │                           # WS:   /ws/scanner (stream de oportunidades)
│   │
│   └── main.py                 # Orquestador principal del backend.
│                               # Exporta create_bot_components() y
│                               # run_trading_service() para integración API.
│
├── claude.md                   # Arquitectura y reglas de negocio (fuente de verdad).
├── pending_tasks.md            # Kanban operativo del proyecto.
├── frontend/
│   └── src/
│       └── App.jsx             # Dashboard React (P21/P22/P23): semántica visual de funding,
│                               # countdown live y tabla institucional de posiciones activas
│                               # (PnL, breakeven, intervención manual).
└── venv/                       # Entorno virtual local (permanece en raíz).
```

**Regla operativa Fase 5:** todo comando Python debe ejecutarse desde `backend/`.

**Arranque Bot (motor):** `cd backend && python main.py`.

**Arranque API (read-only):** `cd backend && uvicorn api:app --reload`.

**Arquitectura P10:** el bot y la API son procesos separados. El motor de trading vive solo en `main.py`; `api.py` consulta SQLite y datos de mercado en modo lectura.

**Arquitectura P14 (Push vs Pull):**
- Fuente primaria (Push): `FundingTickerCache` vía WebSocket de Bybit (tickers linear).
- Fallback (Pull): lecturas REST endurecidas (P7) cuando el cache está vacío o stale.
- Ambos procesos (`main.py` y `api.py`) levantan su cliente WS al inicio y lo cierran en shutdown.

**Arquitectura Frontend P21/P22:**
- UI React consume `GET /api/positions` y `WS /ws/scanner`.
- El scanner renderiza escala semántica de funding (verde/neutral/rojo) con barra de magnitud.
- Cada fila muestra countdown en vivo (`hh:mm:ss`) al próximo settlement usando `nextFundingTime`.

**Arquitectura Frontend P23 (Monitor de Posiciones):**
- La sección de posiciones activas usa tabla enriquecida con `unrealized_pnl`, `breakeven_periods` y estado operativo.
- PnL se colorea semánticamente (verde ganancias, rojo pérdidas).
- Si `requires_manual_intervention=true`, la fila entra en modo alerta visual agresiva y muestra `intervention_reason`.

**Arquitectura P24 (Portfolio Dashboard):**
- Backend expone `GET /api/portfolio` con `total_balance` (equity consolidada USDT desde Bybit) y `lifetime_pnl` (suma histórica de `realized_pnl` en SQLite).
- `bot_positions` persiste `realized_pnl` al cierre exitoso de `close_delta_neutral` para mantener histórico acumulable post-reinicio.
- Frontend renderiza un widget superior institucional con métricas `TOTAL EQUITY (USDT)` y `LIFETIME PNL (USDT)` con semántica de color para PnL.

---

## Flujo de Datos (Happy Path)

```
main.py → startup
    │
    ├─→ FundingTickerCache.start(symbols_linear)
    │
    └─→ trading_loop()
  │
  ├─→ FundingRateScanner.scan(top_n=5)
    │       ├─→ WS cache fresh → usar funding/price en memoria
    │       └─→ si WS stale/vacío → REST get_tickers(category="linear")
  │           └─→ DataFrame: [symbol, funding_rate, funding_rate_pct,
  │                            apr_est, volume_24h, last_price]
  │
  ├─→ top = df.iloc[0]   ← par con mayor APR estimado
  │
  ├─→ capital = min(settings.max_position_usdt, TESTNET_CAP)  ← 50 USDT en testnet
  │
  └─→ OrderManager.open_delta_neutral(
              symbol, current_price, funding_rate_pct, capital)
          │
          ├─→ PositionSizer.evaluate()  ← aborta si not is_viable
          ├─→ calculate_qty()           ← tokens = position_usdt / price
          │
          └─→ asyncio.gather(
                  _colocar_orden(symbol, "Buy",  qty, "spot"),
                  _colocar_orden(symbol, "Sell", qty, "linear")
              )  ← run_in_executor: paralelismo real con pybit síncrono
              │
          ├─ Ambas OK   → persistir en SQLite:
          │              bot_positions(status='open') + trades(2 órdenes)
          │              pausa breve (~5s) y siguiente ciclo de monitor
              └─ Una falla  → _rollback() → orden inversa en pata exitosa

PositionMonitor.check_active_positions()
    │
    ├─→ funding por símbolo desde WS cache (primario)
    └─→ fallback REST por símbolo si dato WS stale/vacío
```

---

## Persistencia SQLite (P8)

- **DB local:** `DB_PATH` (default `backend/data/bot_database.db`).
- **Fuente de verdad de posiciones del bot:** tabla `bot_positions` con `status='open'`.
- **Histórico de ejecución:** tabla `trades` (orderLinkId, symbol, side, qty, category, timestamp).
- **Recuperación tras reinicio:** `PositionMonitor` carga símbolos abiertos desde SQLite y solo gestiona esos símbolos.

---

## Decisiones de Diseño Registradas

| # | Decisión | Motivo |
|---|----------|--------|
| 1 | `pybit` como cliente primario | Soporte nativo Bybit V5, sin overhead de abstracción cross-exchange |
| 2 | `ccxt` como cliente secundario | Utilidades de normalización y fallback en caso de deprecación de pybit |
| 3 | `loguru` para logging | Formato enriquecido, niveles de color, rotación de archivos sin configuración |
| 4 | `dataclasses` en Settings y ViabilityReport | Inmutabilidad (`frozen=True`), claridad de tipos, sin dependencias extra |
| 5 | `asyncio.gather` para ejecución paralela | Mínima latencia entre patas; evita Legging Risk |
| 6 | Testnet por defecto (`BYBIT_TESTNET=true`) | Seguridad: no se puede accidentalmente operar con capital real |
| 7 | Break-even por periodos + umbrales por config | Viabilidad depende de amortizar fricción de round-trip en <= `MAX_BREAKEVEN_PERIODS` y cumplir `MIN_NOTIONAL_USDT` |

---

## Variables de Entorno Requeridas (`.env`)

```env
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true          # cambiar a false para mainnet
BASE_CURRENCY=USDT
MAX_POSITION_USDT=1000
MAX_BREAKEVEN_PERIODS=3
MIN_NOTIONAL_USDT=10
MAX_RETRIES=3
BOT_ORDER_PREFIX=FBOT
MAX_NETWORK_RETRIES=3
BACKOFF_FACTOR=2.0
WS_TICKER_MAX_SYMBOLS_PER_CONN=50
WS_TICKER_STALE_SECONDS=20
DB_PATH=backend/data/bot_database.db
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
CORS_ORIGINS=http://localhost:5173
API_ACCESS_KEY=
LEVERAGE=1
```

---

## Convenciones de Código

- Todos los métodos de I/O con el exchange son `async`.
- Los métodos internos (no parte de la API pública) se prefijan con `_`.
- Los importes `from __future__ import annotations` en todos los módulos.
- Logging: `logger.debug` para trazas internas, `logger.info` para eventos de negocio, `logger.critical` para alertas de riesgo.
