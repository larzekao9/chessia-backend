"""Arranca el backend (FastAPI) de forma que se pueda cerrar con Ctrl+C.

    python scripts/iniciar_backend.py                # 127.0.0.1:8000
    python scripts/iniciar_backend.py --host 0.0.0.0 # visible en la red del laboratorio

Con `uvicorn backend.main:app` a secas, Ctrl+C se queda en «Waiting for connections to close» mientras haya un video en vivo
abierto (`/vision/flujo`): el video no termina nunca por sí solo y, desde Python 3.12, uvicorn espera a que se cierren todas las
conexiones aunque se apriete Ctrl+C otra vez. Acá se fija un tiempo máximo de cierre (`SEGUNDOS_CIERRE`): pasado ese tiempo se cortan
las conexiones que sigan abiertas y el proceso termina.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

RAIZ = Path(__file__).resolve().parent.parent
SEGUNDOS_CIERRE = 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Arranca el backend con cierre rápido.")
    parser.add_argument("--host", default="127.0.0.1", help="dirección en la que escucha (por defecto, solo esta máquina)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="reinicia solo al cambiar el código (desarrollo)")
    argumentos = parser.parse_args()
    uvicorn.run(
        "backend.main:app",
        host=argumentos.host,
        port=argumentos.port,
        reload=argumentos.reload,
        app_dir=str(RAIZ),
        timeout_graceful_shutdown=SEGUNDOS_CIERRE,
    )


if __name__ == "__main__":
    main()
