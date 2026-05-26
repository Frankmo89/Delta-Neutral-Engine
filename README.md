# Funding Rate Arbitrage Bot — Bybit

Bot de arbitraje estadístico **Delta-Neutral** basado en Funding Rate para Bybit.  
La estrategia consiste en tomar una posición larga en spot y corta en perpetuo (o viceversa) para capturar el funding rate de forma neutral al mercado.

## Estructura del proyecto

```
funding_bot/
├── .env.example          # Plantilla de variables de entorno
├── .gitignore
├── requirements.txt
├── config/
│   └── settings.py       # Carga de config desde .env
├── core/
│   ├── exchange.py       # Wrapper de conexión a Bybit
│   └── order_manager.py  # Ejecución simultánea Spot / Perp
├── data/
│   ├── scanner.py        # Escaneo y ranking de funding rates
│   └── websockets.py     # Feed de precios en tiempo real (async)
├── risk/
│   └── position_sizer.py # Validación de tamaño, leverage y fricción
└── scripts/
    ├── calcular_viabilidad.py  # Calculadora de break-even offline
    └── test_conexion.py        # Prueba de conectividad con Testnet
```

## Instalación

```bash
# 1. Clonar el repositorio
git clone <repo_url> && cd funding_bot

# 2. Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar credenciales
cp .env.example .env
# Editar .env con tus API keys de Bybit Testnet
```

## Uso rápido

```bash
# Verificar conexión con Testnet
python scripts/test_conexion.py

# Calcular viabilidad de un par (sin conexión)
python scripts/calcular_viabilidad.py

# Escanear funding rates en vivo
python -c "from data.scanner import FundingRateScanner; ..."
```

## Seguridad

- **Nunca** subas el archivo `.env` al repositorio.
- Usa siempre Testnet para pruebas antes de operar en producción.
- Revisa los límites de rate-limit de la API antes de escalar.

## Advertencia

Este software es únicamente con fines educativos. El trading algorítmico implica riesgo de pérdida de capital. Úsalo bajo tu propia responsabilidad.
