"""Rutas /vision/dataset/*: captura, resumen, borrado y reentrenamiento, sin cámara ni subproceso real."""
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import training.capturar_dataset_propio as capturar
from backend.main import app
from backend.rutas.ruta_auth import get_current_facilitador
from backend.servicios.vision import dataset_propio, piezas

_ESQUINAS = np.array([[150, 80], [750, 40], [780, 620], [120, 660]], dtype=np.int32)
FEN = "8/8/8/8/8/8/8/8"


def _foto(*_a, **_k):
    imagen = np.full((700, 900, 3), 200, dtype=np.uint8)
    cv2.fillConvexPoly(imagen, _ESQUINAS, (60, 60, 60))
    return imagen


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(capturar, "CARPETA_DATASET", tmp_path)
    monkeypatch.setattr(capturar, "capturar_foto_tablero", _foto)
    import backend.servicios.vision.camara as camara_mod

    monkeypatch.setattr(camara_mod, "capturar_foto_tablero", _foto)
    monkeypatch.setattr(camara_mod, "_preferencia", {})  # sin cámara secundaria ni preferencias del equipo
    monkeypatch.delenv("CAMARA_FUENTE_2", raising=False)
    app.dependency_overrides[get_current_facilitador] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_facilitador, None)


def test_foto_resumen_y_deshacer(cliente) -> None:
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["guardadas"] == 64 and cuerpo["total"] == 64 and cuerpo["captura_id"].isdigit()
    resumen = cliente.get("/vision/dataset/resumen").json()
    assert resumen == {"por_clase": {"vacia": 64}, "total": 64, "capturas": 1, "minimo_recomendado": 1500}
    control = cliente.get("/vision/dataset/control")
    assert control.status_code == 200 and control.headers["content-type"] == "image/jpeg"
    assert cliente.delete(f"/vision/dataset/captura/{cuerpo['captura_id']}").json() == {"borradas": 64}
    assert cliente.get("/vision/dataset/resumen").json()["total"] == 0


def test_corregir_casilla_guarda_una_sola_muestra_en_la_clase_correcta(cliente, tmp_path) -> None:
    r = cliente.post("/vision/dataset/casilla", json={"casilla": "h7", "clase": "black-pawn"})
    assert r.status_code == 200 and r.json()["guardadas"] == 1 and r.json()["camaras"] == ["principal"]
    assert cliente.get("/vision/dataset/resumen").json()["por_clase"] == {"black-pawn": 1}
    assert [p.name.split("_")[-1] for p in (tmp_path / "black-pawn").glob("*.png")] == ["h7.png"]


def test_corregir_casilla_con_una_camara_elegida(cliente) -> None:
    r = cliente.post("/vision/dataset/casilla", json={"casilla": "h7", "clase": "black-pawn", "camaras": ["principal"]})
    assert r.status_code == 200 and r.json()["camaras"] == ["principal"]


def test_corregir_casilla_invalida_es_422(cliente) -> None:
    assert cliente.post("/vision/dataset/casilla", json={"casilla": "z9", "clase": "black-pawn"}).status_code == 422
    assert cliente.post("/vision/dataset/casilla", json={"casilla": "h7", "clase": "dragon"}).status_code == 422


def test_control_sin_imagen_es_404(cliente) -> None:
    assert cliente.get("/vision/dataset/control").status_code == 404


def test_fen_invalido_es_422(cliente) -> None:
    assert cliente.post("/vision/dataset/foto", json={"fen_piezas": "xyz"}).status_code == 422


def test_tablero_no_detectado_es_422(cliente, monkeypatch) -> None:
    def sin_tablero(imagen, *_a, **_k):
        raise ValueError("No se detectó ningún contorno en la imagen")

    monkeypatch.setattr(capturar, "esquinas_y_margen", sin_tablero)
    assert cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN}).status_code == 422


def test_camara_no_disponible_es_503(cliente, monkeypatch) -> None:
    def falla(*_a, **_k):
        raise RuntimeError("sin cámara")

    monkeypatch.setattr(capturar, "capturar_foto_tablero", falla)
    import backend.servicios.vision.camara as camara_mod

    monkeypatch.setattr(camara_mod, "capturar_foto_tablero", falla)
    assert cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN}).status_code == 503


def test_borrar_con_id_no_numerico_es_422(cliente) -> None:
    assert cliente.delete("/vision/dataset/captura/abc").status_code == 422


def _png(imagen=None) -> bytes:
    ok, buffer = cv2.imencode(".png", _foto() if imagen is None else imagen)
    assert ok
    return buffer.tobytes()


def test_subir_fotos_pieza_guarda_y_cuenta_en_resumen(cliente, tmp_path) -> None:
    pieza = np.full((40, 40, 3), 90, dtype=np.uint8)
    archivos = [("fotos", (f"p{i}.png", _png(pieza), "image/png")) for i in range(3)]
    r = cliente.post("/vision/dataset/pieza", data={"clase": "white-king"}, files=archivos)
    assert r.status_code == 200 and r.json() == {"guardadas": 3, "total_clase": 3}
    assert len(list((tmp_path / "white-king").glob("sub_*.png"))) == 3
    r2 = cliente.post("/vision/dataset/pieza", data={"clase": "white-king"}, files=archivos[:1])
    assert r2.json()["total_clase"] == 4
    assert cliente.get("/vision/dataset/resumen").json()["por_clase"] == {"white-king": 4}


def test_subir_fotos_pieza_clase_invalida_es_422(cliente) -> None:
    archivos = [("fotos", ("p.png", _png(), "image/png"))]
    assert cliente.post("/vision/dataset/pieza", data={"clase": "dragon"}, files=archivos).status_code == 422


def test_subir_fotos_pieza_ilegible_es_422_con_nombre(cliente) -> None:
    archivos = [("fotos", ("ok.png", _png(), "image/png")), ("fotos", ("rota.png", b"no es imagen", "image/png"))]
    r = cliente.post("/vision/dataset/pieza", data={"clase": "vacia"}, files=archivos)
    assert r.status_code == 422 and "rota.png" in r.json()["detail"]


def test_subir_fotos_pieza_mas_de_20_es_422(cliente) -> None:
    archivos = [("fotos", (f"p{i}.png", _png(), "image/png")) for i in range(21)]
    assert cliente.post("/vision/dataset/pieza", data={"clase": "vacia"}, files=archivos).status_code == 422


def test_subir_foto_tablero_guarda_64_casillas_y_control(cliente, monkeypatch) -> None:
    def camara_no_debe_usarse():
        raise AssertionError("no debe usar la cámara")

    monkeypatch.setattr(capturar, "capturar_foto_tablero", camara_no_debe_usarse)
    r = cliente.post(
        "/vision/dataset/foto-tablero",
        data={"fen_piezas": FEN},
        files={"foto": ("t.png", _png(), "image/png")},
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["guardadas"] == 64 and cuerpo["total"] == 64 and cuerpo["captura_id"].isdigit()
    assert cliente.get("/vision/dataset/control").status_code == 200


def test_foto_solo_piezas_no_guarda_vacias(cliente) -> None:
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": "8/8/8/8/8/8/8/R3K3", "solo_piezas": True})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["guardadas"] == 2 and cuerpo["total"] == 2 and cuerpo["captura_id"].isdigit()
    assert cliente.get("/vision/dataset/resumen").json()["por_clase"] == {"white-rook": 1, "white-king": 1}
    assert cliente.get("/vision/dataset/control").status_code == 200


def test_foto_solo_piezas_sin_piezas_es_422(cliente) -> None:
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN, "solo_piezas": True})
    assert r.status_code == 422
    assert cliente.get("/vision/dataset/resumen").json()["total"] == 0


def test_foto_tablero_solo_piezas(cliente) -> None:
    foto = {"foto": ("t.png", _png(), "image/png")}
    r = cliente.post(
        "/vision/dataset/foto-tablero", data={"fen_piezas": "8/8/8/8/8/8/8/R7", "solo_piezas": "true"}, files=foto
    )
    assert r.status_code == 200 and r.json()["guardadas"] == 1
    assert cliente.get("/vision/dataset/resumen").json()["por_clase"] == {"white-rook": 1}
    vacio = cliente.post(
        "/vision/dataset/foto-tablero", data={"fen_piezas": FEN, "solo_piezas": "true"}, files=foto
    )
    assert vacio.status_code == 422


def test_subir_foto_tablero_errores_422(cliente, monkeypatch) -> None:
    foto = {"foto": ("t.png", _png(), "image/png")}
    assert cliente.post("/vision/dataset/foto-tablero", data={"fen_piezas": "xyz"}, files=foto).status_code == 422
    ilegible = {"foto": ("t.png", b"basura", "image/png")}
    assert cliente.post("/vision/dataset/foto-tablero", data={"fen_piezas": FEN}, files=ilegible).status_code == 422

    def sin_tablero(imagen, *_a, **_k):
        raise ValueError("No se detectó ningún contorno en la imagen")

    monkeypatch.setattr(capturar, "esquinas_y_margen", sin_tablero)
    assert cliente.post("/vision/dataset/foto-tablero", data={"fen_piezas": FEN}, files=foto).status_code == 422


class _ProcesoFalso:
    def __init__(self, lineas, codigo):
        self.stdout = iter(lineas)
        self._codigo = codigo

    def wait(self):
        return self._codigo


def _esperar(cliente, estado):
    for _ in range(100):
        if cliente.get("/vision/dataset/reentrenar/estado").json()["estado"] == estado:
            return
        time.sleep(0.02)
    pytest.fail(f"no llegó al estado {estado}")


@pytest.fixture
def estado_limpio(monkeypatch):
    monkeypatch.setattr(dataset_propio, "_reentrenamiento", dataset_propio._Reentrenamiento())


def test_reentrenar_con_pocas_muestras_es_400(cliente, estado_limpio) -> None:
    assert cliente.post("/vision/dataset/reentrenar").status_code == 400


def test_reentrenar_exito_invalida_modelo_y_reporta_log(cliente, estado_limpio, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "MINIMO_PARA_REENTRENAR", 10)
    cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN})
    invalidado = []
    monkeypatch.setattr(piezas, "invalidar_modelo", lambda: invalidado.append(True))
    llamadas = []

    def popen(comando, **kwargs):
        llamadas.append((comando, kwargs))
        return _ProcesoFalso(["linea 1\n", "listo\n"], 0)

    monkeypatch.setattr(dataset_propio.subprocess, "Popen", popen)
    r = cliente.post("/vision/dataset/reentrenar")
    assert r.status_code == 200 and r.json() == {"estado": "en_curso"}
    assert llamadas[0][0][1:] == ["-m", "training.reentrenar_con_dataset_propio"]
    assert llamadas[0][1]["env"]["KMP_DUPLICATE_LIB_OK"] == "TRUE"
    _esperar(cliente, "terminado")
    assert cliente.get("/vision/dataset/reentrenar/estado").json()["log"] == "linea 1\nlisto"
    assert invalidado == [True]


def test_reentrenar_409_si_ya_hay_uno_corriendo(cliente, estado_limpio, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "MINIMO_PARA_REENTRENAR", 0)
    monkeypatch.setattr(dataset_propio, "_vigilar", lambda proceso: None)
    monkeypatch.setattr(dataset_propio.subprocess, "Popen", lambda *a, **k: _ProcesoFalso([], 0))
    assert cliente.post("/vision/dataset/reentrenar").status_code == 200
    assert cliente.post("/vision/dataset/reentrenar").status_code == 409


def test_reentrenar_proceso_con_error(cliente, estado_limpio, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "MINIMO_PARA_REENTRENAR", 0)
    monkeypatch.setattr(dataset_propio.subprocess, "Popen", lambda *a, **k: _ProcesoFalso(["boom\n"], 1))
    assert cliente.post("/vision/dataset/reentrenar").status_code == 200
    _esperar(cliente, "error")
    assert "boom" in cliente.get("/vision/dataset/reentrenar/estado").json()["log"]


def _esperar_candidato(cliente, estado):
    for _ in range(100):
        if cliente.get("/vision/dataset/candidato/estado").json()["estado"] == estado:
            return
        time.sleep(0.02)
    pytest.fail(f"no llegó al estado {estado}")


def test_candidato_reporta_progreso_y_resultado(cliente, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "_candidato", dataset_propio._Reentrenamiento())
    monkeypatch.setattr(dataset_propio, "MINIMO_PARA_REENTRENAR", 0)
    invalidado = []
    monkeypatch.setattr(piezas, "invalidar_modelo", lambda: invalidado.append(True))
    llamadas = []

    def popen(comando, **kwargs):
        llamadas.append((comando, kwargs))
        return _ProcesoFalso(["Dispositivo de entrenamiento: mps\n", "Época 3/50 — pérdida 0.5\n", "RESULTADO: reemplazado — mejor\n"], 0)

    monkeypatch.setattr(dataset_propio.subprocess, "Popen", popen)
    assert cliente.post("/vision/dataset/candidato").json() == {"estado": "en_curso"}
    assert llamadas[0][0][1:] == ["-m", "training.entrenar_candidato", "--recorte", "ampliado"]  # por defecto, solo desde la última calibración
    assert llamadas[0][1]["env"]["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
    _esperar_candidato(cliente, "terminado")
    estado = cliente.get("/vision/dataset/candidato/estado").json()
    assert estado["progreso"].startswith("Época 3/50") and estado["resultado"] == "reemplazado — mejor"
    assert invalidado == [True]


def test_candidato_3d_pasa_el_recorte_volumen_y_rechaza_uno_invalido(cliente, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "_candidato", dataset_propio._Reentrenamiento())
    monkeypatch.setattr(dataset_propio, "MINIMO_PARA_REENTRENAR", 0)
    llamadas = []
    monkeypatch.setattr(dataset_propio.subprocess, "Popen", lambda comando, **k: llamadas.append(comando) or _ProcesoFalso([], 0))
    assert cliente.post("/vision/dataset/candidato", params={"recorte": "dragon"}).status_code == 400
    assert cliente.post("/vision/dataset/candidato", params={"recorte": "volumen"}).status_code == 200
    assert llamadas[0][-2:] == ["--recorte", "volumen"]
    monkeypatch.setattr(dataset_propio, "_candidato", dataset_propio._Reentrenamiento())
    assert cliente.post("/vision/dataset/candidato", params={"solo_posicion_actual": "false"}).status_code == 200
    assert llamadas[1][-1] == "--todas-las-fotos"
    monkeypatch.setattr(dataset_propio, "_candidato", dataset_propio._Reentrenamiento())
    assert cliente.post("/vision/dataset/candidato", params={"incluir_secundaria": "true"}).status_code == 200
    assert llamadas[2][-1] == "--incluir-secundaria"


def test_candidato_con_pocas_muestras_es_400_y_no_pisa_al_reentrenamiento(cliente, monkeypatch) -> None:
    monkeypatch.setattr(dataset_propio, "_candidato", dataset_propio._Reentrenamiento())
    assert cliente.post("/vision/dataset/candidato").status_code == 400
    assert cliente.get("/vision/dataset/reentrenar/estado").json()["estado"] != "en_curso"


def test_export_zip_por_clase(cliente) -> None:
    import io
    import zipfile

    assert cliente.get("/vision/dataset/export").status_code == 404
    cliente.post("/vision/dataset/foto", json={"fen_piezas": "8/8/8/8/8/8/8/4K3", "solo_piezas": True})
    r = cliente.get("/vision/dataset/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zip_entrada:
        nombres = zip_entrada.namelist()
    assert "clases.json" in nombres and any(n.startswith("white-king/") and n.endswith(".png") for n in nombres)


def test_borrar_clase(cliente) -> None:
    cliente.post("/vision/dataset/foto", json={"fen_piezas": "8/8/8/8/8/8/8/4K3", "solo_piezas": True})
    assert cliente.delete("/vision/dataset/clase/white-king").json() == {"borradas": 1}
    assert cliente.get("/vision/dataset/resumen").json()["total"] == 0
    assert cliente.delete("/vision/dataset/clase/../x").status_code in (404, 405, 422)  # el cliente normaliza «..» y la ruta queda sin DELETE (405)
    assert cliente.delete("/vision/dataset/clase/inventada").status_code == 422


def test_vista_previa_no_guarda(cliente, monkeypatch) -> None:
    import backend.servicios.vision.camara as camara

    monkeypatch.setattr(camara, "capturar_foto_tablero", _foto)
    r = cliente.get("/vision/dataset/vista-previa", params={"casillas": "e4,f4"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert cliente.get("/vision/dataset/vista-previa", params={"casillas": "z9"}).status_code == 422
    assert cliente.get("/vision/dataset/resumen").json()["total"] == 0


def test_probar_piezas(cliente, monkeypatch) -> None:
    import backend.servicios.vision.verificacion as verificacion

    monkeypatch.setattr(verificacion, "capturar_foto_tablero", _foto)
    monkeypatch.setattr(
        verificacion.piezas, "clasificar_con_confianza", lambda recortes: [[("vacia", 0.9), ("white-pawn", 0.1)] for _ in recortes]
    )
    r = cliente.get("/vision/probar-piezas")
    assert r.status_code == 200
    cuerpo = r.json()
    assert len(cuerpo["casillas"]) == 64 and cuerpo["casillas"][0]["clase"] == "vacia" and cuerpo["imagen"]


def test_flujo_en_vivo_camara(cliente, monkeypatch) -> None:
    import backend.rutas.ruta_vision as ruta
    from backend.rutas.ruta_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 1
    monkeypatch.setattr(ruta, "flujo_de_frames", lambda *a, **k: iter([_foto(), _foto()]))
    try:
        with cliente.stream("GET", "/vision/flujo", params={"modo": "camara"}) as r:
            assert r.status_code == 200 and "multipart/x-mixed-replace" in r.headers["content-type"]
            cuerpo = b"".join(r.iter_bytes())
        assert cuerpo.count(b"--cuadro") == 2 and b"Content-Length" in cuerpo
        assert cliente.get("/vision/flujo", params={"modo": "x"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_listar_y_seleccionar_camara(cliente, monkeypatch) -> None:
    import backend.rutas.ruta_vision as ruta

    monkeypatch.setattr(ruta.servicio_camara, "listar_camaras", lambda: [{"indice": 0, "disponible": True, "ancho": 1280, "alto": 720}])
    monkeypatch.setattr(ruta.servicio_camara, "foto_de_indice", lambda i: _foto())
    elegida = []
    monkeypatch.setattr(ruta.servicio_camara, "seleccionar_fuente", lambda f, c="principal": elegida.append((f, c)))
    r = cliente.get("/vision/camaras")
    assert r.status_code == 200 and r.json()["camaras"][0]["disponible"] is True
    assert cliente.get("/vision/camaras/0/foto").headers["content-type"] == "image/jpeg"
    assert cliente.get("/vision/camaras/99/foto").status_code == 422
    assert cliente.post("/vision/camara", json={"fuente": "2"}).status_code == 200 and elegida == [(2, "principal")]
    assert cliente.post("/vision/camara", json={"fuente": "1", "camara": "secundaria"}).status_code == 200 and elegida[-1] == (1, "secundaria")
    assert cliente.post("/vision/camara", json={"fuente": "1", "camara": "tercera"}).status_code == 422
    assert cliente.post("/vision/camara", json={"fuente": "http://192.168.1.5:8080/video"}).status_code == 200
    assert elegida[-1] == ("http://192.168.1.5:8080/video", "principal")


def test_captura_doble_guarda_las_dos_camaras_con_el_mismo_grupo(cliente, tmp_path, monkeypatch) -> None:
    import json

    import backend.servicios.vision.camara as camara_mod
    from backend.servicios.vision import tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    tablero.guardar_esquinas_calibradas({"a1": (150, 660), "h1": (780, 620), "h8": (750, 40), "a8": (150, 80)}, tablero.archivo_esquinas("secundaria"))
    monkeypatch.setattr(camara_mod, "_preferencia", {"secundaria": {"fuente": "1"}, "ajustes": {"usar_secundaria": True}})
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN}).json()
    assert len(r["capturas"]) == 2 and r["camaras"] == ["principal", "secundaria"] and r["guardadas"] == 128
    manifiestos = [json.loads(m.read_text()) for m in (tmp_path / "dataset_propio_cuadros").glob("*.json")] if (tmp_path / "dataset_propio_cuadros").exists() else []
    assert not manifiestos or {m["grupo"] for m in manifiestos} == {r["capturas"][0]}
    for captura in r["capturas"]:
        assert cliente.delete(f"/vision/dataset/captura/{captura}").json() == {"borradas": 64}


def test_con_la_secundaria_apagada_solo_se_captura_con_la_principal(cliente, tmp_path, monkeypatch) -> None:
    """Aunque la secundaria esté configurada y calibrada, apagada no entra en las fotos de entrenamiento (antes salía su foto negra)."""
    import backend.servicios.vision.camara as camara_mod
    from backend.servicios.vision import tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    tablero.guardar_esquinas_calibradas({"a1": (150, 660), "h1": (780, 620), "h8": (750, 40), "a8": (150, 80)}, tablero.archivo_esquinas("secundaria"))
    monkeypatch.setattr(camara_mod, "_preferencia", {"secundaria": {"fuente": "1"}})
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": FEN}).json()
    assert r["camaras"] == ["principal"] and r["guardadas"] == 64


def test_interruptor_de_la_secundaria_y_estado_de_las_camaras(cliente, tmp_path, monkeypatch) -> None:
    import backend.servicios.vision.camara as camara_mod
    from backend.servicios.vision import tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara_mod, "ARCHIVO_PREFERENCIA", tmp_path / "preferencia.json")
    monkeypatch.setattr(camara_mod, "_preferencia", {"secundaria": {"fuente": "1"}})
    estado = cliente.get("/vision/camaras/estado").json()
    assert estado["configuradas"] == ["principal"] and estado["secundaria_configurada"] is True and estado["usar_secundaria"] is False
    assert cliente.post("/vision/camara/secundaria/uso", json={"activa": True}).json() == {"usar_secundaria": True, "camaras": ["principal", "secundaria"]}
    assert cliente.get("/vision/camaras/estado").json()["configuradas"] == ["principal", "secundaria"]
    assert cliente.post("/vision/camara/secundaria/uso", json={"activa": False}).json()["camaras"] == ["principal"]


def test_flujo_de_las_dos_camaras_junta_las_vistas(cliente, monkeypatch) -> None:
    import backend.rutas.ruta_vision as ruta
    import backend.servicios.vision.captura_estable as estable
    from backend.rutas.ruta_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 1
    monkeypatch.setattr(ruta, "flujo_de_frames", lambda *a, **k: iter([_foto()]))
    monkeypatch.setattr(ruta, "capturar_foto_tablero", _foto)
    monkeypatch.setattr(estable, "camaras_calibradas", lambda: ["principal", "secundaria"])
    try:
        with cliente.stream("GET", "/vision/flujo", params={"modo": "camara", "camara": "todas"}) as r:
            cuerpo = b"".join(r.iter_bytes())
        assert r.status_code == 200 and cuerpo.count(b"--cuadro") == 1
        assert cliente.get("/vision/flujo", params={"camara": "otra"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_flujo_con_limite_de_cuadros_por_segundo_salta_los_intermedios(cliente, monkeypatch) -> None:
    import backend.rutas.ruta_vision as ruta
    from backend.rutas.ruta_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 1
    monkeypatch.setattr(ruta, "flujo_de_frames", lambda *a, **k: iter([_foto(), _foto(), _foto(), _foto()]))
    try:
        with cliente.stream("GET", "/vision/flujo", params={"modo": "camara", "max_fps": 1}) as r:
            limitado = b"".join(r.iter_bytes()).count(b"--cuadro")
        monkeypatch.setattr(ruta, "flujo_de_frames", lambda *a, **k: iter([_foto(), _foto(), _foto(), _foto()]))
        with cliente.stream("GET", "/vision/flujo", params={"modo": "camara"}) as r:
            libre = b"".join(r.iter_bytes()).count(b"--cuadro")
        assert limitado == 1 and libre == 4  # cuadros que llegan juntos: con el límite solo se procesa el primero
        assert cliente.get("/vision/flujo", params={"max_fps": 500}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_los_ampliados_se_guardan_junto_a_los_recortes_y_se_borran_con_ellos(cliente, tmp_path, monkeypatch) -> None:
    import cv2
    import numpy as np

    import training.capturar_dataset_propio as capturar_mod

    esquinas = np.array([[150, 80], [750, 40], [780, 620], [120, 660]], dtype=np.float32)
    monkeypatch.setattr(capturar_mod, "esquinas_y_margen", lambda imagen, *a, **k: (esquinas, 0.0))
    r = cliente.post("/vision/dataset/foto", json={"fen_piezas": "8/8/8/8/8/8/8/4K3", "solo_piezas": True}).json()
    ampliados = list((capturar_mod.carpeta_ampliados() / "white-king").glob("*.png"))
    assert len(ampliados) == 1 and cv2.imread(str(ampliados[0])).shape == (96, 96, 3)
    for archivo in ampliados:
        archivo.unlink()
    assert cliente.post("/vision/dataset/regenerar-ampliados").json()["escritos"] == 1  # se rehace desde el cuadro completo
    assert len(list((capturar_mod.carpeta_ampliados() / "white-king").glob("*.png"))) == 1
    cliente.delete(f"/vision/dataset/captura/{r['captura_id']}")
    assert not list(capturar_mod.carpeta_ampliados().glob("*/*.png"))


def test_estado_de_los_modelos(cliente, monkeypatch) -> None:
    from backend.servicios.vision import ocupacion, piezas

    def sin_modelo():
        raise FileNotFoundError("no hay")

    monkeypatch.setattr(ocupacion, "modo_recorte", lambda: "base")
    monkeypatch.setattr(piezas, "modo_recorte", sin_modelo)
    monkeypatch.setattr(piezas, "hay_dos_vistas", lambda: False)
    monkeypatch.setattr(piezas, "motivo_dos_vistas_ignorado", lambda: None)
    assert cliente.get("/vision/modelos/estado").json() == {
        "ocupacion": {"existe": True, "recorte": "base"},
        "piezas": {"existe": False, "recorte": None},
        "dos_vistas": {"existe": False, "recorte": None, "ignorado": None},
    }


def test_flujo_de_piezas_valida_la_vista(cliente) -> None:
    from backend.rutas.ruta_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 1
    try:
        assert cliente.get("/vision/flujo", params={"modo": "piezas", "vista": "otra"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_problemas_de_posicion(cliente) -> None:
    from backend.rutas.ruta_auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 1
    try:
        respuesta = cliente.post("/vision/posicion/problemas", json={"fen": "4k2P/8/8/8/8/8/8/4K3 w - - 0 1"})
        assert respuesta.status_code == 200 and respuesta.json()["problemas"] == ["Hay peones en la primera o en la última fila"]
        assert cliente.post("/vision/posicion/problemas", json={"fen": "8/8/8/8/8/8/8/8"}).json() == {"problemas": []}
        assert cliente.post("/vision/posicion/problemas", json={"fen": "nada"}).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
