"""Prueba de punta a punta del reconocimiento del tablero completo (HU1, CU01 y CU02).

Recorre el camino real de `reconocer_tablero`: esquinas de la cámara → corrección de perspectiva → 64 recortes → clasificación → FEN. La foto es sintética (un
tablero con piezas de colores distintos fotografiado en perspectiva) y el clasificador se reemplaza por uno que lee el color del centro de cada casilla, de modo
que la prueba no depende del dataset ni de ningún checkpoint entrenado: verifica que cada casilla vaya a su lugar, la orientación y el armado del FEN.
La exactitud con fotos reales se mide aparte (`training/evaluar_reconocimiento.py`).
"""
from __future__ import annotations

import chess
import cv2
import numpy as np
import pytest

from backend.servicios.vision import reconocimiento
from backend.servicios.vision.modelo_piezas import CLASE_VACIA

LADO_CASILLA = 64
TAMANO_FOTO = 900
CLARA, OSCURA = (225, 225, 225), (70, 70, 70)
# Cada pieza se dibuja con un color propio, lejos de los de las casillas, para que el clasificador de prueba la lea sin ambigüedad.
COLORES_PIEZAS = {
    "white-pawn": (0, 0, 255), "white-knight": (0, 128, 255), "white-bishop": (0, 255, 255), "white-rook": (0, 255, 0), "white-queen": (255, 255, 0), "white-king": (255, 0, 0),
    "black-pawn": (128, 0, 255), "black-knight": (255, 0, 128), "black-bishop": (128, 255, 0), "black-rook": (0, 160, 90), "black-queen": (160, 90, 0), "black-king": (90, 0, 160),
}
NOMBRES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
# Dónde quedan a8, h8, h1 y a1 en la foto (orden interno de las esquinas del tablero): una vista en perspectiva, sin cuadrar con la imagen.
ESQUINAS_EN_LA_FOTO = np.array([[150, 110], [760, 150], [700, 780], [90, 700]], dtype=np.float32)


def _foto_del_tablero(fen_piezas: str) -> np.ndarray:
    """Dibuja el tablero de frente (a8 arriba a la izquierda) con una pieza por casilla ocupada y lo pasa a perspectiva."""
    plano = np.zeros((8 * LADO_CASILLA, 8 * LADO_CASILLA, 3), np.uint8)
    tablero = chess.Board(fen_piezas + " w - - 0 1")
    for fila in range(8):
        for columna in range(8):
            casilla = chess.square(columna, 7 - fila)
            x, y = columna * LADO_CASILLA, fila * LADO_CASILLA
            cv2.rectangle(plano, (x, y), (x + LADO_CASILLA, y + LADO_CASILLA), CLARA if (fila + columna) % 2 == 0 else OSCURA, -1)
            pieza = tablero.piece_at(casilla)
            if pieza is not None:
                etiqueta = f"{'white' if pieza.color else 'black'}-{NOMBRES[pieza.symbol().lower()]}"
                cv2.rectangle(plano, (x + 20, y + 20), (x + LADO_CASILLA - 20, y + LADO_CASILLA - 20), COLORES_PIEZAS[etiqueta], -1)
    origen = np.array([[0, 0], [8 * LADO_CASILLA - 1, 0], [8 * LADO_CASILLA - 1, 8 * LADO_CASILLA - 1], [0, 8 * LADO_CASILLA - 1]], dtype=np.float32)
    matriz = cv2.getPerspectiveTransform(origen, ESQUINAS_EN_LA_FOTO)
    return cv2.warpPerspective(plano, matriz, (TAMANO_FOTO, TAMANO_FOTO), borderValue=(40, 90, 140))


def _clasificador_por_color(recorte: np.ndarray) -> str:
    """Clasificador de prueba: lee el color del centro de la casilla y lo busca entre los de las piezas."""
    alto, ancho = recorte.shape[:2]
    centro = recorte[alto // 2 - 3: alto // 2 + 3, ancho // 2 - 3: ancho // 2 + 3].reshape(-1, 3).mean(axis=0)
    etiqueta, color = min(COLORES_PIEZAS.items(), key=lambda par: float(np.linalg.norm(centro - np.array(par[1]))))
    return etiqueta if np.linalg.norm(centro - np.array(color)) < 60 else CLASE_VACIA


@pytest.fixture
def camara_calibrada(monkeypatch):
    """Cámara con las esquinas ya marcadas (como en la estación), recorte plano de cada casilla y clasificador de prueba."""
    monkeypatch.setattr(reconocimiento, "cargar_esquinas_calibradas", lambda *_args, **_kwargs: ESQUINAS_EN_LA_FOTO.copy())
    monkeypatch.setattr(reconocimiento, "modo_recorte", lambda: "justo")
    monkeypatch.setattr(reconocimiento, "clasificar_pieza", _clasificador_por_color)


def test_posicion_inicial_en_perspectiva_se_reconoce_casilla_por_casilla(camara_calibrada) -> None:
    fen_piezas = chess.STARTING_BOARD_FEN
    fen = reconocimiento.reconocer_tablero(_foto_del_tablero(fen_piezas))
    assert fen == f"{fen_piezas} w - - 0 1"
    assert chess.Board(fen).is_valid()


def test_posicion_de_medio_juego_conserva_cada_pieza_en_su_casilla(camara_calibrada) -> None:
    tablero = chess.Board()
    for jugada in ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "O-O", "Nf6"):
        tablero.push_san(jugada)
    fen = reconocimiento.reconocer_tablero(_foto_del_tablero(tablero.board_fen()), turno="w")
    assert fen.split()[0] == tablero.board_fen()


def test_el_turno_lo_indica_quien_llama_porque_la_foto_no_lo_dice(camara_calibrada) -> None:
    fen = reconocimiento.reconocer_tablero(_foto_del_tablero(chess.STARTING_BOARD_FEN), turno="b")
    assert fen.split()[1] == "b"


def test_tablero_vacio_da_un_fen_sin_piezas(camara_calibrada) -> None:
    fen = reconocimiento.reconocer_tablero(_foto_del_tablero("8/8/8/8/8/8/8/8"))
    assert fen.split()[0] == "8/8/8/8/8/8/8/8"


def test_una_foto_sin_tablero_y_sin_calibracion_lanza_valueerror(monkeypatch) -> None:
    monkeypatch.setattr(reconocimiento, "cargar_esquinas_calibradas", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError):
        reconocimiento.reconocer_tablero(np.full((480, 640, 3), 128, np.uint8))
