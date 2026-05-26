# Prompts para el Agente — Funding Bot (Hoja de Ruta)

> Un prompt = un cambio lógico = un commit. Ejecútalos en orden (los bloques A→G van de "antes de tocar Mainnet" a "pulido").
> Cada prompt ya incluye la instrucción de **leer** `claude.md` + `pending_tasks.md` antes y **actualizarlos** al terminar.
> Empieza SIEMPRE por **P0**: deja el protocolo escrito dentro de `claude.md` para que el agente lo respete solo en cada sesión.

---

## P0 — Protocolo permanente del agente (hazlo primero)

```
Lee claude.md completo. Añade una sección nueva titulada "Protocolo de Trabajo del Agente (OBLIGATORIO)" con estas reglas:

1. Antes de cualquier cambio: leer claude.md completo y pending_tasks.md.
2. Un cambio lógico = un commit. No mezclar tareas distintas en el mismo commit.
3. Al terminar cada tarea: actualizar claude.md si cambian contratos públicos, firmas de métodos, arquitectura o decisiones de diseño; y marcar [x] o añadir la tarea en pending_tasks.md.
4. Nunca tocar .env. Nunca hardcodear credenciales ni montos: todo parámetro va en config/settings.py leído desde .env.
5. Todo I/O con el exchange es async; métodos internos con prefijo _; from __future__ import annotations en todos los módulos.
6. Si un cambio rompe un contrato documentado, actualizar claude.md en el MISMO commit.

No cambies código todavía. Solo edita claude.md y haz commit "docs: protocolo de trabajo del agente".
```

---

# BLOQUE A — Correcciones críticas (antes de Mainnet)

## P1 — Modelo de fricción / break-even

```
Lee claude.md y pending_tasks.md antes de empezar.

Corrige el modelo de fricción en risk/position_sizer.py. Bug actual: friction_annual = friction * FUNDING_PERIODS_PER_DAY * DAYS_PER_YEAR multiplica un costo ÚNICO de round-trip (abrir+cerrar) por ~1095, como si se pagara cada 8h. La fricción de round-trip es un costo único por la vida de la posición, no recurrente.

Reemplaza la lógica de viabilidad por un modelo de break-even por periodos:
- funding_per_period = abs(funding_rate)
- roundtrip_cost = (taker_fee + slippage) * 2 patas * 2 (apertura + cierre)
- breakeven_periods = roundtrip_cost / funding_per_period   (cuántos periodos de 8h se necesitan solo para amortizar entrar/salir)
- is_viable si breakeven_periods <= MAX_BREAKEVEN_PERIODS Y position_size >= MIN_NOTIONAL_USDT.

Añade a config/settings.py: MAX_BREAKEVEN_PERIODS (default 3) y MIN_NOTIONAL_USDT (default 10), leídos desde .env con fallback. Quita el "10" mágico hardcodeado en position_sizer y el min_net_apr_pct como gate (déjalo solo informativo si quieres). Añade breakeven_periods al ViabilityReport y a su __str__.

Antes de cerrar, imprime un par de casos numéricos en el log (funding 0.01%, 0.03%, 0.10%) para verificar que el filtro ahora aprueba rates realistas.

Al terminar actualiza claude.md (sección PositionSizer + tabla de Decisiones de Diseño) y marca la tarea en pending_tasks.md.
```

## P2 — Cadencia de monitoreo (main.py)

```
Lee claude.md y pending_tasks.md antes de empezar.

En main.py, tras abrir una posición se hace await _sleep_or_stop(FUNDING_CYCLE_SECONDS, ...) (8h), pero el log dice que monitorea cada SCAN_INTERVAL_SECONDS. Eso deja la posición sin revisar 8h: si el funding cae a la hora, se sigue pagando sin reaccionar.

Cambia ese sleep posterior a la apertura por SCAN_INTERVAL_SECONDS y deja que PositionMonitor.check_active_positions() decida el cierre en cada ciclo. Elimina o reusa FUNDING_CYCLE_SECONDS solo donde tenga sentido, y corrige el mensaje de log para que refleje la cadencia real.

Al terminar actualiza claude.md (Flujo de Datos) y pending_tasks.md.
```

## P3 — Rollback con balance real + reduceOnly

```
Lee claude.md y pending_tasks.md antes de empezar.

Dos fixes en core/order_manager.py, mismo commit (ambos son seguridad de cierre):

1) En _rollback, la rama "Spot OK + Perp FAIL" vende qty nominal en spot, pero la compra con marketUnit=baseCoin descuenta la comisión del token base → quedas con qty*(1-fee) y "Sell qty" falla por insufficient balance (ErrCode 170131). Haz que el rollback de Spot lea el walletBalance real de la moneda base (UNIFIED) y aplique _apply_qty_step, igual que close_delta_neutral. Reutiliza el helper de lectura de balance.

2) Todas las órdenes cuyo propósito es CERRAR una pata linear (el BUY de _rollback y el BUY de close_delta_neutral) deben enviarse con reduceOnly=True, para que nunca abran un long si el qty supera la posición. Pásalo como parámetro opcional en _colocar_orden.

Al terminar actualiza claude.md (descripción de OrderManager / Reglas Críticas #3) y pending_tasks.md.
```

## P4 — Alinear criterio de apertura y cierre

```
Lee claude.md y pending_tasks.md antes de empezar.

Hoy se abre solo si el trade es viable con fricción (PositionSizer), pero se cierra cuando el funding BRUTO cae bajo MIN_FUNDING_THRESHOLD (≈1.1% APR, sin descontar fricción). Eso mantiene posiciones que nunca se habrían abierto y donde el costo de cerrar+reabrir supera el funding cobrado.

Alinea el criterio de cierre con el de apertura: en core/position_monitor.py, en vez de comparar contra un funding bruto fijo, reutiliza PositionSizer para evaluar si la posición SIGUE siendo viable (mismo modelo de break-even) y ciérrala cuando deje de serlo. Mueve el umbral a config si conviene.

Al terminar actualiza claude.md (PositionMonitor) y pending_tasks.md.
```

## P5 — Manejo de fallo de cierre

```
Lee claude.md y pending_tasks.md antes de empezar.

Cuando close_delta_neutral falla en una pata hoy solo loguea critical y deja una pata huérfana (exposición direccional). Añade recuperación:
- Si una de las dos patas del cierre falla, reintenta SOLO la pata fallida (con balance/qty fresco) hasta N veces.
- Si tras los reintentos sigue abierta, loguea critical con datos accionables (symbol, pata, qty) y deja un estado claro para alerta posterior.
- En PositionMonitor, no marcar la posición como cerrada si close_delta_neutral devolvió False.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P6 — orderLinkId para identificar posiciones propias

```
Lee claude.md y pending_tasks.md antes de empezar.

core/position_monitor.py trata CUALQUIER short USDT como propio y lo cerraría. Etiqueta las órdenes del bot:
- En _colocar_orden (order_manager), genera y envía un orderLinkId con prefijo configurable (p.ej. "FBOT-{symbol}-{uuid}") en cada place_order.
- Guarda/expone la relación símbolo→bot para que PositionMonitor filtre solo posiciones del bot. (Si aún no hay persistencia, usa por ahora un prefijo reconocible y filtra por él; la fuente de verdad definitiva llega en el prompt de SQLite.)
- Añade BOT_ORDER_PREFIX a config/settings.py.

Al terminar actualiza claude.md (convenciones + OrderManager/PositionMonitor) y pending_tasks.md.
```

---

# BLOQUE B — Robustez

## P7 — Reintentos con backoff + idempotencia

```
Lee claude.md y pending_tasks.md antes de empezar.

Las llamadas REST a Bybit abortan el ciclo ante errores transitorios (timeouts, retCode 10006 rate limit). Añade reintentos con backoff exponencial (usa tenacity o un decorador propio) en las llamadas de solo-lectura (get_tickers, get_positions, get_instruments_info, get_wallet_balance).

Para place_order: NO reintentar a ciegas. Usa el orderLinkId del prompt P6 como clave de idempotencia y, ante timeout sin respuesta, consulta el estado de la orden antes de reenviar para no duplicar.

Añade los parámetros de reintento (intentos, base de backoff) a config/settings.py.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P8 — Persistencia de estado en SQLite

```
Lee claude.md y pending_tasks.md antes de empezar.

Crea una capa de persistencia ligera (SQLite, stdlib sqlite3 o sqlmodel) en backend/, p.ej. core/store.py. Registra:
- trades: orderLinkId, symbol, lado, qty, precio, categoría, timestamp, estado.
- posiciones lógicas del bot (apertura/cierre, par de orderIds spot+linear).
- eventos de funding cobrado (se llenará en el prompt de tracking).

Integra: OrderManager escribe al abrir/cerrar; PositionMonitor lee de aquí para saber qué posiciones son del bot (sustituye el filtro por prefijo de P6 por esta fuente de verdad). Asegura recuperación tras reinicio leyendo posiciones abiertas desde la DB y reconciliando contra Bybit.

Añade la ruta de la DB a config/settings.py. Documenta el esquema en claude.md.

Al terminar actualiza claude.md (nuevo módulo store.py + Flujo de Datos) y pending_tasks.md.
```

## P9 — Verificar balance real + sizing dinámico

```
Lee claude.md y pending_tasks.md antes de empezar.

Hoy PositionSizer asume available_balance = max_capital sin consultar la wallet. Antes de comprometer capital en open_delta_neutral, consulta el balance real (get_wallet_balance UNIFIED, campo totalAvailableBalance) y pásalo como available_balance_usdt real a evaluate(). Aborta limpio si no alcanza, en vez de fallar a media apertura y disparar rollback.

Añade un sizing dinámico opcional: desplegar un porcentaje configurable del balance disponible (CAPITAL_FRACTION en settings) en lugar de un tope fijo, respetando siempre max_position_usdt y el cap de Testnet.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P10 — Separar proceso del bot + cerrar la API

```
Lee claude.md y pending_tasks.md antes de empezar.

Desacopla el motor de trading del proceso de la API:
- Que main.py sea el proceso del bot (un solo proceso, un solo motor).
- Que backend/api.py NO arranque el motor en el lifespan; que solo lea estado (de SQLite del prompt P8) y exponga REST/WS. Documenta que el bot y la API son procesos separados.
- Cierra la API para Mainnet: cambia allow_origins=["*"] por una lista configurable (CORS_ORIGINS en settings) y añade auth básica (token bearer leído de .env) a los endpoints REST y al WS.

Al terminar actualiza claude.md (sección Fase 5 / arranque) y pending_tasks.md.
```

---

# BLOQUE C — Rendimiento y red

## P11 — Executor dedicado

```
Lee claude.md y pending_tasks.md antes de empezar.

Todos los run_in_executor(None, ...) usan el ThreadPoolExecutor por defecto, compartido y limitado; en el gather de apertura las dos patas pueden serializarse. Crea un ThreadPoolExecutor dedicado (max_workers configurable, thread_name_prefix="bybit") en BybitExchange o inyectado a OrderManager, y úsalo explícitamente en todos los run_in_executor del flujo de órdenes. Ciérralo en el shutdown.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P12 — Warmup de instrumentos + minOrderAmt

```
Lee claude.md y pending_tasks.md antes de empezar.

Dos cosas en core/exchange.py:
1) Warmup: al arranque, carga get_instruments_info(category) SIN symbol para spot y linear, y llena el cache de qty_step de una sola vez, para que el primer trade no pague esa latencia en el momento crítico.
2) Captura también minOrderAmt (mínimo notional en USDT) de spot y exponlo. En order_manager, valida que el notional de la orden spot supere minOrderAmt antes de disparar; aborta limpio si no.

Al terminar actualiza claude.md (BybitExchange) y pending_tasks.md.
```

## P13 — Snapshot compartido para /ws/scanner + batch tickers

```
Lee claude.md y pending_tasks.md antes de empezar.

Dos optimizaciones de red, mismo commit:
1) backend/api.py: hoy cada cliente WS dispara su propio scanner.scan() cada 5s. Crea UNA sola task de fondo que escanee periódicamente y guarde el último snapshot en app.state; cada conexión /ws/scanner solo lee y reenvía ese snapshot compartido (broadcast). El costo de red deja de escalar con el número de clientes.
2) core/position_monitor.py: reemplaza las llamadas get_tickers por símbolo en bucle por UNA llamada get_tickers(category="linear") sin symbol y filtra en memoria.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P14 — WebSocket de tickers/funding

```
Lee claude.md y pending_tasks.md antes de empezar.

Implementa data/websockets.py (hoy "por explorar") usando el WebSocket de pybit para suscribirte al stream de tickers (precio + fundingRate) de los símbolos relevantes. Reemplaza el polling REST del scanner y el monitor por este feed donde sea posible, manteniendo REST como fallback. Reduce drásticamente las llamadas REST/min y la latencia. Maneja reconexión.

Al terminar actualiza claude.md (data/websockets.py + Flujo de Datos) y pending_tasks.md.
```

---

# BLOQUE D — Estrategia (avanzado)

## P15 — Slippage real desde el orderbook

```
Lee claude.md y pending_tasks.md antes de empezar.

En risk/position_sizer.py el slippage es una constante 0.0002. Usa exchange.get_orderbook(symbol) para estimar el impacto real de mercado contra la profundidad disponible para el notional que vas a mover (spot y linear), y alimenta ese slippage estimado al modelo de break-even. Mantén la constante como fallback si el orderbook no está disponible.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P16 — Múltiples posiciones simultáneas

```
Lee claude.md y pending_tasks.md antes de empezar.

Hoy main.py abre solo el Top 1 y bloquea el resto con open_symbols. Refactoriza para permitir N posiciones simultáneas (MAX_CONCURRENT_POSITIONS en settings): evalúa el Top N del scanner, descarta símbolos ya abiertos, reparte capital entre las viables y abre varias. El candado pasa de "¿hay alguna posición?" a "¿este símbolo ya está abierto?" + "¿queda cupo y capital?".

Al terminar actualiza claude.md (Flujo de Datos) y pending_tasks.md.
```

## P17 — Funding predicho y rebalanceo del hedge

```
Lee claude.md y pending_tasks.md antes de empezar.

Dos mejoras de estrategia (puedes hacerlas en commits separados; documenta decisiones):
1) Ranking por funding PREDICHO del próximo settlement (no solo el actual): confirma qué campo del ticker de Bybit corresponde al próximo funding y úsalo en el scanner/decisión.
2) Diseña (y deja anotado en claude.md) una lógica de rebalanceo del hedge: como el precio mueve el notional relativo de las dos patas, define un umbral de drift a partir del cual reequilibrar para volver a Delta ≈ 0. Implementa si es viable; si no, deja el diseño documentado como tarea futura.

Al terminar actualiza claude.md (Decisiones de Diseño) y pending_tasks.md.
```

---

# BLOQUE E — Observabilidad

## P18 — Tracking de funding cobrado real + PnL neto

```
Lee claude.md y pending_tasks.md antes de empezar.

Implementa el tracking de ingresos reales: consulta get_transaction_log (type=SETTLEMENT) de Bybit para registrar cada pago de funding por posición, guárdalo en SQLite (prompt P8) y calcula PnL neto = funding cobrado − comisiones pagadas. Expón este PnL real por posición y agregado en /api/positions y /api/status. NO uses el unrealisedPnl del perp como PnL de la estrategia.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P19 — Alertas push

```
Lee claude.md y pending_tasks.md antes de empezar.

Añade alertas push (Telegram o Discord webhook, configurable en .env) que se disparen en eventos critical: rollback ejecutado, pata huérfana tras fallo de cierre, fallo crítico que requiere intervención manual. Crea un módulo notifier ligero y engánchalo en los logger.critical relevantes (o vía un sink de loguru). Si no hay credenciales configuradas, degrada a solo-log sin romper.

Al terminar actualiza claude.md y pending_tasks.md.
```

## P20 — Logs rotativos

```
Lee claude.md y pending_tasks.md antes de empezar.

Configura logging persistente con loguru (tarea ya listada en Fase 5): añade un sink a archivo con rotation="100 MB" y retention="30 days", manteniendo la salida a consola. Hazlo configurable (ruta de log en settings). Aplica en el arranque del bot y de la API.

Al terminar actualiza claude.md y marca la tarea de logs en pending_tasks.md.
```

---

# BLOQUE F — Frontend / Diseño

## P21 — Color semántico + barras de magnitud en el scanner

```
Lee claude.md y pending_tasks.md antes de empezar (frontend/).

En la tabla Live Arbitrage Scanner:
- Codifica el FUNDING RATE por signo: positivo en verde, negativo en rojo/atenuado (un funding negativo NO es oportunidad para la estrategia long-spot/short-perp).
- Convierte la columna NET APR de texto verde plano a una mini-barra horizontal de fondo que se llene proporcional al valor, con intensidad de color por magnitud. El ojo escanea barras más rápido que dígitos.
- Capa el display de APR irreales: >500% muestra "500%+", y añade un badge sutil "Testnet — rates no realistas" cuando settings.testnet sea true (toma el flag de /api/status).

Stack: React + Tailwind + lucide (el del proyecto). Al terminar actualiza claude.md/pending_tasks.md si aplica.
```

## P22 — Rediseño de la tarjeta de posición activa

```
Lee claude.md y pending_tasks.md antes de empezar (frontend/).

Rediseña la tarjeta de Active Positions para una estrategia delta-neutral. Debe mostrar:
- Las DOS patas: Spot LONG qty y Perp SHORT qty (confirmación visual de cobertura).
- Un countdown al próximo settlement de funding ("próximo funding en 3h 12m") — es el dato más importante de la pantalla.
- Funding cobrado acumulado y PnL neto (de /api/positions, prompt P18).
- Una barra de "distancia al umbral de cierre" que muestre qué tan cerca está el funding del umbral de PositionMonitor.
- Etiqueta clara para el número de precio (hoy "26.2100" aparece sin label).
Dale más peso visual que al scanner (borde de acento / glow) para reflejar que es estado real con capital comprometido.

Al terminar actualiza pending_tasks.md.
```

## P23 — Pulido visual y feedback de WebSocket

```
Lee claude.md y pending_tasks.md antes de empezar (frontend/).

Pulido del dashboard:
- Números con font-variant-numeric: tabular-nums y columnas numéricas alineadas a la derecha; baja la precisión de APR a 1 decimal (funding puede quedar en 4).
- Feedback de WS: flash sutil de fondo en las filas que cambian al llegar un snapshot + un "actualizado hace Xs".
- Más aire entre filas (padding/line-height).
- Header con 2-3 KPIs globales: capital desplegado, # posiciones, funding cobrado hoy.
- Empty state del scanner con skeleton ("buscando oportunidades…") en vez de tabla vacía.
- Corrige package.json: lucide-react ^1.16.0 se ve inventado (la librería va en 0.x); fija una versión real y verifica que el resto de dependencias existan.

Al terminar actualiza pending_tasks.md.
```

---

# BLOQUE G — Calidad y limpieza

## P24 — Tests con exchange mockeado

```
Lee claude.md y pending_tasks.md antes de empezar.

Añade pytest con un BybitExchange mockeado (sin red). Cubre la lógica que mueve dinero:
- Las 4 ramas de _rollback (Spot OK/Perp FAIL, Spot FAIL/Perp OK, ambas FAIL, rollback que falla).
- La asimetría de cierre en close_delta_neutral (balance spot real vs qty nominal linear) incluyendo balance spot = 0.
- El modelo de break-even de PositionSizer (casos viable / no viable / posición demasiado pequeña).
- reduceOnly presente en las patas de cierre.

Añade los tests a CI si existe. Al terminar actualiza claude.md y pending_tasks.md (nueva tarea de tests).
```

## P25 — Limpieza final

```
Lee claude.md y pending_tasks.md antes de empezar.

Limpieza de código, mismo commit:
- Inyecta PositionSizer una sola vez (es stateless salvo config) en lugar de instanciarlo dentro de cada open_delta_neutral.
- ccxt se inicializa y autentica en exchange.py pero no lo usa nadie: quítalo, o márcalo claramente como "reservado para futuro" sin autenticarlo al arranque.
- En get_open_positions (API/UI), etiqueta unrealisedPnl como "solo perp" o reemplázalo por el PnL neto real del prompt P18, para no engañar en el dashboard.

Al terminar actualiza claude.md (Decisiones de Diseño / convenciones) y pending_tasks.md.
```

---

## Orden sugerido si vas con prisa hacia Mainnet

P0 → P1 → P3 → P2 → P4 → P5 → P6 → P7 → P8 → P19 (alertas) → P10 (cerrar API) → resto.

Los demás bloques (rendimiento, estrategia avanzada, diseño) suman valor pero no son los que evitan que un fallo silencioso se coma capital real.
