"""Reloj del servidor y regla de ausencia de las salas con reloj (humano vs humano)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import backend.servicios.sala.servicio_sala as svc
from backend.repositorios.repositorio_partida import RepositorioPartidasEnMemoria
from backend.servicios.sala import presencia_sala
from backend.servicios.sala.analisis_partida_sala import motivo_fin

CINCO_MIN = 300_000
INICIO = datetime(2026, 10, 26, 15, 0, 0, tzinfo=timezone.utc)


class Reloj:
    """Un reloj que se adelanta a mano: sirve para el tiempo de la partida y para la ausencia."""

    def __init__(self) -> None:
        self.ahora = INICIO
        self.mono = 1000.0

    def pasan(self, segundos: float) -> None:
        self.ahora += timedelta(seconds=segundos)
        self.mono += segundos


@pytest.fixture
def reloj(monkeypatch):
    falso = Reloj()
    monkeypatch.setattr(svc, "_repositorio", RepositorioPartidasEnMemoria())
    monkeypatch.setattr(svc, "_ahora", lambda: falso.ahora)
    monkeypatch.setattr(presencia_sala, "_instante", lambda: falso.mono)
    svc._locks_partida.clear()
    presencia_sala._ultima_senal.clear()
    return falso


def _pasan_conectados(reloj: Reloj, codigo: str, segundos: float) -> None:
    """Pasa el tiempo con los dos jugadores en pantalla (cada diez segundos consultan la sala), para que la ausencia no tape lo que se prueba."""
    restante = segundos
    while restante > 0:
        paso = min(10, restante)
        reloj.pasan(paso)
        restante -= paso
        svc.obtener_estado(codigo, 1)
        svc.obtener_estado(codigo, 2)


def _sala_en_curso(control_ms: int = CINCO_MIN):
    """Sala con reloj, `1` con blancas y `2` con negras, ya llena y en curso."""
    sala = svc.crear_sala(1, "blancas", control_ms)
    return svc.unirse(sala.codigo_sala, 2).codigo_sala


def test_solo_se_permiten_los_controles_de_tiempo_conocidos(reloj) -> None:
    with pytest.raises(ValueError):
        svc.crear_sala(1, "blancas", 12_345)
    assert svc.crear_sala(1, "blancas", 600_000).control_tiempo_ms == 600_000


def test_al_llenarse_la_sala_cada_lado_tiene_el_tiempo_completo_y_el_reloj_no_corre(reloj) -> None:
    codigo = _sala_en_curso()

    partida = svc.obtener_estado(codigo, 1)

    assert (partida.tiempo_blancas_ms, partida.tiempo_negras_ms) == (CINCO_MIN, CINCO_MIN)
    assert svc.reloj_corre(partida) is False
    reloj.pasan(120)  # pensar antes de la primera jugada no gasta tiempo
    assert svc.restantes_ms(svc.obtener_estado(codigo, 1)) == (CINCO_MIN, CINCO_MIN)


def test_las_dos_primeras_jugadas_no_gastan_tiempo_y_despues_corre_el_del_turno(reloj) -> None:
    codigo = _sala_en_curso()
    _pasan_conectados(reloj, codigo, 40)
    svc.mover_humano(codigo, 1, "e2e4")
    _pasan_conectados(reloj, codigo, 7)
    partida = svc.mover_humano(codigo, 2, "e7e5")
    assert (partida.tiempo_blancas_ms, partida.tiempo_negras_ms) == (CINCO_MIN, CINCO_MIN)
    assert svc.reloj_corre(partida) is True

    _pasan_conectados(reloj, codigo, 10)  # blancas piensan 10 segundos
    en_vivo = svc.restantes_ms(svc.obtener_estado(codigo, 2))
    assert en_vivo == (CINCO_MIN - 10_000, CINCO_MIN)

    partida = svc.mover_humano(codigo, 1, "g1f3")
    assert (partida.tiempo_blancas_ms, partida.tiempo_negras_ms) == (CINCO_MIN - 10_000, CINCO_MIN)
    _pasan_conectados(reloj, codigo, 4)  # ahora corre el de negras
    assert svc.restantes_ms(svc.obtener_estado(codigo, 1)) == (CINCO_MIN - 10_000, CINCO_MIN - 4_000)


def test_se_guarda_cuanto_tardo_cada_jugada(reloj) -> None:
    codigo = _sala_en_curso()
    svc.mover_humano(codigo, 1, "e2e4")
    reloj.pasan(3)
    svc.mover_humano(codigo, 2, "e7e5")
    reloj.pasan(8)
    partida = svc.mover_humano(codigo, 1, "g1f3")

    assert partida.tiempos_jugadas_ms == [None, 3_000, 8_000]


def test_al_que_se_le_acaba_el_tiempo_pierde_aunque_nadie_haga_nada(reloj) -> None:
    codigo = _sala_en_curso(180_000)
    svc.mover_humano(codigo, 1, "e2e4")
    svc.mover_humano(codigo, 2, "e7e5")
    _pasan_conectados(reloj, codigo, 179)
    assert svc.obtener_estado(codigo, 2).estado == "en_curso"
    _pasan_conectados(reloj, codigo, 2)  # a blancas se les acabaron los 3 minutos

    partida = svc.obtener_estado(codigo, 2)

    assert (partida.estado, partida.resultado, partida.terminada) == ("terminada", "0-1", True)
    assert partida.tiempo_blancas_ms == 0
    assert motivo_fin(partida) == "tiempo"


def test_una_jugada_que_llega_despues_de_agotado_el_tiempo_no_se_aplica(reloj) -> None:
    codigo = _sala_en_curso(180_000)
    svc.mover_humano(codigo, 1, "e2e4")
    svc.mover_humano(codigo, 2, "e7e5")
    reloj.pasan(200)  # nadie consultó la sala: la jugada tardía es lo primero que revisa el reloj

    partida = svc.mover_humano(codigo, 1, "g1f3")

    assert (partida.resultado, motivo_fin(partida)) == ("0-1", "tiempo")
    assert len(partida.tablero.move_stack) == 2  # la jugada tardía no entró


def test_sin_reloj_nadie_pierde_por_tiempo_ni_por_irse(reloj) -> None:
    codigo = _sala_en_curso(0)
    svc.mover_humano(codigo, 1, "e2e4")
    svc.mover_humano(codigo, 2, "e7e5")
    reloj.pasan(3600)

    partida = svc.obtener_estado(codigo, 2)

    assert (partida.estado, partida.resultado) == ("en_curso", None)
    assert svc.restantes_ms(partida) == (0, 0)


def test_con_reloj_quien_se_va_mas_de_20_segundos_pierde_la_partida(reloj) -> None:
    codigo = _sala_en_curso()
    svc.mover_humano(codigo, 1, "e2e4")
    reloj.pasan(5)
    svc.obtener_estado(codigo, 2)  # negras siguen en pantalla; blancas dejan de avisar
    reloj.pasan(15)
    svc.obtener_estado(codigo, 2)
    assert svc.obtener_estado(codigo, 2).estado == "en_curso"  # 20 segundos justos todavía no alcanzan

    reloj.pasan(3)
    partida = svc.obtener_estado(codigo, 2)

    assert (partida.estado, partida.resultado) == ("abandonada", "0-1")
    assert motivo_fin(partida) == "ausencia"


def test_quien_vuelve_a_tiempo_no_pierde(reloj) -> None:
    codigo = _sala_en_curso()
    for _ in range(6):
        reloj.pasan(10)
        svc.obtener_estado(codigo, 1)
        svc.obtener_estado(codigo, 2)

    assert svc.obtener_estado(codigo, 2).estado == "en_curso"


def test_si_los_dos_se_van_la_sala_se_cierra_sin_ganador(reloj) -> None:
    codigo = _sala_en_curso()
    reloj.pasan(60)

    partida = svc.revisar_reloj_y_ausencia(codigo)

    assert (partida.estado, partida.resultado) == ("abandonada", None)
    assert motivo_fin(partida) == "inactividad"


def test_el_espectador_no_cuenta_como_presencia(reloj) -> None:
    codigo = _sala_en_curso()
    reloj.pasan(25)
    svc.obtener_estado(codigo, 99)  # alguien mirando no salva a los jugadores ausentes

    assert svc.obtener_estado(codigo, 99).estado == "abandonada"


def test_abandonar_la_sala_cuenta_como_abandono_y_no_como_tiempo(reloj) -> None:
    codigo = _sala_en_curso()
    svc.mover_humano(codigo, 1, "e2e4")
    svc.mover_humano(codigo, 2, "e7e5")

    partida = svc.abandonar(codigo, 1)

    assert (partida.estado, partida.resultado, motivo_fin(partida)) == ("terminada", "0-1", "abandono")
    assert presencia_sala.segundos_sin_senal(partida.id, 1) is None  # ya no se lleva la cuenta


def test_la_limpieza_solo_mira_las_salas_abiertas_y_no_todas_las_partidas(reloj, monkeypatch) -> None:
    _sala_en_curso()
    repo = svc._repositorio

    def _no_deberia_listar_todo():
        raise AssertionError("el barrido no tiene que recorrer todas las partidas")

    monkeypatch.setattr(repo, "listar", _no_deberia_listar_todo)

    svc.limpiar_salas_inactivas()  # no falla: usa `listar_salas_abiertas`


def test_el_barrido_de_limpieza_cierra_la_sala_aunque_nadie_la_consulte(reloj) -> None:
    codigo = _sala_en_curso()
    svc.mover_humano(codigo, 1, "e2e4")
    svc.mover_humano(codigo, 2, "e7e5")
    reloj.pasan(30)

    svc.limpiar_salas_inactivas()

    assert svc._repositorio.obtener_por_codigo(codigo).estado == "abandonada"
