"""Señales de vida de los jugadores de una sala con reloj: cuándo fue la última vez que cada uno estuvo en la pantalla de la partida.

Una sala con reloj no se puede dejar (como en Lichess): si un jugador se va (cierra la pestaña, pierde la conexión, bloquea el celular) y pasan más
de `ciclo_vida.SEGUNDOS_AUSENCIA_CON_RELOJ` sin que su pantalla avise, pierde la partida. Cada consulta del estado de la sala y cada jugada de un
jugador sentado cuenta como señal de vida; la pantalla consulta cada segundo o dos.

Es memoria del proceso a propósito (igual que `partida.presencia`, que es la de las partidas contra la IA y se barre por separado): una señal de
vida dura segundos y no vale la pena escribirla en la base. Solo cuenta un jugador del que ya llegó alguna señal; si el servidor se reinicia, nadie
queda ausente hasta que vuelva a avisar. El backend corre en un solo proceso (`deploy/Dockerfile`).
"""
from __future__ import annotations

import time

def _instante() -> float:
    """El instante actual para medir ausencias. Aparte para que los tests lo adelanten sin tocar el reloj de todo el proceso."""
    return time.monotonic()


_ultima_senal: dict[tuple[str, int], float] = {}
"""`(id de partida, id de usuario)` -> instante (`time.monotonic()`) de la última señal de vida."""


def registrar(partida_id: str, usuario_id: int, ahora: float | None = None) -> None:
    """Anota que `usuario_id` está en la pantalla de esta sala en este momento."""
    _ultima_senal[(partida_id, usuario_id)] = _instante() if ahora is None else ahora


def segundos_sin_senal(partida_id: str, usuario_id: int, ahora: float | None = None) -> float | None:
    """Segundos desde la última señal de vida del jugador, o `None` si nunca llegó una."""
    ultima = _ultima_senal.get((partida_id, usuario_id))
    if ultima is None:
        return None
    return (_instante() if ahora is None else ahora) - ultima


def olvidar_sala(partida_id: str) -> None:
    """Borra lo anotado de una sala (cuando ya terminó o se eliminó)."""
    for clave in [clave for clave in _ultima_senal if clave[0] == partida_id]:
        del _ultima_senal[clave]


def olvidar_usuario(usuario_id: int) -> None:
    """Borra lo anotado de un usuario en todas las salas (cuando se elimina su cuenta)."""
    for clave in [clave for clave in _ultima_senal if clave[1] == usuario_id]:
        del _ultima_senal[clave]
