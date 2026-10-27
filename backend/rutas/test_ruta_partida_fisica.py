"""Rutas /partida-fisica: permisos y errores (el ciclo completo se prueba en `test_partida_fisica`)."""
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
    for ruta, cuerpo in (("pausar", None), ("reanudar", None), ("jugada-manual", {"movimiento": "e4"}), ("resolver-incidente", {"accion": "continuar"})):
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
