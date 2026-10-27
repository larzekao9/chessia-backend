"""Rutas /vision/seguimiento/*: seguir la partida por diferencias, con una cámara y una calibración de mentira."""
import chess
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rutas.ruta_auth import get_current_user
from backend.servicios.vision import seguimiento_vivo, tablero
from backend.servicios.vision.test_seguimiento_jugadas import ESQ, _frame, _posicion_tras


@pytest.fixture
def cliente(monkeypatch):
    cuadros: list = []
    monkeypatch.setattr(tablero, "cargar_esquinas_calibradas", lambda *_a, **_k: ESQ)
    monkeypatch.setattr(seguimiento_vivo, "capturar_foto_tablero", lambda camara="principal": cuadros.pop(0))
    app.dependency_overrides[get_current_user] = lambda: 1
    try:
        c = TestClient(app)
        c.cuadros = cuadros
        yield c
    finally:
        seguimiento_vivo.detener()
        app.dependency_overrides.pop(get_current_user, None)


def test_sin_iniciar_no_hay_seguimiento(cliente) -> None:
    assert cliente.get("/vision/seguimiento").json() == {"activo": False}
    assert cliente.post("/vision/seguimiento/tick").status_code == 409


def test_sigue_una_jugada_y_se_puede_detener(cliente) -> None:
    inicio, tras_e4 = chess.STARTING_FEN, _posicion_tras("e2e4")
    assert cliente.post("/vision/seguimiento/iniciar", json={}).json()["fen"] == inicio
    cliente.cuadros += [_frame(inicio, semilla=s) for s in (1, 2, 3)] + [_frame(tras_e4, mano=True, semilla=4)] + [_frame(tras_e4, semilla=s) for s in (5, 6, 7, 8)]
    respuestas = [cliente.post("/vision/seguimiento/tick").json() for _ in range(8)]
    jugada = next(r for r in respuestas if r["estado"] == "jugada")
    assert jugada["jugada"] == "e4" and jugada["turno"] == "negras" and jugada["jugadas"] == ["e4"]
    assert cliente.get("/vision/seguimiento").json()["jugadas"] == ["e4"]
    assert cliente.delete("/vision/seguimiento").json() == {"activo": False}


def test_iniciar_valida_el_fen_y_la_calibracion(cliente, monkeypatch) -> None:
    assert cliente.post("/vision/seguimiento/iniciar", json={"fen": "esto no es un fen"}).status_code == 422
    monkeypatch.setattr(tablero, "cargar_esquinas_calibradas", lambda *_a, **_k: None)
    assert cliente.post("/vision/seguimiento/iniciar", json={}).status_code == 409


def test_fijar_la_posicion_tras_la_jugada_del_robot(cliente) -> None:
    cliente.post("/vision/seguimiento/iniciar", json={})
    nueva = _posicion_tras("e2e4", "e7e5")
    assert cliente.post("/vision/seguimiento/posicion", json={"fen": nueva}).json()["estado"] == "tomando_base"
    assert cliente.post("/vision/seguimiento/posicion", json={"fen": "x"}).status_code == 422


def test_tick_con_vista_deshacer_y_foto_base(cliente) -> None:
    inicio, tras_e4 = chess.STARTING_FEN, _posicion_tras("e2e4")
    cliente.post("/vision/seguimiento/iniciar", json={})
    cliente.cuadros += [_frame(inicio, semilla=s) for s in (1, 2, 3)] + [_frame(tras_e4, mano=True, semilla=4)] + [_frame(tras_e4, semilla=s) for s in (5, 6, 7)]
    respuestas = [cliente.post("/vision/seguimiento/tick?vista=true").json() for _ in range(7)]
    jugada = next(r for r in respuestas if r["estado"] == "jugada")
    assert jugada["vista"]["disponible"] and jugada["vista"]["candidatas"][0]["san"] == "e4" and jugada["puede_deshacer"]
    cliente.cuadros.append(_frame(tras_e4, semilla=8))
    assert "vista" not in cliente.post("/vision/seguimiento/tick").json()  # sin pedirla, no se manda
    assert cliente.get("/vision/seguimiento/vista").json()["jugada"] == "e4"
    assert cliente.post("/vision/seguimiento/foto-base").json()["estado"] == "tomando_base"
    assert cliente.post("/vision/seguimiento/deshacer").json()["deshecha"] == "e4"
    assert cliente.post("/vision/seguimiento/deshacer").status_code == 422  # ya no queda nada que deshacer


def test_vista_deshacer_y_foto_base_sin_seguimiento(cliente) -> None:
    assert cliente.get("/vision/seguimiento/vista").status_code == 409
    assert cliente.post("/vision/seguimiento/deshacer").status_code == 409
    assert cliente.post("/vision/seguimiento/foto-base").status_code == 409


def test_una_alerta_guarda_las_fotos_y_los_cambios_para_revisarlos(cliente, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(seguimiento_vivo, "GUARDAR_FALLOS", True)
    monkeypatch.setattr(seguimiento_vivo, "DIRECTORIO_FALLOS", tmp_path / "fallos")
    inicio = chess.STARTING_FEN
    ilegal = chess.Board(inicio); ilegal.set_piece_at(chess.E2, None); ilegal.set_piece_at(chess.E5, chess.Piece(chess.PAWN, chess.WHITE))
    cliente.post("/vision/seguimiento/iniciar", json={})
    cliente.cuadros += [_frame(inicio, semilla=s) for s in (1, 2, 3)] + [_frame(ilegal.fen(), mano=True, semilla=4)] + [_frame(ilegal.fen(), semilla=s) for s in range(5, 12)]
    estados = [cliente.post("/vision/seguimiento/tick").json()["estado"] for _ in range(11)]
    assert "invalida" in estados
    archivos = sorted((tmp_path / "fallos").glob("*"))
    assert [a.suffix for a in archivos] == [".jpg", ".json"]  # una sola vez por alerta, aunque dure varios cuadros
    import json

    datos = json.loads(archivos[1].read_text(encoding="utf-8"))
    assert datos["estado"] == "invalida" and {"e2", "e5"} <= set(datos["cambiadas"]) and datos["puntajes"]["e5"] > 0


def test_avanzar_con_cuadros_nuevos_no_repite_ni_reprocesa_un_cuadro(monkeypatch, cliente) -> None:
    inicio = chess.STARTING_FEN
    llegadas = [(1, _frame(inicio, semilla=1)), None, (2, _frame(inicio, semilla=2)), None]
    pedidos: list[int] = []

    def nueva(despues_de, camara="principal", timeout=0.25):
        pedidos.append(despues_de)
        return llegadas.pop(0)

    monkeypatch.setattr(seguimiento_vivo, "esperar_foto_nueva", nueva)
    cliente.post("/vision/seguimiento/iniciar", json={})
    primero = seguimiento_vivo.avanzar(nuevo=True)
    sin_cuadro = seguimiento_vivo.avanzar(nuevo=True)
    segundo = seguimiento_vivo.avanzar(nuevo=True)
    assert "repetido" not in primero and sin_cuadro["repetido"] is True and sin_cuadro["estado"] == primero["estado"] and "repetido" not in segundo
    assert pedidos == [0, 1, 1]  # cada pedido es «después del último cuadro ya procesado»


def test_los_cuadros_quietos_se_ajustan_al_ritmo_real_de_la_camara(monkeypatch, cliente) -> None:
    """`ticks_para` supone 30 cuadros por segundo; si la cámara entrega 10, esos cuadros serían tres veces más tiempo: se recalculan con el ritmo medido."""
    inicio = chess.STARTING_FEN
    cuadros = iter(range(1, 100))
    monkeypatch.setattr(seguimiento_vivo, "esperar_foto_nueva", lambda despues_de, camara="principal", timeout=0.25: (next(cuadros), _frame(inicio, semilla=despues_de + 1)))
    ritmo = {"fps": 30.0}
    monkeypatch.setattr(seguimiento_vivo, "_rendimiento_camara", lambda camara="principal": {"fps": ritmo["fps"], "formato": "MJPG"})
    seguimiento_vivo.iniciar(intervalo_s=1 / 30)  # un cuadro de la cámara (la ruta no admite menos de 0,05 s: la pantalla mira a 4 por segundo)
    seguimiento_vivo.avanzar(nuevo=True)
    assert seguimiento_vivo._actual.ticks_estable == 4 and seguimiento_vivo._actual.ticks_base == 30  # 30 cuadros por segundo: los de siempre
    ritmo["fps"] = 28.0
    seguimiento_vivo.avanzar(nuevo=True)
    assert seguimiento_vivo._actual.ticks_base == 30  # una diferencia chica no los mueve
    ritmo["fps"] = 10.0
    seguimiento_vivo.avanzar(nuevo=True)
    assert seguimiento_vivo._actual.ticks_estable == 2 and seguimiento_vivo._actual.ticks_base == 10  # a 10 cuadros por segundo: 0,2 s quieto y 1 s para la foto base
    info = seguimiento_vivo.rendimiento()
    assert info["fps_camara"] == 10.0 and info["formato"] == "MJPG" and info["ms_cuadro"] is not None and info["ms_cuadro"] >= 0


def test_rendimiento_sin_seguimiento_ni_camara_no_falla(monkeypatch) -> None:
    monkeypatch.setattr(seguimiento_vivo, "_rendimiento_camara", lambda camara="principal": None)
    assert seguimiento_vivo.rendimiento()["fps_camara"] is None
