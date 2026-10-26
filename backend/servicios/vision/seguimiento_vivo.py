"""El seguimiento de la partida (`seguimiento_jugadas.Seguimiento`) en marcha: uno solo para todo el backend, que avanza un cuadro por cada llamada.

Quien lo usa (la pantalla) llama a `avanzar()` una vez por segundo: no hay ningún hilo ni trabajo en segundo plano, así que si se cierra la pantalla no se
gasta nada. Cada llamada toma el último cuadro de la cámara (la captura continua ya lo tiene a mano), lo compara con el anterior y con la foto base y
devuelve qué pasó (nada, movimiento, una jugada o una alerta).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import chess
import cv2
import numpy as np

from backend.servicios.vision import seguimiento_jugadas, tablero
from backend.servicios.vision.camara import capturar_foto_tablero, esperar_foto_nueva

GUARDAR_FALLOS = not os.environ.get("PYTEST_RUNNING")
"""Cuando el seguimiento da una alerta (posición inválida o jugada dudosa) se guardan las fotos y los cambios por casilla: así se puede ver después por qué falló."""
DIRECTORIO_FALLOS = tablero.ARCHIVO_ESQUINAS.parent / "seguimiento_fallos"
FALLOS_MAXIMOS = 30
_cerrojo = threading.Lock()
_fallo_guardado = False
_ultimo_cuadro = 0
_ultimo_resultado: dict | None = None
_actual: seguimiento_jugadas.Seguimiento | None = None
_camara = "principal"


class SinSeguimiento(Exception):
    """No hay un seguimiento iniciado."""


def _datos(seguimiento: seguimiento_jugadas.Seguimiento, camara: str) -> dict:
    return {
        "activo": True, "camara": camara, "estado": seguimiento.estado, "fen": seguimiento.fen, "jugadas": list(seguimiento.jugadas),
        "turno": "blancas" if chess.Board(seguimiento.fen).turn == chess.WHITE else "negras", "alerta": seguimiento.ultima_alerta,
        "puede_deshacer": bool(seguimiento.historial),
    }


def iniciar(fen: str | None = None, camara: str = "principal", intervalo_s: float = 1.0) -> dict:
    """Empieza a vigilar la partida desde `fen` (por defecto, la posición inicial) con las esquinas calibradas de `camara`, mirándola cada `intervalo_s` segundos
    (de eso salen cuántos cuadros quietos hacen falta: ver `seguimiento_jugadas.ticks_para`).

    Raises:
        ValueError: FEN inválido o la cámara no está calibrada.
    """
    global _actual, _camara, _ultimo_cuadro, _ultimo_resultado
    esquinas = tablero.cargar_esquinas_calibradas(tablero.archivo_esquinas(camara))
    if esquinas is None:
        raise ValueError(f"La cámara {camara} no está calibrada: calibrala antes de seguir una partida")
    chess.Board(fen or chess.STARTING_FEN)  # valida (ValueError)
    with _cerrojo:
        _actual = seguimiento_jugadas.Seguimiento(esquinas, fen or chess.STARTING_FEN, **seguimiento_jugadas.ticks_para(intervalo_s))
        _camara = camara
        _ultimo_cuadro, _ultimo_resultado = 0, None
        return _datos(_actual, camara)


def detener() -> dict:
    global _actual
    with _cerrojo:
        _actual = None
    return {"activo": False}


def estado() -> dict:
    with _cerrojo:
        return _datos(_actual, _camara) if _actual is not None else {"activo": False}


def fijar_posicion(fen: str) -> dict:
    """Cambia la posición conocida (por ejemplo, después de que el robot jugó) y vuelve a tomar la foto base cuando el tablero esté quieto.

    Raises:
        SinSeguimiento: no hay seguimiento. ValueError: FEN inválido.
    """
    with _cerrojo:
        if _actual is None:
            raise SinSeguimiento()
        _actual.fijar_posicion(fen)
        return _datos(_actual, _camara)


def rebasar() -> dict:
    """Vuelve a tomar la foto base con la posición que ya se sabe (las piezas quedaron bien pero la imagen no coincide con la foto).

    Raises:
        SinSeguimiento: no hay seguimiento.
    """
    with _cerrojo:
        if _actual is None:
            raise SinSeguimiento()
        _actual.rebasar()
        return _datos(_actual, _camara)


def deshacer() -> dict:
    """Quita la última jugada detectada (si se detectó mal) y vuelve a tomar la foto base. Devuelve los datos del seguimiento más `deshecha` (el SAN quitado).

    Raises:
        SinSeguimiento: no hay seguimiento. ValueError: no hay jugada que deshacer.
    """
    with _cerrojo:
        if _actual is None:
            raise SinSeguimiento()
        san = _actual.deshacer()
        return {**_datos(_actual, _camara), "deshecha": san}


def vista() -> dict:
    """Cómo decidió el último cuadro (fotos base y actual, cambios por casilla, jugadas candidatas).

    Raises:
        SinSeguimiento: no hay seguimiento.
    """
    with _cerrojo:
        if _actual is None:
            raise SinSeguimiento()
        return _actual.vista()


def _guardar_fallo(seguimiento: seguimiento_jugadas.Seguimiento, resultado: dict) -> None:
    """Guarda (una vez por alerta) la foto base junto a la de ahora y un JSON con los cambios por casilla. Deja solo las últimas `FALLOS_MAXIMOS`."""
    global _fallo_guardado
    analisis = seguimiento.analisis
    if resultado["estado"] not in ("invalida", "dudosa"):
        _fallo_guardado = False
        return
    if _fallo_guardado or analisis is None:
        return
    _fallo_guardado = True
    try:
        DIRECTORIO_FALLOS.mkdir(parents=True, exist_ok=True)
        nombre = time.strftime("%Y%m%d_%H%M%S")
        lado = seguimiento_jugadas.LADO_VISTA
        pares = [cv2.resize(analisis[clave], (lado, lado), interpolation=cv2.INTER_AREA) for clave in ("base", "actual")]
        cv2.imwrite(str(DIRECTORIO_FALLOS / f"{nombre}.jpg"), np.hstack(pares), [cv2.IMWRITE_JPEG_QUALITY, 85])
        puntajes = analisis["puntajes"]
        por_casilla = {} if puntajes is None else {seguimiento_jugadas.NOMBRES[i]: round(float(p), 1) for i, p in enumerate(puntajes) if p >= 2}
        (DIRECTORIO_FALLOS / f"{nombre}.json").write_text(
            json.dumps({"estado": resultado["estado"], "mensaje": resultado["mensaje"], "fen": resultado["fen"], "cambiadas": resultado["cambiadas"], "puntajes": por_casilla,
                        "umbral": seguimiento.cambio_minimo, "evidencia_minima": seguimiento_jugadas.EVIDENCIA_MINIMA}, ensure_ascii=False, indent=1), encoding="utf-8")
        for viejo in sorted(DIRECTORIO_FALLOS.glob("*.json"))[:-FALLOS_MAXIMOS]:
            viejo.unlink(missing_ok=True)
            viejo.with_suffix(".jpg").unlink(missing_ok=True)
    except OSError:
        pass  # guardar la evidencia nunca debe tumbar el seguimiento


def avanzar(con_vista: bool = False, nuevo: bool = False) -> dict:
    """Procesa un cuadro de la cámara y devuelve lo que pasó (ver `Seguimiento.actualizar`) más `activo` y `camara`; con `con_vista`, también `vista`.

    Con `nuevo=True` espera un cuadro que la cámara acabe de entregar (así el seguimiento puede ir a la velocidad de la cámara sin repetir cuadros); si no llegó ninguno
    en un instante, devuelve lo mismo que la vez anterior con `repetido: True` y no procesa nada (quien lo usa tiene que ignorarlo).

    Raises:
        SinSeguimiento: no hay seguimiento. RuntimeError: la cámara no está disponible.
    """
    global _ultimo_cuadro, _ultimo_resultado
    with _cerrojo:
        if _actual is None:
            raise SinSeguimiento()
        seguimiento, camara, anterior = _actual, _camara, _ultimo_cuadro
    numero = anterior
    if nuevo:
        foto = esperar_foto_nueva(anterior, camara)
        if foto is None:
            with _cerrojo:
                if _ultimo_resultado is None:
                    raise RuntimeError("La cámara todavía no entrega cuadros")
                return {**_ultimo_resultado, "repetido": True}
        numero, imagen = foto
    else:
        imagen = capturar_foto_tablero(camara=camara)
    with _cerrojo:
        if _actual is not seguimiento:  # se detuvo o reinició mientras se tomaba el cuadro
            raise SinSeguimiento()
        resultado = {"activo": True, "camara": camara, **seguimiento.actualizar(imagen)}
        resultado["alerta"] = seguimiento.ultima_alerta
        if GUARDAR_FALLOS:
            _guardar_fallo(seguimiento, resultado)
        if con_vista:
            resultado["vista"] = seguimiento.vista()
        _ultimo_cuadro, _ultimo_resultado = numero, {k: v for k, v in resultado.items() if k != "vista"}
        return resultado
