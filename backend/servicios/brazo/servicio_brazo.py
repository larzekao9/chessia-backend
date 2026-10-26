"""Conecta la estrategia de jugada activa con el ejecutor de movimiento físico
(brazo simulado en PyBullet o Dobot real), sin que `servicio_partida.mover`
tenga que saber nada de Strategy/Factory de `ejecutor_movimiento.py`
(PLAN_IMPLEMENTACION_COMPLETO.md, sección 4.1/4.2; HU9).

Solo ejecuta la jugada de la estrategia (motor o modelo), nunca la del
humano — esa ya se jugó a mano sobre el tablero físico real (por eso
`mover_desde_foto` puede detectarla con la cámara); pedirle al brazo que la
"reproduzca" agarraría una pieza que ya no está en esa casilla.
"""
from __future__ import annotations

import logging
import os

import chess

from backend.modelos.partida import Partida
from backend.servicios.brazo import control_brazo
from backend.servicios.brazo.calibracion_brazo import cargar_calibracion_brazo, cargar_parametros_agarre
from backend.servicios.brazo.ejecutor_movimiento import EjecutorMovimiento
from backend.servicios.brazo.fabrica_ejecutores import crear_ejecutor_movimiento

logger = logging.getLogger(__name__)

_ejecutor: EjecutorMovimiento | None = None
_ultima_jugada: str | None = None


def _calibracion_del_brazo() -> dict:
    """Argumentos de `EjecutorReal` que salen de la calibración local (`calibracion_local/brazo.json`).

    Raises:
        RuntimeError: si todavía no se calibró el brazo (no se mueve nada sin calibración).
    """
    calibracion = cargar_calibracion_brazo()
    parametros = cargar_parametros_agarre()
    if calibracion is None or parametros is None:
        raise RuntimeError(
            "El brazo real no está calibrado: corré scripts/importar_puntos_dobotstudio.py y guardá los parámetros de agarre "
            "(ver docs/bitacora_brazo_robotico.md)."
        )
    return {
        "posiciones_casillas": calibracion.posiciones_casillas,
        "orientacion": calibracion.orientacion,
        "angulos_cero": calibracion.angulos_cero,
        "parametros": parametros,
    }


def _obtener_ejecutor() -> EjecutorMovimiento:
    """Crea, una sola vez por proceso (cacheado a nivel módulo), el ejecutor
    de movimiento configurado para todo el backend.

    El modo sale de `AJEDREZ_MODO_BRAZO` — `"simulado"` por defecto. Nunca
    `"real"` por defecto (regla 3 de las reglas del proyecto: el brazo físico solo se
    mueve en el laboratorio y con sus precauciones). En modo `"real"` el host del
    Dobot sale de `AJEDREZ_BRAZO_HOST` y la calibración y los parámetros de agarre de
    `calibracion_local/brazo.json`; si falta alguno, no se mueve nada.
    """
    global _ejecutor
    if _ejecutor is not None:
        return _ejecutor

    modo = os.environ.get("AJEDREZ_MODO_BRAZO", "simulado")
    kwargs: dict = {}
    if modo == "real":
        kwargs["host"] = os.environ["AJEDREZ_BRAZO_HOST"]
        puerto_dashboard = os.environ.get("AJEDREZ_BRAZO_PUERTO_DASHBOARD")
        if puerto_dashboard is not None:
            kwargs["puerto_dashboard"] = int(puerto_dashboard)
        kwargs.update(_calibracion_del_brazo())
        kwargs["velocidad_global"] = kwargs["parametros"].velocidad_global
    if modo == "real":
        kwargs["reintentos_agarre"] = 1  # regla de negocio: un solo reintento de agarre, después incidente
    ejecutor = crear_ejecutor_movimiento(modo, **kwargs)
    if modo == "real":
        ejecutor.conectar()
    _ejecutor = ejecutor
    return _ejecutor


def ejecutar_jugada_fisica(tablero_antes: chess.Board, jugada: chess.Move) -> None:
    """Ejecuta `jugada` en el brazo y deja pasar cualquier error (a diferencia de `ejecutar_respuesta_en_brazo`, que lo devuelve como texto).

    La usa `partida_fisica`, que necesita saber exactamente qué falló para pasar a incidente. Si hay un brazo conectado en el panel de control (real o emulador) usa ese;
    si no, y solo con `AJEDREZ_MODO_BRAZO=real`, el ejecutor real de esa variable; en cualquier otro caso falla (no hay brazo).
    """
    global _ultima_jugada
    if control_brazo.conectado():  # el brazo (o el emulador) que se conectó en el panel de control: misma conexión y mismos parámetros
        control_brazo.ejecutar_jugada(tablero_antes, jugada)
    elif os.environ.get("AJEDREZ_MODO_BRAZO", "simulado") == "real":
        _obtener_ejecutor().ejecutar_movimiento(tablero_antes, jugada)
    else:
        # Sin brazo conectado no se finge que se movió algo (el ejecutor «simulado» no mueve nada real): se avisa.
        raise RuntimeError("No hay ningún brazo conectado: conectá el brazo real (o el emulador) en «Control del brazo», habilitalo y volvé a intentar.")
    _ultima_jugada = jugada.uci()


def ejecutar_respuesta_en_brazo(
    partida: Partida, tablero_antes: chess.Board, jugada: chess.Move
) -> str | None:
    """Ejecuta en el brazo la jugada de respuesta que ya decidió la estrategia activa.

    No hace nada (devuelve `None` de inmediato) si `partida.usa_brazo` es
    `False` — la mayoría de las partidas.

    `servicio_partida.mover` llama a esto ya con la jugada de la estrategia
    resuelta (Stockfish o el modelo ya decidieron, sin errores): un fallo acá
    es siempre del brazo (desconectado, calibración sin cargar, captura
    todavía no implementada), nunca de la jugada en sí, y no debe romper el
    juego digital ni la respuesta HTTP — cualquier excepción se loguea y se
    devuelve como mensaje en vez de propagarse.

    Returns:
        `None` si no hubo error (incluido el caso `usa_brazo=False`); el
        mensaje del error en caso contrario.
    """
    global _ultima_jugada
    if not partida.usa_brazo:
        return None
    try:
        ejecutor = _obtener_ejecutor()
        ejecutor.ejecutar_movimiento(tablero_antes, jugada)
    except Exception as error:
        logger.exception(
            "Fallo al ejecutar en el brazo la jugada de respuesta de la partida %s", partida.id
        )
        return str(error)
    _ultima_jugada = jugada.uci()
    return None


def estado_brazo() -> dict:
    """Estado del brazo para `GET /brazo/estado`. No crea el ejecutor ni abre conexiones.

    Returns:
        `{modo, host, conectado, ultima_jugada}`: `host` y `conectado` son `None` en modo `simulado`; en `real`, `conectado` indica si
        ya hay un ejecutor cacheado.
    """
    modo = os.environ.get("AJEDREZ_MODO_BRAZO", "simulado")
    es_real = modo == "real"
    return {
        "modo": modo,
        "host": os.environ.get("AJEDREZ_BRAZO_HOST") if es_real else None,
        "conectado": (_ejecutor is not None) if es_real else None,
        "ultima_jugada": _ultima_jugada,
    }
