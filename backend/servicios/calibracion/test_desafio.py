"""Pruebas del desafío progresivo de Turing (`desafio.py`): la regla del saldo y el conteo de jugadas del jugador."""
from types import SimpleNamespace

from backend.servicios.calibracion.desafio import DESAFIO_MAX, _jugadas_del_jugador, saldo_a_desafio


def test_sin_partidas_o_con_saldo_negativo_no_hay_desafio():
    assert saldo_a_desafio(0, 0) == 0
    assert saldo_a_desafio(1, 3) == 0


def test_el_desafio_es_el_saldo_de_victorias_menos_derrotas():
    assert saldo_a_desafio(1, 0) == 1
    assert saldo_a_desafio(3, 1) == 2


def test_el_desafio_no_pasa_del_maximo():
    assert saldo_a_desafio(5, 0) == DESAFIO_MAX


def test_las_jugadas_del_jugador_son_la_mitad_redondeada_hacia_arriba():
    assert _jugadas_del_jugador(SimpleNamespace(jugadas_uci="e2e4 e7e5 g1f3")) == 2
    assert _jugadas_del_jugador(SimpleNamespace(jugadas_uci="e2e4 e7e5")) == 1
    assert _jugadas_del_jugador(SimpleNamespace(jugadas_uci="")) == 0
