"""Quién está en línea: la última vez que la pantalla de cada jugador avisó que sigue abierta.

La pantalla consulta sus avisos (`GET /social/avisos`) cada pocos segundos mientras el jugador tiene la aplicación a la vista, y esa consulta
es la señal. Un jugador está «en línea» si la última fue hace menos de `SEGUNDOS_EN_LINEA`. Es memoria del proceso a propósito: dura segundos y no
vale la pena escribirla en la base (si el servidor se reinicia, todos vuelven a aparecer en cuanto su pantalla consulte). El backend corre en un
solo proceso (`deploy/Dockerfile`).
"""
from __future__ import annotations

import time

SEGUNDOS_EN_LINEA = 45
"""Una pantalla que no consultó hace más que esto se considera desconectada (consulta cada 4 segundos; el margen cubre una pestaña dormida)."""


def _instante() -> float:
    """El instante actual. Aparte para que los tests lo adelanten sin tocar el reloj de todo el proceso."""
    return time.monotonic()


_visto: dict[int, float] = {}
"""`id de usuario` -> instante de la última señal."""


def marcar(usuario_id: int) -> None:
    """Anota que el jugador está en la aplicación en este momento."""
    _visto[usuario_id] = _instante()


def esta_en_linea(usuario_id: int) -> bool:
    """`True` si el jugador dio señal hace menos de `SEGUNDOS_EN_LINEA`."""
    ultima = _visto.get(usuario_id)
    return ultima is not None and _instante() - ultima < SEGUNDOS_EN_LINEA


def olvidar(usuario_id: int) -> None:
    """Borra lo anotado de un usuario (cuando se elimina su cuenta)."""
    _visto.pop(usuario_id, None)
