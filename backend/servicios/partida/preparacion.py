"""Lista de verificación antes de empezar una partida física: ¿está todo listo (cámara, brazo, Turing, Stockfish)?

Es SOLO LECTURA: no mueve el brazo, no cambia ningún parámetro, no abre la cámara. Cada punto sale como `{id, etiqueta, estado, detalle, brazo}`:

- `ok`: listo. `error`: no se puede jugar así. `aviso`: conviene mirarlo pero no frena. `info`: dato para saber (por ejemplo, la velocidad con que está el brazo).
- `brazo: true` marca lo que solo importa si la partida usa el brazo (la pantalla los exige solo en ese caso).

Lo único «caro» son las pruebas de Turing y de Stockfish (una jugada de cada uno): se hacen una vez y se recuerdan unos segundos, porque la pantalla pregunta seguido.
"""
from __future__ import annotations

import logging
import threading
import time

import chess

from backend.servicios.brazo import control_brazo
from backend.servicios.brazo.calibracion_brazo import cargar_calibracion_brazo, cargar_parametros_agarre
from backend.servicios.vision import camara, tablero

logger = logging.getLogger(__name__)

SEGUNDOS_RECORDAR_BIEN = 60.0
SEGUNDOS_RECORDAR_MAL = 8.0
_FEN_DE_PRUEBA = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"  # las negras responden a 1. e4
_recordado: dict[str, tuple[float, dict]] = {}
_cerrojo = threading.Lock()


def _item(id_: str, etiqueta: str, estado: str, detalle: str, brazo: bool = False) -> dict:
    return {"id": id_, "etiqueta": etiqueta, "estado": estado, "detalle": detalle, "brazo": brazo}


def _recordando(clave: str, calcular) -> dict:
    """El resultado de `calcular()` si no está recordado o ya se venció; si no, el recordado."""
    with _cerrojo:
        guardado = _recordado.get(clave)
        if guardado is not None and time.monotonic() < guardado[0]:
            return guardado[1]
    resultado = calcular()
    plazo = SEGUNDOS_RECORDAR_BIEN if resultado["estado"] in ("ok", "info") else SEGUNDOS_RECORDAR_MAL
    with _cerrojo:
        _recordado[clave] = (time.monotonic() + plazo, resultado)
    return resultado


def _olvidar() -> None:
    with _cerrojo:
        _recordado.clear()


# --- cámara ------------------------------------------------------------------------------------------------------------------
def _camara() -> dict:
    esquinas = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas("principal"))
    if esquinas is None:
        return _item("camara", "Cámara: tablero calibrado", "error", "Falta calibrar las esquinas del tablero: hacelo en «Cámara y Brazo».")
    fuente = camara.fuente_actual("principal")
    return _item("camara", "Cámara: tablero calibrado", "ok", f"Esquinas guardadas (cámara {fuente}).")


# --- brazo (solo lectura) ------------------------------------------------------------------------------------------------------
def _brazo() -> list[dict]:
    items: list[dict] = []
    try:
        estado = control_brazo.estado()
    except Exception as error:  # noqa: BLE001 — la verificación nunca tumba la pantalla
        return [_item("brazo_conexion", "Brazo: conectado y habilitado", "error", f"No se pudo leer el brazo: {error}", brazo=True)]

    if not estado.get("conectado"):
        items.append(_item("brazo_conexion", "Brazo: conectado y habilitado", "error", "No está conectado: conectalo en «Cámara y Brazo» → Brazo.", brazo=True))
    elif estado.get("error"):
        items.append(_item("brazo_conexion", "Brazo: conectado y habilitado", "error", f"Conectado pero no responde: {estado['error']}", brazo=True))
    elif estado.get("falla"):
        items.append(_item("brazo_conexion", "Brazo: conectado y habilitado", "error", f"El brazo tiene una falla: {estado['falla']}. Limpiala en «Control del brazo».", brazo=True))
    elif estado.get("modo") != 5:
        items.append(_item("brazo_conexion", "Brazo: conectado y habilitado", "error", f"No está habilitado y libre ({estado.get('modo_texto', 'estado desconocido')}): habilitalo en «Control del brazo».", brazo=True))
    else:
        donde = "emulador" if estado.get("emulador") else "brazo real"
        items.append(_item("brazo_conexion", "Brazo: conectado y habilitado", "ok", f"Conectado ({donde}), habilitado y libre.", brazo=True))
        pinza = estado.get("pinza")
        if pinza is None:
            items.append(_item("brazo_pinza", "Brazo: pinza", "aviso", "La pinza todavía no respondió: inicializala en «Control del brazo» si hace falta.", brazo=True))
        elif not pinza.get("inicializada"):
            items.append(_item("brazo_pinza", "Brazo: pinza", "aviso", "La pinza no está inicializada: inicializala en «Control del brazo».", brazo=True))
        else:
            items.append(_item("brazo_pinza", "Brazo: pinza", "ok", "Inicializada.", brazo=True))
        if estado.get("velocidad_global") is not None:
            items.append(_item("brazo_velocidad", "Brazo: velocidad global", "info", f"{estado['velocidad_global']} % (solo se muestra: acá no se cambia).", brazo=True))

    try:
        calibracion = cargar_calibracion_brazo()
    except ValueError as error:
        items.append(_item("brazo_calibracion", "Brazo: calibración del tablero", "error", f"El archivo de calibración está dañado: {error}", brazo=True))
    else:
        if calibracion is None or not calibracion.posiciones_casillas:
            items.append(_item("brazo_calibracion", "Brazo: calibración del tablero", "error", "Falta calibrar el tablero con el brazo (los 4 puntos a1, h1, a8, h8).", brazo=True))
        else:
            items.append(_item("brazo_calibracion", "Brazo: calibración del tablero", "ok", f"Cargada ({len(calibracion.posiciones_casillas)} casillas).", brazo=True))
    try:
        parametros = cargar_parametros_agarre()
    except ValueError as error:
        items.append(_item("brazo_agarre", "Brazo: parámetros de agarre", "error", f"El archivo está dañado: {error}", brazo=True))
    else:
        items.append(
            _item("brazo_agarre", "Brazo: parámetros de agarre", "ok", "Guardados.", brazo=True) if parametros is not None
            else _item("brazo_agarre", "Brazo: parámetros de agarre", "error", "Faltan los parámetros de agarre (aperturas por pieza, alturas).", brazo=True)
        )
    return items


# --- Turing y Stockfish (una jugada de cada uno) -------------------------------------------------------------------------------------
def _stockfish() -> dict:
    inicio = time.perf_counter()
    try:
        from backend.servicios.motor.motor_ajedrez import NIVEL_MAX, analizar_posicion

        analizar_posicion(_FEN_DE_PRUEBA, NIVEL_MAX, 0.05)
    except Exception as error:  # noqa: BLE001
        return _item("stockfish", "Stockfish (análisis de cada jugada)", "error", f"No responde: {error}")
    return _item("stockfish", "Stockfish (análisis de cada jugada)", "ok", f"Responde ({round((time.perf_counter() - inicio) * 1000)} ms).")


def _turing() -> dict:
    inicio = time.perf_counter()
    try:
        from backend.servicios.estrategias.fabrica_estrategias import crear_estrategia_jugada

        san = crear_estrategia_jugada("modelo", nivel=10).decidir_jugada(_FEN_DE_PRUEBA)
        chess.Board(_FEN_DE_PRUEBA).parse_san(san)  # que la respuesta sea una jugada legal
    except Exception as error:  # noqa: BLE001
        return _item("turing", "Turing (modelo propio)", "error", f"No responde: {error}")
    return _item("turing", "Turing (modelo propio)", "ok", f"Responde ({round((time.perf_counter() - inicio) * 1000)} ms).")


def verificar() -> dict:
    """La lista de verificación completa: `{listo, items}`. `listo` es `False` si algún punto que no es del brazo está en `error` (los del brazo los exige quien sabe si la partida lo usa)."""
    from backend.servicios.partida import partida_fisica

    items = [_item("backend", "Backend", "ok", "Respondiendo."), _camara()]
    items += _brazo()
    if partida_fisica.en_marcha():
        # Con una partida en marcha no se le pide a Turing ni a Stockfish una jugada de prueba: le quitarían tiempo a la partida.
        items += [_item("turing", "Turing (modelo propio)", "info", "Hay una partida en marcha."), _item("stockfish", "Stockfish (análisis de cada jugada)", "info", "Hay una partida en marcha.")]
    else:
        items += [_recordando("turing", _turing), _recordando("stockfish", _stockfish)]
    return {"listo": not any(i["estado"] == "error" and not i["brazo"] for i in items), "items": items}
