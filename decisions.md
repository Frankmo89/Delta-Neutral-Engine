# Registro de Decisiones y Lecciones de IA

Fecha: 24 de mayo de 2026.

Tema: Frontend y Tailwind.

Decisión: Migramos a Tailwind V4 nativo con @tailwindcss/vite.

Regla: NUNCA generar ni utilizar archivos postcss.config.js o tailwind.config.js en este proyecto, ya que pertenecen a la V3 heredada y rompen la compilación y los estilos en Vite. Todo el CSS base debe ir en index.css usando @import "tailwindcss";.

Fecha: 25 de mayo de 2026.

Tema: Recuperación de cierres parciales en Delta-Neutral.

Decisión: Si `close_delta_neutral` cierra solo una pata, se reintenta EXCLUSIVAMENTE la pata fallida hasta `MAX_RETRIES` recalculando qty/balance en cada intento para evitar stale state.

Regla: Si el cierre sigue fallando tras reintentos, `PositionMonitor` debe bloquear el símbolo en `_open_symbols` y marcarlo como intervención manual para impedir reaperturas automáticas inseguras.

Fecha: 25 de mayo de 2026.

Tema: Identificación de posiciones propias antes de SQLite (P6).

Decisión: Todas las órdenes del bot se firman con `orderLinkId` usando `BOT_ORDER_PREFIX` para trazabilidad (`FBOT-{symbol}-{uuid8}`). Como `get_positions` de Bybit V5 no garantiza exponer el `orderLinkId` original, el bot mantiene un registro en memoria (`OrderManager._bot_positions`) con los símbolos que abrió exitosamente.

Regla: `PositionMonitor` solo puede cerrar símbolos presentes en ese registro (más los bloqueados por intervención manual). Posiciones manuales o externas deben ignorarse hasta que la fuente de verdad pase a SQLite en P8.

Fecha: 25 de mayo de 2026.

Tema: Resiliencia de red e idempotencia de órdenes (P7).

Decisión: Todas las lecturas REST de Bybit pasan por `BybitExchange` con retry y backoff exponencial (`MAX_NETWORK_RETRIES`, `BACKOFF_FACTOR`). Para `place_order`, no se usa retry ciego: tras timeout/error transitorio se consulta `open_orders`/`order_history` por `orderLinkId` antes de reintentar.

Regla: Si no se encuentra la orden por `orderLinkId`, recién entonces se reenvía con backoff. Si se encuentra, se considera éxito idempotente y se prohíbe reenviar para evitar duplicados.

Fecha: 25 de mayo de 2026.

Tema: Persistencia de estado en SQLite (P8).

Decisión: Se reemplaza la memoria volátil de símbolos gestionados por una fuente de verdad persistente en SQLite (`bot_positions` con `status='open'`) y se registra el histórico de órdenes en `trades`.

Regla: `PositionMonitor` debe leer símbolos abiertos desde la base SQLite en cada ciclo para recuperar estado tras reinicios; no depender de estructuras en memoria para la propiedad de posiciones.

Fecha: 25 de mayo de 2026.

Tema: Alertas push opcionales por Telegram (P19).

Decisión: El canal de alertas usa `TelegramNotifier` asíncrono sobre stdlib (`urllib` en thread) para evitar dependencias nuevas. El notificador se degrada en silencio si faltan `TELEGRAM_BOT_TOKEN` o `TELEGRAM_CHAT_ID`.

Regla: Las alertas push solo deben conectarse a eventos críticos o de observabilidad operativa inmediata: activación de rollback, fallo definitivo de cierre y arranque opcional del bot. La ausencia de credenciales nunca puede romper el motor.

Fecha: 25 de mayo de 2026.

Tema: Separación de procesos bot/API y cierre de superficie web (P10).

Decisión: `main.py` y `api.py` se ejecutan como procesos independientes. La API deja de arrancar el motor y pasa a ser read-only: lee posiciones desde SQLite y expone scanner/estado para la UI. CORS deja de ser abierto y se restringe a `CORS_ORIGINS` con default `http://localhost:5173`.

Regla: Nunca volver a acoplar el motor de trading al lifespan de FastAPI. Cualquier endpoint web debe consumir estado persistido o lecturas read-only, no mutar ni arrancar el bot.

Fecha: 25 de mayo de 2026.

Tema: Feed de mercado Push con fallback Pull (P14).

Decisión: `FundingTickerCache` (WS linear por lotes) pasa a ser la fuente primaria para funding/price en `FundingRateScanner` y `PositionMonitor`. El fallback a REST se activa solo si el cache está vacío o stale (`WS_TICKER_STALE_SECONDS`).

Regla: Las decisiones de trading nunca deben depender exclusivamente del WS. Si el push falla, el sistema debe degradar automáticamente al camino REST robustecido (P7) sin detener el bot ni la API.

Fecha: 25 de mayo de 2026.

Tema: Semántica visual de funding y countdown operativo en frontend (P21/P22).

Decisión: La tabla del scanner usa una escala semántica de funding (verde para tasas favorables, rojo para riesgo/negativas, gris para neutral) y una barra de magnitud proporcional para lectura instantánea. Además, el frontend renderiza un countdown live (`hh:mm:ss`) al próximo funding usando `nextFundingTime` del stream WS.

Regla: La UI de trading debe priorizar señales visuales rápidas sobre lectura numérica cruda, y todo estilo debe permanecer en utilidades Tailwind V4 (sin archivos legacy de configuración).

Fecha: 25 de mayo de 2026.

Tema: Monitor de posiciones enriquecido y alerta operativa (P23).

Decisión: La sección de posiciones activas migra a tabla institucional con `unrealized_pnl`, `breakeven_periods` y estado de intervención. El estado de intervención se persiste en SQLite (`requires_manual_intervention`, `intervention_reason`) para que la API read-only y el frontend lo reflejen incluso en procesos separados.

Regla: Si una posición requiere intervención manual, la UI debe destacarla con señal visual agresiva (fila en alerta + badge crítico + motivo visible) y nunca ocultar ese estado por reinicio del frontend o de la API.

Fecha: 25 de mayo de 2026.

Tema: Tooltips flotantes y stacking context en tablas.

Decisión: Todo tooltip renderizado dentro de tablas o layouts densos debe usar z-index alto (`z-[9999]`) en el panel flotante y un ancla relativa con prioridad (`z-[100]`) para evitar quedar detrás de filas, botones o capas adyacentes.

Regla: Nunca envolver tablas con tooltips en contenedores que recorten verticalmente (`overflow-hidden`/`overflow-y-hidden`). Para conservar scroll horizontal sin cortar overlays, usar `overflow-x-auto` + `overflow-y-visible`.

Fecha: 25 de mayo de 2026.

Tema: Límite concurrente y limpieza forzada de posiciones.

Decisión: `MAX_OPEN_POSITIONS` pasa a ser una setting explícita con default 3. El motor ya no se detiene al detectar cualquier posición abierta; solo rechaza nuevas oportunidades cuando alcanza ese límite y deja un log info operativo con el conteo actual. Para intervención manual extrema, la API expone `DELETE /api/positions/{symbol}/force`, que elimina el registro de SQLite sin enviar órdenes al exchange.

Regla: La liberación del candado de intervención debe reconciliarse contra SQLite en `PositionMonitor`; si un símbolo fue borrado de `bot_positions`, cualquier lock en memoria debe purgarse automáticamente en el siguiente ciclo. El botón de frontend `Forzar Limpieza DB` solo debe mostrarse para posiciones en intervención manual.

Fecha: 25 de mayo de 2026.

Tema: Cierre de patas independientes.

Decisión: `close_delta_neutral` no puede abortar el cierre completo cuando una pata queda en `qty=0` por expiración externa, dust o descalce operativo. Si `qty_spot=0` y `qty_linear>0`, se cierra solo linear con `reduceOnly=True`. Si `qty_linear=0` y `qty_spot>0`, se cierra solo spot. En ambos casos se persiste cierre en SQLite para evitar bloqueos y shorts desnudos.

Regla: Solo abortar cuando ambas patas están en `qty=0` (no hay nada que ejecutar). Los logs críticos deben distinguir explícitamente `spot expirado/externo` o `linear cerrada/externa` de un `fallo real` de ejecución para acelerar diagnóstico en producción.

Fecha: 25 de mayo de 2026.

Tema: set_leverage obligatorio antes de apertura.

Decisión: `open_delta_neutral` llama a `set_leverage` vía `run_in_executor` justo antes del `asyncio.gather` de órdenes. retCode 110043 (`leverage not modified`) se trata como éxito silencioso. Cualquier otro error loguea warning pero no aborta la apertura: el exchange ya tiene algún leverage configurado y operar con él es mejor que perder la oportunidad.

Regla: Nunca confiar en el leverage por defecto del exchange (habitualmente 10x en Bybit). `settings.leverage` (default 1) debe forzarse antes de cada apertura para garantizar el comportamiento Delta-Neutral sin riesgo de liquidación amplificada en mainnet.

Fecha: 25 de mayo de 2026.

Tema: retCode=0 es aceptación, no fill — verificar balance real antes de declarar cobertura delta-neutral.

Decisión: `open_delta_neutral` no puede declarar "Cobertura perfecta" ni persistir en SQLite basándose únicamente en retCode=0 de Bybit. retCode=0 confirma que la orden fue **aceptada** por el exchange, no que se **ejecutó**. En Testnet (y en Mainnet con liquidez nula) una Market Buy de Spot puede aceptarse sin llenarse jamás, dejando el bot con un short desnudo sin la pata compradora.

Fix: tras el gather de apertura, se hace polling del balance real del base coin (hasta `MAX_FILL_CHECK_ATTEMPTS` veces con `FILL_CHECK_DELAY_SECONDS` de espera entre polls) y se verifica que el delta `(balance_after - balance_before) >= qty * SPOT_FILL_TOLERANCE`. Para la pata linear se consulta `_get_linear_short_size` para confirmar que el short quedó registrado. Solo si AMBAS patas superan la verificación se persiste la posición en SQLite. Si cualquiera falla, se fuerza rollback de la pata que sí ejecutó.

Regla: NUNCA asumir que retCode=0 implica fill ejecutado en mercados de spot con baja liquidez. Siempre verificar balance/posición real antes de registrar cobertura.
Fecha: 25 de mayo de 2026.

Tema: Verificaci\u00f3n de integridad del hedge en el monitor \u2014 el funding viable no implica hedge intacto.

Decisi\u00f3n: `PositionMonitor.check_active_positions` eval\u00faa cada posici\u00f3n short con una verificaci\u00f3n de integridad del hedge ANTES de la evaluaci\u00f3n de viabilidad de funding. Un short perpetuo sin la pata spot correspondiente (balance < `qty * HEDGE_INTEGRITY_TOLERANCE`) es exposici\u00f3n direccional desnuda y debe cerrarse de inmediato, sin importar si el funding es saludable. La pata linear se cierra v\u00eda `close_delta_neutral` que con el fix de patas independientes ya gestiona el caso `qty_spot=0`. Si el cierre falla, el s\u00edmbolo queda bloqueado para intervenci\u00f3n manual.

Regla: Funding saludable \u2260 cobertura intacta. El monitor debe verificar la integridad del hedge en cada ciclo antes de evaluar rentabilidad. Un hedge roto es una emergencia operativa que tiene prioridad sobre cualquier criterio de viabilidad econ\u00f3mica.