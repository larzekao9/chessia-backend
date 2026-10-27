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
import importlib.util
import sys
from pathlib import Path

import uvicorn

RAIZ = Path(__file__).resolve().parent.parent
SEGUNDOS_CIERRE = 3
DEPENDENCIAS_DEL_BACKEND = ("sqlalchemy", "fastapi", "chess")


def _avisar_si_falta_el_entorno() -> None:
    """Si este Python no tiene las dependencias del backend (casi siempre: se abrió una terminal sin el entorno de conda `ajedrez`), lo dice con claridad y sale."""
    faltan = [nombre for nombre in DEPENDENCIAS_DEL_BACKEND if importlib.util.find_spec(nombre) is None]
    if not faltan:
        return
    print(
        f"\nEste Python ({sys.executable}) no tiene: {', '.join(faltan)}.\n"
        "El backend se corre con el entorno de conda `ajedrez` (ver el README):\n"
        "    conda activate ajedrez\n"
        "    python scripts/iniciar_backend.py\n"
        "o, sin activar nada, con el Python del entorno:\n"
        '    & "$env:USERPROFILE\\miniconda3\\envs\\ajedrez\\python.exe" scripts\\iniciar_backend.py\n',
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    _avisar_si_falta_el_entorno()
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
