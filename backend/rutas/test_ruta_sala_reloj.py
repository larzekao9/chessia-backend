"""Salas con reloj por la API: crear con control de tiempo, tiempos en vivo, derrota por tiempo y por irse (SQLite en memoria)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import backend.servicios.sala.servicio_sala as svc
from backend.rutas.test_ruta_sala import _registrar_jugador, auth_header, cliente  # noqa: F401  (fixture y ayudas compartidas)
from backend.servicios.sala import presencia_sala

CINCO_MIN = 300_000
INICIO = datetime(2026, 10, 26, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def tiempo(monkeypatch):
    """Reloj de la partida y de la ausencia, que avanzan a mano."""
    estado = {"ahora": INICIO, "mono": 1000.0}

    def pasan(segundos: float) -> None:
        estado["ahora"] += timedelta(seconds=segundos)
        estado["mono"] += segundos

    monkeypatch.setattr(svc, "_ahora", lambda: estado["ahora"])
    monkeypatch.setattr(presencia_sala, "_instante", lambda: estado["mono"])
    presencia_sala._ultima_senal.clear()
    estado["pasan"] = pasan
    return estado


def _partida(cliente, control_ms: int = CINCO_MIN):
    """Dos jugadores en una sala ya llena: devuelve (código, cabecera de blancas, cabecera de negras)."""
    t1 = _registrar_jugador(cliente, "uno@test.com", "Jugador Uno")
    t2 = _registrar_jugador(cliente, "dos@test.com", "Jugador Dos")
    codigo = cliente.post("/sala", json={"color": "blancas", "control_tiempo_ms": control_ms}, headers=auth_header(t1)).json()["codigo"]
    cliente.post(f"/sala/{codigo}/unirse", headers=auth_header(t2))
    return codigo, auth_header(t1), auth_header(t2)


def test_se_crea_una_sala_con_reloj_y_se_ve_en_la_respuesta(cliente, tiempo) -> None:
    t1 = _registrar_jugador(cliente, "uno@test.com", "Jugador Uno")

    sala = cliente.post("/sala", json={"color": "blancas", "control_tiempo_ms": 600_000}, headers=auth_header(t1)).json()

    assert (sala["control_tiempo_ms"], sala["estado"], sala["reloj_corre"]) == (600_000, "esperando", False)


def test_un_control_de_tiempo_inventado_se_rechaza(cliente, tiempo) -> None:
    t1 = _registrar_jugador(cliente, "uno@test.com", "Jugador Uno")

    assert cliente.post("/sala", json={"control_tiempo_ms": 12_345}, headers=auth_header(t1)).status_code == 400
    assert cliente.post("/sala", json={"control_tiempo_ms": -1}, headers=auth_header(t1)).status_code == 422
    assert cliente.post("/sala", json={}, headers=auth_header(t1)).json()["control_tiempo_ms"] == 0  # sin indicar: sin reloj


def test_los_tiempos_corren_en_vivo_para_el_que_tiene_el_turno(cliente, tiempo) -> None:
    codigo, blancas, negras = _partida(cliente)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=blancas)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e7e5"}, headers=negras)
    tiempo["pasan"](12)

    sala = cliente.get(f"/sala/{codigo}", headers=blancas).json()

    assert (sala["tiempo_blancas_ms"], sala["tiempo_negras_ms"], sala["reloj_corre"]) == (CINCO_MIN - 12_000, CINCO_MIN, True)
    assert sala["negras"]["ausente_s"] == 12  # hace 12 segundos que negras no consulta
    assert sala["blancas"]["ausente_s"] == 0


def test_al_acabarse_el_tiempo_la_partida_termina_y_se_dice_por_que(cliente, tiempo) -> None:
    codigo, blancas, negras = _partida(cliente, 180_000)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=blancas)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e7e5"}, headers=negras)
    for _ in range(20):  # los dos siguen en pantalla mientras blancas piensan de más
        tiempo["pasan"](10)
        cliente.get(f"/sala/{codigo}", headers=negras)
        sala = cliente.get(f"/sala/{codigo}", headers=blancas).json()

    assert (sala["terminada"], sala["resultado"], sala["motivo_fin"]) == (True, "0-1", "tiempo")
    assert sala["tiempo_blancas_ms"] == 0
    assert cliente.post(f"/sala/{codigo}/mover", json={"jugada": "g1f3"}, headers=blancas).status_code == 400  # ya terminó


def test_quien_deja_de_avisar_pierde_por_ausencia_y_el_otro_lo_ve(cliente, tiempo) -> None:
    codigo, blancas, negras = _partida(cliente)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=blancas)
    for _ in range(2):  # negras siguen consultando; blancas cerraron la pestaña
        tiempo["pasan"](8)
        sala = cliente.get(f"/sala/{codigo}", headers=negras).json()
    assert (sala["estado"], sala["blancas"]["ausente_s"]) == ("en_curso", 16)  # el rival ve que blancas no vuelve hace 16 segundos

    tiempo["pasan"](8)  # 24 segundos sin señal: más de los 20 permitidos
    sala = cliente.get(f"/sala/{codigo}", headers=negras).json()

    assert (sala["estado"], sala["resultado"], sala["motivo_fin"]) == ("abandonada", "0-1", "ausencia")


def test_el_endpoint_de_presencia_cuenta_como_senal_de_vida(cliente, tiempo) -> None:
    codigo, blancas, negras = _partida(cliente)
    for _ in range(6):
        tiempo["pasan"](10)
        assert cliente.post(f"/sala/{codigo}/presencia", headers=blancas).status_code == 200
        cliente.post(f"/sala/{codigo}/presencia", headers=negras)

    assert cliente.get(f"/sala/{codigo}", headers=blancas).json()["estado"] == "en_curso"
    assert cliente.post("/sala/ZZZZZZ/presencia", headers=blancas).status_code == 404


def test_sin_reloj_se_puede_salir_y_volver(cliente, tiempo) -> None:
    codigo, blancas, negras = _partida(cliente, 0)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=blancas)
    tiempo["pasan"](3600)

    sala = cliente.get(f"/sala/{codigo}", headers=negras).json()

    assert (sala["estado"], sala["tiempo_blancas_ms"], sala["reloj_corre"]) == ("en_curso", None, False)
