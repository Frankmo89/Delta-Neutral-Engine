# Pending Tasks — Tablero de Control del Funding Bot

> Actualizar este archivo al completar cada tarea. `[x]` = completado, `[ ]` = pendiente.

---

## Bloque A: Primeras tareas críticas `[COMPLETADO]`

- [x] P1 — Corrección matemática en `risk/position_sizer.py` con modelo de break-even por periodos.
- [x] P2 — Cadencia de monitoreo en `backend/main.py` sin pausa de 8h tras apertura.
- [x] P3 — Seguridad en `core/order_manager.py` con rollback sobre balance real y `reduceOnly` en cierres lineales.
- [x] P4 — Alineación de criterios en `core/position_monitor.py` usando `PositionSizer` para decidir cierres.
- [x] P5 — Manejo de fallo de cierre con reintentos por pata y bloqueo para intervención manual.
- [x] P6 — Firma `orderLinkId` + filtrado temporal por símbolos gestionados en memoria (`_bot_positions`).

## Bloque B: Robustez `[EN PROGRESO]`

- [x] P7 — Reintentos con backoff en lecturas REST + idempotencia en `place_order` por `orderLinkId`.
- [x] P8 — Persistencia SQLite (`bot_positions`, `trades`) e integración de recuperación en `PositionMonitor`.
- [x] P10 — Separar proceso del bot y API read-only con CORS configurable seguro.
- [x] Hotfix Producción — Cierre de patas independientes en `close_delta_neutral` para evitar naked short cuando una pata llega a `qty=0`.
- [x] Hotfix Pre-Mainnet — `set_leverage` obligatorio en `open_delta_neutral` antes del gather de órdenes; retCode 110043 tratado como éxito silencioso.
- [x] **P-CRÍTICO Pre-Mainnet** — Verificación de fill real en `open_delta_neutral`: polling de balance spot y short_size linear tras el gather; `retCode=0` dejó de ser criterio suficiente de éxito; rollback automático si cualquier pata no llena.
- [x] **P-CRÍTICO Pre-Mainnet** — Verificación de integridad del hedge en `PositionMonitor.check_active_positions`: detecta short desnudo (spot < qty * tolerance) y cierra la pata linear inmediatamente antes de evaluar funding.
- [x] Hotfix Riesgo — Modelo de fricción en `risk/position_sizer.py` corregido a break-even por periodos (sin anualizar costo único), con gate por `MAX_BREAKEVEN_PERIODS` + `MIN_NOTIONAL_USDT` desde settings.

## Bloque C: Rendimiento y Red `[EN PROGRESO]`

- [x] P14 — WebSocket de tickers/funding con caché in-memory y fallback REST por staleness.
- [x] P13 — Scanner WS con productor único compartido + desconexión limpia: snapshot buffered en `app.state` sin scans duplicados por cliente; handler WS corta limpio ante desconexión sin reenviar por conexión cerrada.

## Bloque F: UI y UX `[EN PROGRESO]`

- [x] P21 — Escala semántica de colores para Funding Rate + barra de magnitud visual en el dashboard.
- [x] P22 — Contador regresivo en vivo por `nextFundingTime` (actualización cada segundo).
- [x] P23 — Tabla de posiciones enriquecida con PnL, breakeven e intervención manual visible.

---

## Fase 1: Infraestructura Base `[COMPLETADA]`

- [x] Generar andamiaje del proyecto (estructura de carpetas y módulos).
- [x] Configurar entorno virtual y dependencias (`requirements.txt`, `pybit`, `ccxt`, `loguru`, etc.).
- [x] Implementar `config/settings.py` — singleton con validación de `.env`.
- [x] Implementar `core/exchange.py` — wrapper `BybitExchange` (pybit + ccxt).
- [x] Implementar `data/scanner.py` — `FundingRateScanner` con ranking por APR.
- [x] Implementar `risk/position_sizer.py` — `PositionSizer` con modelo de fricción y `ViabilityReport`.
- [x] Implementar `core/order_manager.py` — esqueleto `OrderManager` con `asyncio.gather`.
- [x] Pruebas exitosas de conexión REST y WebSockets en Testnet (`scripts/test_conexion.py`).

---

## Fase 2: Ejecución y Riesgo `[EN PROGRESO]`

- [x] Implementar la llamada real a la API de Bybit V5 dentro de `order_manager.py` usando `pybit`.
  - [x] Reemplazar la simulación `asyncio.sleep` por `self.exchange.session.place_order(...)`.
  - [x] Manejar `category="spot"` y `category="linear"` correctamente.
  - [x] `marketUnit="baseCoin"` en Spot Buy para simetría exacta de notional con la pata de futuros.
  - [x] `run_in_executor` para no bloquear el event loop y garantizar paralelismo real en `gather()`.
  - [x] Captura de `orderId` en respuesta exitosa y `retCode`/`retMsg` en caso de rechazo.

- [x] Programar el protocolo de emergencia (Rollback) si falla una de las patas (Spot o Perp).
  - [x] Si Spot OK y Perp FAIL → colocar orden SELL en Spot para cerrar.
  - [x] Si Spot FAIL y Perp OK → colocar orden BUY en Perp para cerrar.
  - [x] Si ambas FAIL → no hay posición abierta, log crítico sin acción.
  - [x] Si el rollback mismo falla → log `FALLO CRÍTICO` con aviso de intervención manual.

- [x] Integrar `position_sizer.py` en `order_manager.py`.
  - [x] Nueva firma: `open_delta_neutral(symbol, current_price, funding_rate_pct, max_capital_usdt)`.
  - [x] Instanciar `PositionSizer` y llamar a `evaluate()` antes de cualquier orden.
  - [x] Abortar con `logger.warning` si `report.is_viable == False`.
  - [x] Calcular `qty` con `sizer.calculate_qty(report.position_size_usdt, current_price)`.
  - [x] Validar `qty > 0` antes de disparar órdenes.

---

## ✅ Fase 2: Ejecución y Riesgo `[COMPLETADA]`

---

## Fase 3: El Orquestador `[EN PROGRESO]`

- [x] Crear `main.py` — bucle principal asíncrono.
  - [x] Instanciar `BybitExchange`, `FundingRateScanner`, `OrderManager`.
  - [x] Loop: scan → Top 1 → `open_delta_neutral` → pausa breve si se abre posición.
  - [x] `TESTNET_CAPITAL_CAP_USDT = 50 USDT` — tope de seguridad en Testnet.
  - [x] `_sleep_or_stop()` — pausa interruptible por `stop_event` en todos los waits.
  - [x] Graceful shutdown: `SIGINT`/`SIGTERM` → `stop_event.set()` con fallback Windows.

- [x] Gestión de posiciones abiertas.
  - [x] Crear `core/position_monitor.py` — clase `PositionMonitor` con `check_active_positions()` y `get_open_symbols()`.
  - [x] Consultar posiciones lineares abiertas vía `session.get_positions(category="linear")`.
  - [x] Evaluar funding rate actual contra `MIN_FUNDING_THRESHOLD = 0.001%` (≈ 1.095% APR bruto).
  - [x] Llamar a `OrderManager.close_delta_neutral()` cuando el funding cae bajo el umbral.
  - [x] `close_delta_neutral(symbol, qty)` — SELL Spot + BUY Linear con `asyncio.gather` y `_apply_qty_step`.

- [x] Manejo de señales del sistema operativo (`SIGINT`, `SIGTERM`).
  - [x] `stop_event` + `SIGINT`/`SIGTERM` ya implementado en `main.py` (Fase 3 inicial).
  - [x] Al parar, el loop termina limpiamente; posiciones activas persisten y son gestionadas en el próximo arranque por `PositionMonitor`.

- [x] Integrar `PositionMonitor` en `main.py`.
  - [x] Al inicio de cada ciclo: `monitor.check_active_positions()` — cierra posiciones no rentables.
  - [x] Si `monitor.get_open_symbols()` retorna algo, saltar escaneo y esperar (no abrir duplicados).
  - [x] Si no hay posición: escanear, abrir y pausar (luego el monitor decide el cierre).

---

## ✅ Fase 3: El Orquestador `[COMPLETADA]`

---

## ✅ Fase 4.5: Ajuste de Precisión de Cierre `[COMPLETADA]`

- [x] **Asimetría de cierre** en `close_delta_neutral()` — los dos pasos difieren para cada pata:
  - Pata Spot SELL: usar `walletBalance` real de la moneda base (UNIFIED) para no vender más de lo disponible tras las comisiones de compra.
  - Pata Linear BUY: usar `qty` nominal del contrato reportado por Bybit (el short fue registrado exactamente).
- [x] Lectura de balance Spot y qty_steps en un solo `asyncio.gather` triple para mínima latencia.
- [x] Validaciones independientes por pata: si una qty colapsa a 0 y la otra >0, cerrar la pata remanente y persistir cierre; abortar solo si ambas qty=0.
- [x] **Prueba de estrés superada**: bot detectó balance Spot = 0.0, abortó limpiamente y evitó descalce.
- [x] Valores de producción restaurados: `min_funding_threshold=0.001`, `TESTNET_CAPITAL_CAP_USDT=50.0`.

---

## 🏆 MVP — Arquitectura 100% Finalizada y Lista para Operar

> Todos los módulos del núcleo están implementados, probados en Testnet y documentados.
> El bot puede abrir, monitorear y cerrar posiciones Delta-Neutral de forma autónoma.

---

## Fase 5: Servidor y UI `[EN PROGRESO]`

- [x] Reestructuración Monorepo.
  - [x] Crear `backend/` en la raíz.
  - [x] Mover `core/`, `data/`, `risk/`, `config/`, `scripts/`, `main.py`, `.env`, `requirements.txt` dentro de `backend/`.
  - [x] Mantener `venv/`, `claude.md`, `pending_tasks.md` en raíz.
  - [x] Definir `.gitignore` en raíz con reglas explícitas para `venv/`, `.env`, `__pycache__/`, `*.pyc`, `node_modules/`.
  - [x] Establecer convención: toda ejecución Python desde `backend/`.

- [x] Implementación de servidor FastAPI (backend API).
  - [x] Crear `backend/api.py` con app FastAPI y CORS abierto (`allow_origins=["*"]`).
  - [x] Exponer endpoints REST: `GET /api/status`, `GET /api/positions`.
  - [x] Exponer WebSocket `WS /ws/scanner` para streaming periódico del scanner.
  - [x] Definir lifespan startup/shutdown con `asyncio.create_task` y cierre graceful por `stop_event`.
  - [x] Refactor en `backend/main.py` para ejecución reusable desde API (`create_bot_components`, `run_trading_service`).
  - [x] Listo para servir con: `cd backend && uvicorn api:app --reload`.

- [x] Inicialización de UI con Vite + React (frontend).
  - [x] Crear carpeta `frontend/` en raíz.
  - [x] Inicializar proyecto Vite React + Tailwind y configurar cliente API.
  - [x] Dashboard conectado: fetch inicial a `GET /api/positions` + stream `WS /ws/scanner`.
  - [x] Migración visual completada (TopBar, Active Positions, Live Arbitrage Scanner, indicador WS).
  - [x] Íconos `lucide-react` integrados y lint frontend en estado limpio.

- [ ] Producción y Monitoreo.
  - [ ] Migrar de Testnet a Mainnet (`BYBIT_TESTNET=false`) con capital real.
  - [x] P19 — Alertas push por Telegram para rollback, cierres fallidos y arranque opcional.
  - [ ] Añadir persistencia de logs rotativos con `loguru` (archivo + consola).
  - [ ] Dockerizar backend y frontend para despliegue en VPS.

---

*Última actualización: Fase 5 Step 4 completado — Dashboard React conectado al backend (REST + WebSocket) y listo para iterar UX.*
