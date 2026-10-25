"""Las salas (humano vs humano) no miden contra la IA: estadísticas, nivel y endpoints `/partida/{id}`."""
from __future__ import annotations

import chess

from backend.modelos.partida import Partida
from backend.rutas.test_ruta_sala import _registrar_jugador, auth_header, cliente  # noqa: F401
from backend.rutas.test_sala_analisis import (
    MATE_DEL_PASTOR,
    _crear_sala_en_curso,
    _jugar,
    _sala_con_mate_del_pastor,
)


def _sembrar_partida_ia(cliente, email: str, jugadas_uci: list[str], evaluaciones: list[tuple]) -> str:
    """Guarda una partida contra el motor ya terminada (1-0) con evaluaciones por jugada del jugador."""
    usuario_id = cliente.get(
        "/auth/me", headers=auth_header(cliente.post(
            "/auth/login", json={"email": email, "password": "test1234"}
        ).json()["tokens"]["access_token"])
    ).json()["id"]
    tablero = chess.Board()
    partida = Partida(nivel=5, tipo_oponente="motor", usuario_id=usuario_id, estado="terminada")
    cliente.repo.guardar(partida)
    for numero, uci in enumerate(jugadas_uci, start=1):
        movimiento = chess.Move.from_uci(uci)
        cliente.repo.registrar_jugada(
            partida.id, numero, tablero.fen(), uci, "jugador" if numero % 2 == 1 else "motor"
        )
        tablero.push(movimiento)
        partida.tablero.push(movimiento)
    cliente.repo.guardar(partida)
    for numero, (cp, mate, cp_mejor, mate_mejor) in evaluaciones:
        cliente.repo.actualizar_evaluacion_jugada(partida.id, numero, cp, mate, cp_mejor, mate_mejor)
    return partida.id


EVALUACIONES_IA = [(1, (20, None, 30, None)), (3, (-250, None, 40, None)), (5, (60, None, 70, None)), (7, (None, 0, 80, None))]


def _estadisticas(cliente, token) -> dict:
    r = cliente.get("/usuario/estadisticas", headers=auth_header(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_estadisticas_completas_no_cambian_con_una_sala_ni_con_su_analisis(cliente):
    token1 = _registrar_jugador(cliente, "uno@test.com", "Uno")
    token2 = _registrar_jugador(cliente, "dos@test.com", "Dos")
    _sembrar_partida_ia(cliente, "uno@test.com", MATE_DEL_PASTOR, EVALUACIONES_IA)
    antes = _estadisticas(cliente, token1)
    assert antes["total_partidas"] == 1 and antes["partidas_ganadas"] == 1
    assert antes["precision_promedio"] > 0 and antes["top_errores"] and antes["precision_por_fase"]

    sala = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1)).json()
    cliente.post(f"/sala/{sala['codigo']}/unirse", headers=auth_header(token2))
    _jugar(cliente, sala["codigo"], token1, token2, MATE_DEL_PASTOR)
    assert _estadisticas(cliente, token1) == antes

    assert cliente.get(f"/sala/{sala['codigo']}/analisis", headers=auth_header(token1)).status_code == 200
    assert _estadisticas(cliente, token1) == antes

    vacias = _estadisticas(cliente, token2)
    assert vacias["total_partidas"] == 0 and vacias["precision_promedio"] == 0
    assert vacias["top_errores"] == [] and vacias["progreso_semanal"] is not None


def test_estadisticas_no_cuentan_la_sala_cuando_el_creador_juega_con_negras(cliente):
    token1 = _registrar_jugador(cliente, "uno@test.com", "Uno")
    token2 = _registrar_jugador(cliente, "dos@test.com", "Dos")
    antes = _estadisticas(cliente, token1)
    sala = cliente.post("/sala", json={"color": "negras"}, headers=auth_header(token1)).json()
    cliente.post(f"/sala/{sala['codigo']}/unirse", headers=auth_header(token2))
    _jugar(cliente, sala["codigo"], token2, token1, MATE_DEL_PASTOR)
    assert _estadisticas(cliente, token1) == antes


def test_endpoints_de_partida_con_el_id_de_una_sala_no_dan_500(cliente):
    codigo, token1, token2 = _sala_con_mate_del_pastor(cliente)
    partida_id = cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()["partida_id"]
    nivel_antes = cliente.get("/auth/nivel", headers=auth_header(token1)).json()

    r = cliente.post(f"/partida/{partida_id}/calibrar", headers=auth_header(token1))
    assert r.status_code == 200
    assert r.json()["registrada"] is False and r.json()["motivo"] == "no_aplica"

    r = cliente.get(f"/partida/{partida_id}/analisis-completo", headers=auth_header(token1))
    assert r.status_code == 400
    assert r.json()["detail"] == f"El análisis de una sala se pide en /sala/{codigo}/analisis"

    assert cliente.get("/auth/nivel", headers=auth_header(token1)).json() == nivel_antes
    for ruta in ("mover", "tiempo-agotado", "mover-desde-foto"):
        cuerpo = {"jugada": "e2e4"} if ruta == "mover" else ({"lado": "blancas"} if ruta == "tiempo-agotado" else None)
        r = cliente.post(f"/partida/{partida_id}/{ruta}", json=cuerpo, headers=auth_header(token1))
        assert r.status_code == 400, (ruta, r.text)
        assert isinstance(r.json()["detail"], str)


def test_tiempo_agotado_y_mover_por_partida_no_mutan_una_sala_en_curso(cliente):
    codigo, token1, token2 = _crear_sala_en_curso(cliente)
    _jugar(cliente, codigo, token1, token2, ["e2e4", "e7e5"])
    sala = cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()
    r = cliente.post(
        f"/partida/{sala['partida_id']}/tiempo-agotado", json={"lado": "negras"}, headers=auth_header(token1)
    )
    assert r.status_code == 400
    r = cliente.post(f"/partida/{sala['partida_id']}/mover", json={"jugada": "g1f3"}, headers=auth_header(token1))
    assert r.status_code == 400
    r = cliente.put(f"/partida/{sala['partida_id']}/reloj", json={"blancas_ms": 1000}, headers=auth_header(token1))
    assert r.status_code == 400
    assert cliente.get(f"/sala/{codigo}", headers=auth_header(token2)).json() == {**sala, "rol": "negras"}


def test_analisis_red_con_el_id_de_una_sala_es_400(cliente):
    codigo, token1, _ = _sala_con_mate_del_pastor(cliente)
    partida_id = cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()["partida_id"]
    facilitador = _registrar_jugador(cliente, "fac@test.com", "Fac", rol="facilitador")
    r = cliente.get(f"/partida/{partida_id}/analisis-red", headers=auth_header(facilitador))
    assert r.status_code == 400


def test_historial_paginado_mezcla_salas_y_partidas_ia_sin_repetir_ni_saltar(cliente):
    token1 = _registrar_jugador(cliente, "uno@test.com", "Uno")
    token2 = _registrar_jugador(cliente, "dos@test.com", "Dos")
    ia = [_sembrar_partida_ia(cliente, "uno@test.com", MATE_DEL_PASTOR, EVALUACIONES_IA) for _ in range(2)]
    for color, (t_blancas, t_negras) in (("blancas", (token1, token2)), ("negras", (token2, token1))):
        sala = cliente.post("/sala", json={"color": color}, headers=auth_header(token1)).json()
        cliente.post(f"/sala/{sala['codigo']}/unirse", headers=auth_header(token2))
        _jugar(cliente, sala["codigo"], t_blancas, t_negras, ["e2e4", "e7e5", "g1f3"])
    cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1))

    completo = cliente.get("/usuario/historial-partidas?limit=100", headers=auth_header(token1)).json()
    assert completo["total"] == 4 and len(completo["partidas"]) == 4

    ids, offset = [], 0
    while True:
        pagina = cliente.get(
            f"/usuario/historial-partidas?limit=1&offset={offset}", headers=auth_header(token1)
        ).json()
        assert pagina["total"] == 4
        if not pagina["partidas"]:
            break
        assert len(pagina["partidas"]) == 1
        ids.append(pagina["partidas"][0]["id"])
        offset += 1
    assert ids == [p["id"] for p in completo["partidas"]]
    assert len(set(ids)) == 4 and set(ia) <= set(ids)
    salas = [p for p in completo["partidas"] if p["tipo_oponente"] == "humano"]
    assert {p["color"] for p in salas} == {"blancas", "negras"}
    assert all(p["rival_nombre"] == "Dos" and p["codigo_sala"] for p in salas)
