"""Pruebas de las tablas automáticas (`fin_por_reglas` / `resultado_por_reglas`) y del material insuficiente al caer la bandera."""
import chess

from backend.modelos.partida import fin_por_reglas, resultado_por_reglas


def _jugar(tablero: chess.Board, jugadas: str) -> chess.Board:
    for uci in jugadas.split():
        tablero.push_uci(uci)
    return tablero


def test_una_partida_en_juego_no_termina():
    tablero = _jugar(chess.Board(), "e2e4 e7e5")
    assert not fin_por_reglas(tablero)
    assert resultado_por_reglas(tablero) is None


def test_dos_repeticiones_todavia_no_son_tablas_pero_la_tercera_si():
    tablero = _jugar(chess.Board(), "g1f3 g8f6 f3g1 f6g8")
    assert not fin_por_reglas(tablero)
    _jugar(tablero, "g1f3 g8f6 f3g1 f6g8")
    assert fin_por_reglas(tablero)
    assert resultado_por_reglas(tablero) == "1/2-1/2"


def test_las_cincuenta_jugadas_sin_captura_ni_peon_son_tablas():
    tablero = chess.Board("8/8/8/8/8/4k3/8/4K2R w - - 100 80")
    assert not tablero.is_game_over()
    assert fin_por_reglas(tablero)
    assert resultado_por_reglas(tablero) == "1/2-1/2"


def test_el_jaque_mate_sigue_dando_el_resultado_de_siempre():
    tablero = _jugar(chess.Board(), "f2f3 e7e5 g2g4 d8h4")
    assert fin_por_reglas(tablero)
    assert resultado_por_reglas(tablero) == "0-1"


def test_sin_material_para_dar_mate_el_rival_no_gana_por_tiempo():
    tablero = chess.Board("8/8/8/8/8/4k3/8/4K2R w - - 0 1")  # las negras solo tienen el rey; las blancas, una torre
    assert tablero.has_insufficient_material(chess.BLACK)
    assert not tablero.has_insufficient_material(chess.WHITE)
