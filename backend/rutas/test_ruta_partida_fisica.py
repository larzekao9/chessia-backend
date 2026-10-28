"""Rutas /partida-fisica: permisos y errores (el ciclo completo se prueba en `test_partida_fisica`)."""
import chess
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rutas.ruta_auth import get_current_facilitador, get_current_user
from backend.servicios.partida import partida_fisica, servicio_partida
from backend.servicios.vision import seguimiento_vivo, tablero
from backend.servicios.vision.test_seguimiento_jugadas import ESQ


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(tablero, "cargar_esquinas_calibradas", lambda *_a, **_k: ESQ)
    monkeypatch.setattr(partida_fisica.PartidaFisica, "arrancar", lambda self: None)  # sin hilo: no hay carreras en la prueba
    app.dependency_overrides[get_current_facilitador] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        partida_fisica.detener()
        seguimiento_vivo.detener()
        app.dependency_overrides.pop(get_current_facilitador, None)


def test_solo_el_facilitador_puede_usarla() -> None:
    app.dependency_overrides.pop(get_current_facilitador, None)
    assert TestClient(app).get("/partida-fisica/estado").status_code in (401, 403)


def test_sin_partida_fisica_todo_responde_409_menos_el_estado(cliente) -> None:
    assert cliente.get("/partida-fisica/estado").json() == {"activa": False}
    for ruta, cuerpo in (("pausar", None), ("reanudar", None), ("foto-base", None), ("cancelar-lectura", None), ("posicion", {"fen": chess.STARTING_FEN}), ("jugada-manual", {"movimiento": "e4"}), ("resolver-incidente", {"accion": "continuar"})):
        assert cliente.post(f"/partida-fisica/{ruta}", json=cuerpo).status_code == 409


def test_iniciar_valida_la_partida_y_la_camara(cliente, monkeypatch) -> None:
    assert cliente.post("/partida-fisica/iniciar", json={"partida_id": "no-existe"}).status_code == 404
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    monkeypatch.setattr(tablero, "cargar_esquinas_calibradas", lambda *_a, **_k: None)
    assert cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id}).status_code == 409  # cámara sin calibrar


def test_iniciar_pausar_resolver_y_detener(cliente) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    respuesta = cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id}).json()
    assert respuesta["estado"] == "esperando_humano" and respuesta["activa"] and respuesta["turno"] == "blancas"
    assert cliente.post("/partida-fisica/pausar").json()["pausada"] is True
    assert cliente.post("/partida-fisica/reanudar").json()["pausada"] is False
    assert cliente.post("/partida-fisica/foto-base").json()["mensaje"] == "Tomando otra foto base…"
    assert cliente.post("/partida-fisica/resolver-incidente", json={"accion": "continuar"}).status_code == 422  # no hay incidente
    assert cliente.post("/partida-fisica/jugada-manual", json={"movimiento": "Qh8"}).status_code == 422  # ilegal
    assert cliente.delete("/partida-fisica").json() == {"activa": False}
    assert cliente.get("/partida-fisica/estado").json() == {"activa": False}


def test_con_partida_fisica_el_seguimiento_a_mano_responde_409(cliente) -> None:
    """La partida física maneja el seguimiento: el panel de seguimiento no puede reiniciarlo ni avanzarlo mientras dura."""
    app.dependency_overrides[get_current_user] = lambda: 1
    try:
        partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
        cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id})
        for metodo, ruta, cuerpo in (("post", "iniciar", {"fen": partida.fen}), ("post", "tick", None), ("post", "posicion", {"fen": partida.fen}),
                                     ("post", "foto-base", None), ("post", "deshacer", None), ("delete", "", None)):
            respuesta = cliente.request(metodo.upper(), f"/vision/seguimiento/{ruta}".rstrip("/"), json=cuerpo)
            assert respuesta.status_code == 409 and "partida física" in respuesta.json()["detail"], ruta
        assert cliente.get("/vision/seguimiento").json()["activo"] is True  # mirar sí se puede
        cliente.delete("/partida-fisica")
        assert cliente.delete("/vision/seguimiento").status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_corregir_la_posicion_valida_y_sigue_la_partida(cliente) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id})
    sin_rey = cliente.post("/partida-fisica/posicion", json={"fen": "8/8/8/8/8/8/8/8 w - - 0 1"})
    assert sin_rey.status_code == 422 and "rey" in sin_rey.json()["detail"]
    sin_formato = cliente.post("/partida-fisica/posicion", json={"fen": "esto no es un fen"})
    assert sin_formato.status_code == 422
    sin_peon = "rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 2"  # las blancas ya no tienen el peón de e... se acomoda a lo que se ve
    respuesta = cliente.post("/partida-fisica/posicion", json={"fen": sin_peon})
    assert respuesta.status_code == 200 and respuesta.json()["turno"] == "blancas" and respuesta.json()["fen"].startswith("rnbqkbnr/ppp1pppp/8/3p4/8/8/PPPPPPPP/RNBQKBNR")
    assert cliente.post("/partida-fisica/cancelar-lectura").status_code == 409  # no hay ninguna lectura que anular


def test_iniciar_acepta_el_reloj_y_la_ventana(cliente) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    respuesta = cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id, "tiempo_persona_s": 600, "tiempo_turing_s": 180, "ventana_s": 0.5}).json()
    assert respuesta["reloj"]["total_persona_ms"] == 600_000 and respuesta["reloj"]["total_turing_ms"] == 180_000 and respuesta["reloj"]["corre"] == "persona"
    assert cliente.post("/partida-fisica/iniciar", json={"partida_id": partida.id, "tiempo_persona_s": -1}).status_code == 422


def test_la_lista_de_verificacion_no_toca_el_brazo_y_marca_lo_que_falta(cliente, monkeypatch) -> None:
    from backend.servicios.brazo import control_brazo
    from backend.servicios.partida import preparacion

    preparacion._olvidar()
    llamadas = {"turing": 0}

    def turing():
        llamadas["turing"] += 1
        return {"id": "turing", "etiqueta": "Turing (modelo propio)", "estado": "ok", "detalle": "Responde.", "brazo": False}

    monkeypatch.setattr(preparacion, "_turing", turing)
    monkeypatch.setattr(preparacion, "_stockfish", lambda: {"id": "stockfish", "etiqueta": "Stockfish", "estado": "ok", "detalle": "Responde.", "brazo": False})
    monkeypatch.setattr(control_brazo, "estado", lambda: {"conectado": False})
    monkeypatch.setattr(preparacion, "cargar_calibracion_brazo", lambda: None)
    monkeypatch.setattr(preparacion, "cargar_parametros_agarre", lambda: None)
    datos = cliente.get("/partida-fisica/preparacion").json()
    por_id = {i["id"]: i for i in datos["items"]}
    assert por_id["camara"]["estado"] == "ok" and por_id["backend"]["estado"] == "ok"
    assert por_id["brazo_conexion"]["estado"] == "error" and por_id["brazo_conexion"]["brazo"] is True
    assert por_id["brazo_calibracion"]["estado"] == "error" and por_id["brazo_agarre"]["estado"] == "error"
    assert datos["listo"] is True  # lo del brazo lo exige la pantalla solo si la partida lo usa
    cliente.get("/partida-fisica/preparacion")
    assert llamadas["turing"] == 1  # la prueba de Turing se recuerda unos segundos

    monkeypatch.setattr(control_brazo, "estado", lambda: {"conectado": True, "emulador": True, "modo": 5, "modo_texto": "libre", "pinza": {"inicializada": True}, "velocidad_global": 40})
    monkeypatch.setattr(preparacion, "cargar_calibracion_brazo", lambda: type("C", (), {"posiciones_casillas": {"a1": (0, 0, 0)}})())
    monkeypatch.setattr(preparacion, "cargar_parametros_agarre", lambda: object())
    por_id = {i["id"]: i for i in cliente.get("/partida-fisica/preparacion").json()["items"]}
    assert [por_id[k]["estado"] for k in ("brazo_conexion", "brazo_pinza", "brazo_calibracion", "brazo_agarre")] == ["ok"] * 4
    assert por_id["brazo_velocidad"]["estado"] == "info" and "40" in por_id["brazo_velocidad"]["detalle"]
    preparacion._olvidar()
