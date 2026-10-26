"""Tests de `GET /sala/{codigo}/analisis`, `DELETE /sala/{codigo}` y la limpieza al leer (Fase 1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.modelos.tablas_orm import JugadaORM
from backend.rutas.test_ruta_sala import _registrar_jugador, auth_header, cliente  # noqa: F401

MATE_DEL_PASTOR = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


def _crear_sala_en_curso(cliente, mail1="uno@test.com", mail2="dos@test.com"):
    """Crea una sala con `mail1` en blancas y `mail2` en negras; devuelve (codigo, token1, token2)."""
    token1 = _registrar_jugador(cliente, mail1, "Uno")
    token2 = _registrar_jugador(cliente, mail2, "Dos")
    sala = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1)).json()
    cliente.post(f"/sala/{sala['codigo']}/unirse", headers=auth_header(token2))
    return sala["codigo"], token1, token2


def _jugar(cliente, codigo, token1, token2, jugadas):
    for i, jugada in enumerate(jugadas):
        token = token1 if i % 2 == 0 else token2
        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": jugada}, headers=auth_header(token))
        assert r.status_code == 200, r.text


def _sala_con_mate_del_pastor(cliente):
    codigo, token1, token2 = _crear_sala_en_curso(cliente)
    _jugar(cliente, codigo, token1, token2, MATE_DEL_PASTOR)
    return codigo, token1, token2


class TestAnalisisSala:
    def test_analisis_codigo_inexistente_404(self, cliente):
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        r = cliente.get("/sala/ZZZZZZ/analisis", headers=auth_header(token))
        assert r.status_code == 404

    def test_analisis_antes_de_terminar_400(self, cliente):
        codigo, token1, _ = _crear_sala_en_curso(cliente)
        r = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token1))
        assert r.status_code == 400
        assert isinstance(r.json()["detail"], str)

    def test_analisis_sala_terminada_cumple_el_contrato(self, cliente):
        codigo, token1, token2 = _sala_con_mate_del_pastor(cliente)
        r = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token2))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["codigo"] == codigo
        assert data["resultado"] == "1-0"
        assert data["motivo_fin"] == "mate"
        assert data["total_jugadas"] == 7
        assert data["blancas"]["nombre"] == "Uno"
        assert data["negras"]["nombre"] == "Dos"
        for bando in ("blancas", "negras"):
            assert {"precision", "mejores", "imprecisiones", "errores", "blunders"} <= set(data[bando])
        assert [p["ply"] for p in data["curva"]] == list(range(1, 8))
        assert len(data["momentos_clave"]) <= 5
        assert len(data["jugadas"]) == 7

    def test_la_jugada_que_da_mate_es_brillante_y_no_blunder(self, cliente):
        codigo, token1, _ = _sala_con_mate_del_pastor(cliente)
        data = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token1)).json()
        mate = data["jugadas"][-1]
        assert mate["mate_en"] == 0
        assert mate["calidad"] == "brillante"
        assert mate["perdida_cp"] == 0
        assert all(m["ply"] != 7 for m in data["momentos_clave"])
        assert data["blancas"]["blunders"] == 0

    def test_segunda_llamada_usa_cache(self, cliente, monkeypatch):
        import backend.servicios.partida.servicio_partida as svc_partida

        codigo, token1, token2 = _sala_con_mate_del_pastor(cliente)
        llamadas = []
        original = svc_partida.analizar_partida
        monkeypatch.setattr(
            svc_partida, "analizar_partida", lambda *a, **k: (llamadas.append(1), original(*a, **k))[1]
        )
        primero = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token1)).json()
        segundo = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token2)).json()
        assert len(llamadas) == 1
        assert primero == segundo

    def test_analisis_no_persiste_evaluaciones(self, cliente):
        codigo, token1, _ = _sala_con_mate_del_pastor(cliente)
        assert cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token1)).status_code == 200
        with cliente.fabrica() as sesion:
            filas = sesion.scalars(select(JugadaORM)).all()
        assert filas
        assert all(f.evaluacion_cp is None and f.mate_en is None for f in filas)

    def test_analisis_de_sala_abandonada_por_el_rival(self, cliente):
        codigo, token1, token2 = _crear_sala_en_curso(cliente)
        _jugar(cliente, codigo, token1, token2, ["e2e4", "e7e5"])
        cliente.post(f"/sala/{codigo}/abandonar", headers=auth_header(token2))
        r = cliente.get(f"/sala/{codigo}/analisis", headers=auth_header(token1))
        assert r.status_code == 200
        assert r.json()["motivo_fin"] == "abandono"
        assert r.json()["resultado"] == "1-0"


class TestCancelarSala:
    def test_creador_cancela_sala_esperando(self, cliente):
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        codigo = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token)).json()["codigo"]
        r = cliente.delete(f"/sala/{codigo}", headers=auth_header(token))
        assert r.status_code == 204
        assert cliente.get(f"/sala/{codigo}", headers=auth_header(token)).status_code == 404
        assert cliente.get("/sala/activa", headers=auth_header(token)).json() is None

    def test_cancelar_codigo_inexistente_404(self, cliente):
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        assert cliente.delete("/sala/ZZZZZZ", headers=auth_header(token)).status_code == 404

    def test_otro_usuario_no_puede_cancelar_403(self, cliente):
        token1 = _registrar_jugador(cliente, "uno@test.com", "Uno")
        token2 = _registrar_jugador(cliente, "dos@test.com", "Dos")
        codigo = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token1)).json()["codigo"]
        r = cliente.delete(f"/sala/{codigo}", headers=auth_header(token2))
        assert r.status_code == 403
        assert isinstance(r.json()["detail"], str)
        assert cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).status_code == 200

    def test_no_se_cancela_sala_en_curso_400(self, cliente):
        codigo, token1, _ = _crear_sala_en_curso(cliente)
        r = cliente.delete(f"/sala/{codigo}", headers=auth_header(token1))
        assert r.status_code == 400
        assert cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()["estado"] == "en_curso"


class TestAbandonarEsperando:
    def test_abandonar_sala_esperando_400_y_no_registra_derrota(self, cliente):
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        codigo = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token)).json()["codigo"]
        r = cliente.post(f"/sala/{codigo}/abandonar", headers=auth_header(token))
        assert r.status_code == 400
        assert isinstance(r.json()["detail"], str)
        sala = cliente.get(f"/sala/{codigo}", headers=auth_header(token)).json()
        assert sala["estado"] == "esperando" and sala["resultado"] is None
        historial = cliente.get("/usuario/historial-partidas", headers=auth_header(token)).json()
        assert historial["total"] == 0


class TestLimpiezaAlLeer:
    def _envejecer(self, cliente, codigo, **delta):
        from backend.modelos.tablas_orm import PartidaORM

        antes = datetime.now(timezone.utc) - timedelta(**delta)
        with cliente.fabrica() as sesion:
            fila = sesion.scalar(select(PartidaORM).where(PartidaORM.codigo_sala == codigo))
            fila.fecha = antes
            fila.actualizada_en = antes
            sesion.commit()

    def test_get_sala_esperando_vencida_da_404(self, cliente):
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        codigo = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token)).json()["codigo"]
        self._envejecer(cliente, codigo, minutes=45)
        assert cliente.get(f"/sala/{codigo}", headers=auth_header(token)).status_code == 404

    def test_get_sala_sin_reloj_vencida_a_los_2_dias_pierde_quien_tenia_el_turno(self, cliente):
        codigo, token1, token2 = _crear_sala_en_curso(cliente)
        # Tras estas jugadas le toca mover a negras: si no responde en 2 días, pierde.
        _jugar(cliente, codigo, token1, token2, ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"])
        self._envejecer(cliente, codigo, hours=49)
        data = cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()
        assert data["estado"] == "abandonada"
        assert data["terminada"] is True
        assert data["resultado"] == "1-0"
        assert data["motivo_fin"] == "sin_respuesta"

    def test_get_sala_sin_reloj_con_menos_de_2_dias_sigue_en_curso(self, cliente):
        codigo, token1, token2 = _crear_sala_en_curso(cliente)
        _jugar(cliente, codigo, token1, token2, ["e2e4"])
        self._envejecer(cliente, codigo, hours=13)
        data = cliente.get(f"/sala/{codigo}", headers=auth_header(token1)).json()
        assert data["estado"] == "en_curso"

    def test_abandono_manual_sigue_siendo_abandono(self, cliente):
        codigo, token1, token2 = _crear_sala_en_curso(cliente)
        _jugar(cliente, codigo, token1, token2, ["e2e4", "e7e5"])
        data = cliente.post(f"/sala/{codigo}/abandonar", headers=auth_header(token2)).json()
        assert data["estado"] == "terminada"
        assert data["motivo_fin"] == "abandono"
        assert data["resultado"] == "1-0"

    def test_si_la_limpieza_falla_las_rutas_siguen_respondiendo_y_loguean(self, cliente, monkeypatch, caplog):
        import backend.rutas.ruta_sala as ruta

        def _falla(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ruta, "limpiar_salas_inactivas", _falla)
        token = _registrar_jugador(cliente, "uno@test.com", "Uno")
        codigo = cliente.post("/sala", json={"color": "blancas"}, headers=auth_header(token)).json()["codigo"]
        with caplog.at_level("ERROR"):
            assert cliente.get("/sala/activa", headers=auth_header(token)).status_code == 200
            assert cliente.get(f"/sala/{codigo}", headers=auth_header(token)).status_code == 200
        assert "limpieza de salas" in caplog.text
