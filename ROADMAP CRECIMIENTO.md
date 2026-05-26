# Roadmap de Crecimiento — Delta-Neutral Engine

> Prompts para el agente, ordenados de **mayor a menor impacto** sobre tu objetivo real:
> que funcione y veas algunos dólares de ganancia (no hacerte rico).
> Cada prompt explica **por qué** y **qué cambia** en lenguaje simple.
> Todos asumen el protocolo ya establecido: leer `claude.md`, `decisions.md`,
> `pending_tasks.md` antes; actualizar `decisions.md`/`pending_tasks.md` y validar al terminar.

---

## Primero, en simple: ¿de dónde salen los dólares?

Bybit cobra una comisión llamada **funding** cada 8 horas entre los traders de futuros, para mantener el precio del futuro pegado al precio real. Cuando hay más gente apostando "a que sube" que "a que baja", los que apostaron a que sube pagan a los que apostaron a que baja. Tu bot se pone del lado que **cobra** (short en futuro) y al mismo tiempo compra el mismo monto en spot para no tener riesgo de precio. Resultado: cobras esos pagos cada 8h sin que te importe si el precio sube o baja.

**Ganas** cuando: el funding se mantiene positivo y alto el tiempo suficiente para superar las comisiones de abrir y cerrar (~0.3% del monto).

**Pierdes** cuando: cierras antes de recuperar esas comisiones, el funding se vuelve negativo (ahí *tú* pagas), o se rompe la cobertura (short sin spot — ya lo blindamos).

**Lo que determina si ves dólares de verdad** no es predecir el mercado, es: (1) saber cuánto ganaste realmente, (2) no quemar comisiones en intentos fallidos, (3) entrar solo cuando el funding va a durar, (4) estar calibrado para mainnet. En ese orden van los bloques.

---

# BLOQUE 1 — Lo que más mueve la aguja (hazlo primero)

## 1.1 — Tracking de PnL real (cuánto funding cobraste de verdad)

**Por qué (simple):** ahora mismo el bot **no sabe si gana dinero**. Guarda un `realized_pnl` que es solo una foto del PnL del futuro al cerrar, no el funding que cobraste. Es como tener un negocio sin contar las ventas. Sin esto, no puedes responder "¿este bot me dio dólares o no?".

**Qué cambia:** el bot consulta a Bybit los pagos de funding reales que recibió por cada posición, los suma, les resta las comisiones pagadas, y te muestra el **neto real**. A partir de aquí sabrás con números si funciona.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Implementa tracking de PnL real en core/store.py y core/order_manager.py.

1. Consulta a Bybit los pagos de funding reales vía session.get_transaction_log
   (accountType="UNIFIED", category="linear", type="SETTLEMENT") por símbolo,
   acumulando el funding cobrado desde la apertura de cada posición.
2. Consulta las comisiones reales pagadas (type="TRADE") por los orderId
   registrados en la tabla trades.
3. Al cerrar una posición (mark_position_closed), calcula y persiste:
   realized_pnl = funding_cobrado - comisiones_pagadas (campo claro, no la foto
   del unrealisedPnl). Renombra el campo a net_pnl_usdt si hace falta para
   evitar confusión.
4. Añade en store.py un método get_pnl_summary() que devuelva: funding total
   cobrado, comisiones totales, y net acumulado, separando posiciones cerradas
   vs abiertas.
5. Expón esto en /api/portfolio.

Documenta en decisions.md: "PnL real = funding cobrado (SETTLEMENT) menos
comisiones (TRADE), nunca el unrealisedPnl del perp".
Actualiza pending_tasks.md. Valida con py_compile.
```

## 1.2 — Cooldown para símbolos que no llenan (deja de quemar comisiones)

**Por qué (simple):** viste en los logs que el bot intentaba abrir 0GUSDT cada 60 segundos, el spot no llenaba, y hacía rollback. Cada intento fallido **paga dos comisiones de futuro** (abrir + cerrar). En testnet es dinero falso, pero en mainnet eso es plata real goteándose en bucle sobre un par que nunca vas a poder operar.

**Qué cambia:** si un símbolo falla el llenado del spot varias veces, el bot lo manda a la "banca" por un rato y deja de intentarlo, ahorrándote esas comisiones.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Implementa un cooldown/blacklist temporal de símbolos que fallan el fill del
spot, para evitar reintentos en bucle que queman comisiones de perp.

1. En core/order_manager.py, lleva un registro en memoria (y opcionalmente en
   SQLite para sobrevivir reinicios) de símbolos cuya pata spot falló el fill.
2. Si un símbolo acumula FAILED_FILL_THRESHOLD fallos (default 2), márcalo en
   cooldown por SYMBOL_COOLDOWN_SECONDS (default 1800 = 30 min).
3. En main.py / scanner, descarta del ranking cualquier símbolo en cooldown
   activo antes de intentar abrir.
4. Loguea cuándo entra y sale un símbolo del cooldown.

Añade FAILED_FILL_THRESHOLD y SYMBOL_COOLDOWN_SECONDS a config/settings.py con
validación. Documenta en decisions.md. Actualiza pending_tasks.md. py_compile.
```

## 1.3 — Calibración para mainnet (que el filtro deje pasar oportunidades reales)

**Por qué (simple):** en testnet los funding rates son de fantasía (1%, 2%). En mainnet lo normal es 0.01%–0.05% cada 8h. Con tu configuración actual (`MAX_BREAKEVEN_PERIODS=3`), el bot solo abre si el funding es muy alto, así que en mainnet **rechazaría casi todo** y nunca operarías. Hay que aflojar ese filtro a niveles realistas, con cuidado.

**Qué cambia:** el bot pasa a aceptar oportunidades de mainnet realistas (donde recuperas las comisiones en unos días de cobrar funding) en vez de exigir rendimientos imposibles.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Prepara la calibración para mainnet sin tocar el comportamiento de testnet.

1. NO hardcodees: asegúrate de que MAX_BREAKEVEN_PERIODS se lee de .env.
2. Documenta en decisions.md una tabla de referencia: con fricción 0.3%,
   un MAX_BREAKEVEN_PERIODS=15 acepta funding desde ~0.02%/periodo (break-even
   en ~5 días), MAX_BREAKEVEN_PERIODS=10 desde ~0.03%/periodo, etc. Explica
   que en mainnet los rates típicos son 0.01-0.05%/periodo.
3. Añade un parámetro min_net_apr_floor en settings (default 0%): un piso de
   APR neto bajo el cual nunca abrir aunque pase el break-even, como red de
   seguridad. Intégralo en PositionSizer.
4. Añade un modo de arranque que imprima, con la config actual, qué funding
   rate mínimo por periodo se necesita para que un trade sea viable, para que
   el operador entienda qué está filtrando.

Actualiza pending_tasks.md. py_compile.
```

## 1.4 — Tests automatizados (para no romper lo que ya funciona)

**Por qué (simple):** ya tienes lógica que mueve dinero (rollback, verificación de fill, cierre de patas). Cada vez que el agente toca el código, podría romper algo sin que te enteres hasta que pierdas plata en mainnet. Los tests son una red que avisa al instante si un cambio rompió un mecanismo de seguridad.

**Qué cambia:** corres un comando y en segundos sabes si todo lo crítico sigue funcionando, antes de arriesgar capital.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Crea una suite de tests con pytest y un BybitExchange mockeado (sin red real).
Cubre la lógica que mueve dinero:

- Verificación de fill: spot que NO llena → se trata como fallo → rollback del
  perp (no queda short desnudo, no se guarda en SQLite).
- Las 4 ramas de _rollback (spot OK/perp fail, spot fail/perp OK, ambas fail,
  rollback que falla).
- Cierre de patas independientes: spot=0 con perp>0 → cierra solo linear.
- Integrity check del monitor: spot=0 con short>0 → detecta hedge roto y cierra.
- PositionSizer break-even: casos viable / no viable / posición muy pequeña.
- set_leverage: retCode 110043 tratado como éxito.

Añade instrucciones en README para correr los tests. Actualiza pending_tasks.md.
```

---

# BLOQUE 2 — Robustez y eficiencia

## 2.1 — SQLite en modo WAL y lectura de wallet única por ciclo

**Por qué (simple):** dos partes del sistema (el bot y la API) escriben en el mismo archivo de base de datos; sin la configuración correcta, a veces chocan y dan error. Y el monitor lee el saldo completo de la cuenta una vez por cada posición, cuando podría leerlo una sola vez. Son arreglos de eficiencia y estabilidad.

**Qué cambia:** menos errores de "base de datos bloqueada" y menos llamadas repetidas a Bybit (te aleja de los límites de la API).

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Dos optimizaciones de robustez/eficiencia:

1. En core/store.py SQLiteStore._initialize, activa modo WAL:
   conn.execute("PRAGMA journal_mode=WAL")
   conn.execute("PRAGMA synchronous=NORMAL")
   para soportar lectura/escritura concurrente entre main.py y api.py sin
   "database is locked".

2. En core/position_monitor.py check_active_positions, lee el wallet UNIFIED
   UNA sola vez al inicio del ciclo y reúsalo para el integrity check de todas
   las posiciones, en lugar de llamar get_wallet_balance por cada símbolo.

Documenta en decisions.md. Actualiza pending_tasks.md. py_compile.
```

## 2.2 — Logs rotativos en archivo

**Por qué (simple):** ahora tus logs solo viven en la consola; si cierras la terminal, se pierden. Cuando el bot opere solo en un servidor, necesitas un archivo histórico para investigar qué pasó (sobre todo si algo falla mientras no miras).

**Qué cambia:** todo queda guardado en archivos que rotan solos, sin llenarte el disco.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Configura logging persistente con loguru en el arranque de main.py y api.py:
añade un sink a archivo con rotation="100 MB", retention="30 days",
compression="zip", manteniendo también la salida a consola. La ruta del log
debe ser configurable vía settings (LOG_PATH, default backend/logs/bot.log).
Crea el directorio si no existe. Documenta y actualiza pending_tasks.md.
```

---

# BLOQUE 3 — Áreas de oportunidad (estrategia)

## 3.1 — Pre-chequeo de liquidez antes de abrir (evita el problema que viste)

**Por qué (simple):** el bot intentó abrir 0G, ALCH, APT y ninguno tenía liquidez spot — por eso hacía rollback. Sería mejor **mirar antes** si el spot tiene profundidad suficiente para llenar, y ni intentarlo si no. Ahorra comisiones y tiempo.

**Qué cambia:** el bot revisa el libro de órdenes del spot antes de comprometerse; si no hay con qué llenar, salta ese par. Menos rollbacks, menos fees.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Añade un pre-chequeo de liquidez spot en open_delta_neutral, ANTES de disparar
las órdenes:

1. Consulta exchange.get_orderbook del lado spot para el símbolo.
2. Estima si la profundidad de venta (asks) cubre el notional que vas a comprar
   (qty * price) dentro de un slippage máximo aceptable (MAX_SLIPPAGE_PCT,
   nuevo en settings, default 0.5%).
3. Si la profundidad es insuficiente → aborta limpio ANTES de mandar órdenes,
   loguea "liquidez spot insuficiente", y manda el símbolo a cooldown (1.2).

Esto evita abrir el perp y luego no poder llenar el spot. Documenta y actualiza
pending_tasks.md. py_compile.
```

## 3.2 — Usar el funding *predicho*, no solo el actual

**Por qué (simple):** el funding que ves ahora es del último pago. Lo que vas a cobrar es el del **próximo** pago, que puede ser distinto. Bybit publica una estimación del próximo. Usar esa estimación te acerca a la realidad de lo que vas a cobrar de verdad.

**Qué cambia:** el bot decide con base en lo que va a pasar en el próximo pago, no en lo que pasó en el anterior. Mejores decisiones de entrada.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Investiga qué campo del ticker de Bybit V5 corresponde al funding del PRÓXIMO
settlement (predicho) vs el último aplicado. Si existe un campo de funding
estimado/próximo, úsalo en data/scanner.py para el ranking y en PositionSizer
para la decisión de viabilidad, en lugar del fundingRate del último periodo.
Si Bybit solo expone el actual, documenta esa limitación en decisions.md y deja
el código preparado para enchufar el predicho cuando esté disponible.
Actualiza pending_tasks.md. py_compile.
```

## 3.3 — Abrir varias posiciones a la vez (aprovechar el capital)

**Por qué (simple):** hoy el bot abre solo la mejor oportunidad por ciclo y deja capital parado. Repartir entre las 2–3 mejores reduce el riesgo de depender de un solo par y pone a trabajar más capital. (Tu límite `MAX_OPEN_POSITIONS=3` ya existe; falta abrir el Top N de golpe, no de a uno.)

**Qué cambia:** en vez de una posición, el bot puede tener varias buenas a la vez, diversificando de dónde sale el funding.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Refactoriza main.py trading_loop para abrir el Top N de oportunidades viables
en un mismo ciclo (hasta llenar MAX_OPEN_POSITIONS), no solo el Top 1:
- Filtra del ranking los símbolos ya abiertos y los que estén en cooldown.
- Reparte el capital disponible entre las plazas libres (capital_por_trade =
  capital_total / plazas_libres, respetando min_notional).
- Abre secuencialmente las viables hasta llenar el límite.
Mantén intacta la verificación de fill por posición. Documenta y actualiza
pending_tasks.md. py_compile.
```

---

# BLOQUE 4 — Mejoras visuales (frontend)

## 4.1 — Panel de "¿estoy ganando dinero?" (lo más útil de ver)

**Por qué (simple):** lo primero que querrás mirar cada día es: cuánto llevo ganado, cuánto funding cobré, cuánto pagué en comisiones. Un panel claro con eso responde tu pregunta de fondo de un vistazo.

**Qué cambia:** abres el dashboard y ves tu ganancia neta acumulada, una curva de cómo creció, y el desglose funding vs comisiones — sin abrir Bybit.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar (frontend/).

Crea un panel de PnL en el dashboard que consuma /api/portfolio (depende del
tracking real del bloque 1.1):
- KPI grande: PnL neto acumulado (USDT), en verde/rojo según signo.
- Desglose: funding total cobrado vs comisiones totales pagadas.
- Curva de equity simple (balance total a lo largo del tiempo) usando los
  cierres registrados en SQLite. Usa una librería de charts ligera.
- Distingue claramente "realizado" (posiciones cerradas) de "no realizado".
Mantén el stack Tailwind V4 sin archivos de config legacy. Actualiza
pending_tasks.md.
```

## 4.2 — Historial de operaciones y alertas visibles

**Por qué (simple):** necesitas ver qué hizo el bot mientras no mirabas — qué abrió, qué cerró, qué falló. Y cuando una posición necesita intervención manual, tiene que saltarte a la vista, no esconderse.

**Qué cambia:** una tabla con el historial de trades y un aviso visual fuerte cuando algo requiere tu atención.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar (frontend/).

Añade al dashboard:
1. Una tabla de historial de operaciones leyendo la tabla trades vía un nuevo
   endpoint read-only GET /api/trades (paginado, más recientes primero):
   símbolo, lado, qty, categoría, timestamp, orderId.
2. Un banner/alerta visual agresiva (rojo, fija arriba) cuando alguna posición
   tenga requires_manual_intervention=true, con el símbolo y el motivo visibles.
Crea el endpoint /api/trades en api.py (read-only, con la auth opcional
existente). Actualiza pending_tasks.md.
```

---

# BLOQUE 5 — Machine Learning / Deep Learning (con honestidad)

> **Lee esto antes:** como tu estrategia es delta-neutral, **el precio te da igual**.
> Por eso, predecir el precio (lo que enseñan casi todos los tutoriales de "ML para cripto")
> **no te sirve de nada aquí**. Donde el ML sí ayuda es en predecir el **funding** y la
> **liquidez**. Estos prompts son del más útil al más opcional. Ninguno es indispensable para
> ganar dólares —el negocio funciona sin ML— pero el primero sí puede mejorar tus entradas.

## 5.1 — Predecir si el funding va a *durar* (el ML que de verdad ayuda)

**Por qué (simple):** hoy el bot entra si el funding está alto *ahora*. Pero el funding cambia cada 8h. Si entras cuando está alto y al rato cae a cero, pagaste comisiones para nada. Un modelo que aprenda del histórico a estimar "¿este funding seguirá pagando los próximos 1–2 días?" te deja entrar solo en los que probablemente sí pagan, y saltarte las trampas. Esto encaja con lo que ya sabes de tu bootcamp (series de tiempo, clasificación).

**Qué cambia:** el bot deja de reaccionar al instante y empieza a entrar con criterio sobre la *persistencia* del funding. Menos entradas que mueren rápido, más que llegan a ser rentables.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Crea un módulo ml/funding_forecaster.py (separado del motor) que:

1. Recolección de datos: un script que consulte el histórico de funding de
   Bybit (session.get_funding_rate_history) para los símbolos USDT-perp y lo
   guarde en SQLite (tabla funding_history: symbol, funding_rate, timestamp).
   Diseñado para correr periódicamente y acumular dataset.
2. Features simples por símbolo: funding actual, media móvil de N periodos,
   volatilidad del funding, nº de periodos consecutivos positivos, tendencia.
3. Modelo de clasificación (empieza simple: GradientBoosting/LogReg de
   scikit-learn, NO deep learning todavía) que prediga la probabilidad de que
   el funding siga por encima del break-even durante los próximos K periodos.
4. Expón una función predict_persistence(symbol) -> probabilidad [0,1].
5. NO lo conectes aún al motor de trading. Déjalo como módulo independiente con
   un script de evaluación (accuracy, precision/recall) para que se pueda medir
   si realmente predice algo antes de confiarle decisiones.

Documenta en decisions.md el diseño y la advertencia: el modelo solo se conecta
al PositionSizer cuando demuestre métricas decentes en backtest. Actualiza
pending_tasks.md. Añade dependencias a requirements.txt (scikit-learn, etc.).
```

## 5.2 — Clasificador de liquidez (predecir qué pares sí van a llenar)

**Por qué (simple):** el dolor que ya viviste fue intentar abrir pares sin liquidez spot. Un modelo simple que mire la profundidad del libro y el volumen, y prediga "este par sí va a llenar / no", evita esos intentos fallidos mejor que una regla fija.

**Qué cambia:** el bot aprende a reconocer pares "operables de verdad" y descarta los fantasma antes de gastar comisiones.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Crea ml/liquidity_classifier.py (módulo independiente):
1. Recolecta features de liquidez por símbolo: volumen 24h, profundidad del
   orderbook spot (suma de bids/asks en X niveles), spread, y si históricamente
   las órdenes llenaron (puedes usar los fallos de fill ya registrados como
   etiquetas negativas).
2. Entrena un clasificador simple (scikit-learn) que prediga probabilidad de
   fill exitoso del spot.
3. Expón predict_fillable(symbol) -> probabilidad.
4. Módulo independiente, con script de evaluación. No conectar al motor hasta
   validar métricas.

Esto complementa el pre-chequeo de liquidez (3.1): la regla dura es la red de
seguridad, el modelo es el filtro inteligente. Documenta y actualiza
pending_tasks.md. requirements.txt si aplica.
```

## 5.3 — Detección de anomalías en el funding (evitar trampas)

**Por qué (simple):** a veces el funding se dispara raro (manipulación, eventos extraños). Entrar ahí puede ser una trampa: cobras una vez y luego se desploma o se vuelve negativo. Un detector de anomalías marca esos picos sospechosos para que el bot los trate con cuidado.

**Qué cambia:** el bot distingue "funding alto sano y sostenido" de "pico raro que huele a trampa", y evita los segundos.

```
Lee claude.md, decisions.md y pending_tasks.md antes de empezar.

Crea ml/anomaly_detector.py (independiente): usando el funding_history del
prompt 5.1, entrena un detector de anomalías simple (z-score sobre la
distribución histórica del símbolo, o IsolationForest de scikit-learn) que
marque cuándo el funding actual es un outlier sospechoso respecto a su propio
histórico. Expón is_anomalous(symbol, funding_rate) -> bool + score.
Módulo independiente con script de evaluación. No conectar al motor todavía.
Documenta la idea: un pico anómalo no es necesariamente bueno; puede colapsar
en el próximo periodo. Actualiza pending_tasks.md.
```

> **Lo que NO recomiendo hacer (para que no pierdas tiempo):**
> - Predicción de precio con LSTM/redes neuronales: inútil aquí, eres delta-neutral.
> - Reinforcement Learning para "trading automático": altísima complejidad, riesgo
>   real de que aprenda a perder dinero de formas creativas, y para esta estrategia
>   no aporta sobre las reglas claras que ya tienes. Sáltatelo.
> - Deep learning en general: empieza con modelos simples (scikit-learn). Solo
>   considera redes si los simples ya funcionan y te quedas corto — cosa que para
>   funding/liquidez es improbable.

---

## Orden recomendado para ver dólares cuanto antes

1. **1.1 PnL real** — sin esto vuelas a ciegas, no sabrías si ganas.
2. **1.2 Cooldown** — deja de quemar comisiones en bucle.
3. **1.3 Calibración mainnet** — para que el bot abra oportunidades reales.
4. **3.1 Pre-chequeo de liquidez** — menos rollbacks, menos fees.
5. **1.4 Tests** — antes de poner capital real.
6. **4.1 Panel de PnL** — para ver tu ganancia de un vistazo.
7. Recién entonces: **5.1 funding forecaster** y el resto de ML como mejora incremental.

La verdad sin adornos: con funding arbitrage en mainnet, con capital modesto, después de comisiones, hablamos de rendimientos del orden de un dígito o dígito bajo doble anual sobre el capital desplegado, y solo cuando hay buenas oportunidades. "Algunos dólares" es un objetivo realista; el ML afina los márgenes pero el negocio lo hace la ejecución limpia y el control de costos —que es justo lo que ya blindaste.
