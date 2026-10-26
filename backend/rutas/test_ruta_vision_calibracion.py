"""`/vision/calibracion/{estado,previsualizar,guardar,comprobar}` con archivos de esquinas temporales y cámara falsa."""
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.rutas import ruta_vision_calibracion
from backend.rutas.ruta_auth import get_current_facilitador
from backend.servicios.vision import tablero

ANCHO, ALTO = 1280, 720
ESQUINAS = {"a1": [400, 600], "h1": [900, 610], "h8": [890, 110], "a8": [410, 100]}


def _foto(desplazamiento: int = 0) -> np.ndarray:
    azar = np.random.default_rng(3)
    imagen = np.full((ALTO, ANCHO, 3), 127, np.uint8)
    for _ in range(250):
        centro = (int(azar.integers(0, ANCHO)), int(azar.integers(0, ALTO)))
        cv2.circle(imagen, centro, int(azar.integers(4, 30)), tuple(int(c) for c in azar.integers(0, 255, 3)), -1)
    if desplazamiento:
        imagen = cv2.warpAffine(imagen, np.float32([[1, 0, desplazamiento], [0, 1, 0]]), (ANCHO, ALTO), borderValue=(127, 127, 127))
    return imagen


def _jpeg(imagen: np.ndarray) -> bytes:
    return cv2.imencode(".png", imagen)[1].tobytes()


@pytest.fixture
def camara(monkeypatch):
    """Cámara falsa: `camara.imagen` es lo que devuelve; `None` simula una cámara sin imagen."""
    estado = type("Camara", (), {"imagen": _foto()})()

    def _capturar(*_a, **_k):
        if estado.imagen is None:
            raise RuntimeError("sin cámara")
        return estado.imagen

    monkeypatch.setattr(ruta_vision_calibracion, "capturar_foto_tablero", _capturar)
    monkeypatch.setattr(ruta_vision_calibracion, "_fuente_por_defecto", lambda camara="principal": 0)
    return estado


@pytest.fixture
def cliente(tmp_path, monkeypatch, camara):
    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    app.dependency_overrides[get_current_facilitador] = lambda: 1
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_facilitador, None)


def _guardar(cliente, esquinas=ESQUINAS, imagen=None):
    return cliente.post(
        "/vision/calibracion/guardar",
        data={"esquinas": json.dumps(esquinas)},
        files={"foto": ("foto.png", _jpeg(_foto() if imagen is None else imagen), "image/png")},
    )


def test_estado_sin_calibrar(cliente) -> None:
    cuerpo = cliente.get("/vision/calibracion/estado").json()
    assert cuerpo["calibrada"] is False and cuerpo["vigente"] is False and cuerpo["motivo_no_vigente"]


def test_guardar_deja_la_calibracion_vigente_y_compatible_con_la_ruta_vieja(cliente) -> None:
    respuesta = _guardar(cliente)
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["calibrada"] is True and cuerpo["vigente"] is True
    assert cuerpo["resolucion"] == [ANCHO, ALTO] and cuerpo["fuente"] == "0" and cuerpo["advertencias"] == []
    assert cliente.get("/vision/calibracion/estado").json()["vigente"] is True
    assert cliente.get("/vision/calibracion").json()["esquinas"]["h1"] == [900.0, 610.0]
    assert tablero.cargar_esquinas_calibradas() is not None


def test_guardar_con_tablero_chico_guarda_y_avisa(cliente) -> None:
    chico = {"a1": [600, 400], "h1": [680, 400], "h8": [680, 330], "a8": [600, 330]}
    respuesta = _guardar(cliente, chico)
    assert respuesta.status_code == 200
    assert any("muy poco de la foto" in a for a in respuesta.json()["advertencias"])


@pytest.mark.parametrize(
    "esquinas",
    [
        {"a1": [400, 600], "h1": [900, 610], "h8": [890, 110]},
        {**ESQUINAS, "h1": [-5, 610]},
        {"a1": [400, 600], "h1": [900, 610], "h8": [410, 110], "a8": [890, 100]},
        {**ESQUINAS, "h1": [5000, 610]},
    ],
)
def test_guardar_invalido_es_422_y_no_guarda(cliente, esquinas) -> None:
    assert _guardar(cliente, esquinas).status_code == 422
    assert cliente.get("/vision/calibracion").json()["calibrada"] is False


def test_guardar_con_esquinas_que_no_son_json_es_422(cliente) -> None:
    respuesta = cliente.post(
        "/vision/calibracion/guardar", data={"esquinas": "no es json"}, files={"foto": ("f.png", _jpeg(_foto()), "image/png")}
    )
    assert respuesta.status_code == 422


def test_guardar_con_foto_ilegible_es_422(cliente) -> None:
    respuesta = cliente.post(
        "/vision/calibracion/guardar", data={"esquinas": json.dumps(ESQUINAS)}, files={"foto": ("f.png", b"basura", "image/png")}
    )
    assert respuesta.status_code == 422


def test_camara_con_otra_resolucion_vence_la_calibracion(cliente, camara) -> None:
    _guardar(cliente)
    camara.imagen = cv2.resize(_foto(), (640, 360))
    cuerpo = cliente.get("/vision/calibracion/estado").json()
    assert cuerpo["calibrada"] is True and cuerpo["vigente"] is False and "640×360" in cuerpo["motivo_no_vigente"]


def test_camara_sin_imagen_deja_vigente_en_null(cliente, camara) -> None:
    _guardar(cliente)
    camara.imagen = None
    cuerpo = cliente.get("/vision/calibracion/estado").json()
    assert cuerpo["calibrada"] is True and cuerpo["vigente"] is None


def test_previsualizar_devuelve_imagen_y_no_guarda(cliente) -> None:
    respuesta = cliente.post(
        "/vision/calibracion/previsualizar",
        data={"esquinas": json.dumps(ESQUINAS)},
        files={"foto": ("f.png", _jpeg(_foto()), "image/png")},
    )
    assert respuesta.status_code == 200 and respuesta.headers["content-type"] == "image/jpeg"
    assert cv2.imdecode(np.frombuffer(respuesta.content, np.uint8), cv2.IMREAD_COLOR) is not None
    assert cliente.get("/vision/calibracion").json()["calibrada"] is False


def test_comprobar_con_la_camara_en_su_lugar(cliente) -> None:
    _guardar(cliente)
    cuerpo = cliente.post("/vision/calibracion/comprobar").json()
    assert cuerpo["movida"] is False


def test_comprobar_avisa_si_la_camara_se_movio(cliente, camara) -> None:
    _guardar(cliente)
    camara.imagen = _foto(desplazamiento=20)
    cuerpo = cliente.post("/vision/calibracion/comprobar").json()
    assert cuerpo["movida"] is True and "volvé a calibrar" in cuerpo["mensaje"]


def test_comprobar_con_foto_subida(cliente) -> None:
    _guardar(cliente)
    respuesta = cliente.post("/vision/calibracion/comprobar", files={"foto": ("f.png", _jpeg(_foto(desplazamiento=20)), "image/png")})
    assert respuesta.status_code == 200 and respuesta.json()["movida"] is True


def test_comprobar_sin_camara_es_503(cliente, camara) -> None:
    camara.imagen = None
    assert cliente.post("/vision/calibracion/comprobar").status_code == 503


def test_sin_token_es_rechazado(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    anonimo = TestClient(app)
    assert anonimo.get("/vision/calibracion/estado").status_code in (401, 403)
    assert anonimo.post("/vision/calibracion/comprobar").status_code in (401, 403)
    assert anonimo.post("/vision/calibracion/guardar", data={"esquinas": "{}"}).status_code in (401, 403)


def test_estado_de_las_camaras_y_quitar_calibracion(cliente, tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara, tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara, "_preferencia", {"secundaria": {"fuente": "1"}, "ajustes": {"usar_secundaria": True}})
    tablero.guardar_esquinas_calibradas({"a1": (10, 90), "h1": (90, 90), "h8": (90, 10), "a8": (10, 10)}, tablero.archivo_esquinas("secundaria"))
    estado = cliente.get("/vision/camaras/estado").json()
    assert estado["configuradas"] == ["principal", "secundaria"] and estado["calibradas"] == ["secundaria"]
    assert cliente.delete("/vision/calibracion/quitar", params={"camara": "secundaria"}).status_code == 200
    assert cliente.get("/vision/camaras/estado").json()["calibradas"] == []
    assert cliente.delete("/vision/calibracion/quitar", params={"camara": "otra"}).status_code == 422


def test_verificacion_unificada_pide_las_dos_calibraciones(cliente, tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara, tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara, "_preferencia", {})
    monkeypatch.delenv("CAMARA_FUENTE_2", raising=False)
    assert cliente.get("/vision/calibracion/unificada").status_code == 409  # sin cámara secundaria
    monkeypatch.setattr(camara, "_preferencia", {"secundaria": {"fuente": "1"}})
    assert cliente.get("/vision/calibracion/unificada").status_code == 409  # configurada pero sin calibrar


def test_verificacion_unificada_devuelve_el_informe(cliente, tmp_path, monkeypatch) -> None:
    import cv2
    import numpy as np

    from backend.servicios.vision import camara, tablero

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara, "_preferencia", {"secundaria": {"fuente": "1"}})
    imagen = np.random.default_rng(1).integers(0, 255, (700, 900, 3), dtype=np.uint8)
    cv2.rectangle(imagen, (150, 80), (750, 640), (30, 30, 30), -1)
    puntos = {"a1": (150, 640), "h1": (750, 640), "h8": (750, 80), "a8": (150, 80)}
    tablero.guardar_esquinas_calibradas(puntos)
    tablero.guardar_esquinas_calibradas(puntos, tablero.archivo_esquinas("secundaria"))
    monkeypatch.setattr(ruta_vision_calibracion, "capturar_foto_tablero", lambda *a, **k: imagen)
    informe = cliente.get("/vision/calibracion/unificada").json()
    assert informe["nivel"] == "bien" and informe["imagen"] and informe["error_casillas"] < 0.15


def test_ajustar_esquinas_las_mejora_y_las_guarda(cliente, tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara
    from backend.servicios.vision.test_unificada import _camara, _tablero_sintetico

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara, "_preferencia", {"principal": {"fuente": "0"}})
    foto, verdaderas = _camara(_tablero_sintetico(), 0, 40)  # a8, h8, h1, a1
    marcadas = verdaderas + np.float32([[6, -4], [-5, 7], [4, 5], [-7, -3]])
    tablero.guardar_esquinas_calibradas({n: tuple(p) for n, p in zip(tablero.ORDEN_DE_ENDEREZADO, marcadas)})
    monkeypatch.setattr(ruta_vision_calibracion, "capturar_foto_tablero", lambda *a, **k: foto)

    respuesta = cliente.post("/vision/calibracion/ajustar?camara=principal").json()

    assert respuesta["aplicada"] and respuesta["error_despues_casillas"] < respuesta["error_antes_casillas"]
    guardadas = tablero.cargar_esquinas_calibradas()
    assert np.abs(guardadas - verdaderas).max() < 1.5
    assert cliente.post("/vision/calibracion/ajustar?camara=principal").json()["aplicada"] is False  # ya no hay nada que mejorar


def test_perfiles_arranca_con_solo_el_por_defecto(cliente) -> None:
    cuerpo = cliente.get("/vision/calibracion/perfiles").json()
    assert cuerpo["perfiles"] == [{"nombre": "default", "activo": True, "calibrada": False, "fecha": None}]


def test_guardar_con_perfil_nuevo_no_pisa_el_por_defecto_y_queda_activo(cliente) -> None:
    _guardar(cliente)  # calibra el perfil por defecto
    otras = {"a1": [420, 610], "h1": [910, 620], "h8": [900, 120], "a8": [420, 110]}
    respuesta = cliente.post(
        "/vision/calibracion/guardar",
        data={"esquinas": json.dumps(otras), "perfil": "mesa_2"},
        files={"foto": ("foto.png", _jpeg(_foto()), "image/png")},
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["perfil"] == "mesa_2" and cuerpo["calibrada"] is True

    # El por defecto sigue exactamente como se guardó antes: no se pisó.
    estado_default = cliente.get("/vision/calibracion/estado", params={"perfil": "default"}).json()
    assert estado_default["calibrada"] is True and estado_default["esquinas"]["h1"] == [900.0, 610.0]

    # Y ahora es "mesa_2" el que está activo (sin pasar perfil, el estado lee el activo).
    estado_activo = cliente.get("/vision/calibracion/estado").json()
    assert estado_activo["perfil"] == "mesa_2" and estado_activo["esquinas"]["h1"] == [910.0, 620.0]

    perfiles = {p["nombre"]: p for p in cliente.get("/vision/calibracion/perfiles").json()["perfiles"]}
    assert set(perfiles) == {"default", "mesa_2"}
    assert perfiles["mesa_2"]["activo"] is True and perfiles["default"]["activo"] is False


def test_activar_perfil_vuelve_al_tablero_elegido_sin_recalibrar(cliente) -> None:
    _guardar(cliente)
    otras = {"a1": [420, 610], "h1": [910, 620], "h8": [900, 120], "a8": [420, 110]}
    cliente.post(
        "/vision/calibracion/guardar",
        data={"esquinas": json.dumps(otras), "perfil": "mesa_2"},
        files={"foto": ("foto.png", _jpeg(_foto()), "image/png")},
    )
    respuesta = cliente.post("/vision/calibracion/perfiles/activar", params={"camara": "principal", "perfil": "default"})
    assert respuesta.status_code == 200
    assert respuesta.json()["perfil"] == "default" and respuesta.json()["esquinas"]["h1"] == [900.0, 610.0]
    assert cliente.get("/vision/calibracion/estado").json()["esquinas"]["h1"] == [900.0, 610.0]


def test_activar_perfil_inexistente_es_404(cliente) -> None:
    assert cliente.post("/vision/calibracion/perfiles/activar", params={"perfil": "no_existe"}).status_code == 404


def test_borrar_perfil_no_borra_el_por_defecto_y_cae_a_el_si_era_el_activo(cliente) -> None:
    _guardar(cliente)
    cliente.post(
        "/vision/calibracion/guardar",
        data={"esquinas": json.dumps(ESQUINAS), "perfil": "mesa_2"},
        files={"foto": ("foto.png", _jpeg(_foto()), "image/png")},
    )
    assert cliente.get("/vision/calibracion/estado").json()["perfil"] == "mesa_2"

    assert cliente.delete("/vision/calibracion/perfiles/default").status_code == 400

    respuesta = cliente.delete("/vision/calibracion/perfiles/mesa_2")
    assert respuesta.status_code == 200
    assert [p["nombre"] for p in respuesta.json()["perfiles"]] == ["default"]
    # Se quedó sin perfil activo: vuelve solo al por defecto.
    assert cliente.get("/vision/calibracion/estado").json()["perfil"] == "default"


def test_ajustar_esquinas_sin_damero_visible_es_409(cliente, tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara

    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")
    monkeypatch.setattr(camara, "_preferencia", {"principal": {"fuente": "0"}})
    assert cliente.post("/vision/calibracion/ajustar?camara=principal").status_code == 409  # sin calibrar
    tablero.guardar_esquinas_calibradas(ESQUINAS)
    monkeypatch.setattr(ruta_vision_calibracion, "capturar_foto_tablero", lambda *a, **k: _foto())
    assert cliente.post("/vision/calibracion/ajustar?camara=principal").status_code == 409  # foto sin damero
