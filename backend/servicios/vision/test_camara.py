"""Tests de la captura por cámara.

El test contra una cámara real se saltea si esta máquina no tiene ninguna
conectada — el de manejo de error (índice inválido) sí corre siempre, sin
necesitar hardware.
"""
from __future__ import annotations

import cv2
import pytest

from backend.servicios.vision.camara import _api_por_defecto, _fuente_por_defecto, capturar_foto_tablero


def _hay_camara_disponible() -> bool:
    camara = cv2.VideoCapture(0)
    disponible = camara.isOpened()
    camara.release()
    return disponible


CAMARA_DISPONIBLE = _hay_camara_disponible()


def test_capturar_foto_camara_invalida_lanza_error() -> None:
    with pytest.raises(RuntimeError):
        capturar_foto_tablero(fuente=99)


@pytest.mark.skipif(not CAMARA_DISPONIBLE, reason="No hay cámara conectada en esta máquina")
def test_capturar_foto_tablero_devuelve_imagen_valida() -> None:
    imagen = capturar_foto_tablero(fuente=0)
    assert imagen is not None
    assert imagen.ndim == 3


@pytest.fixture(autouse=True)
def _sin_preferencias_guardadas(tmp_path, monkeypatch):
    """Ningún test lee la cámara que se eligió en la app de esta máquina (`calibracion_local/camara.json`): usan un archivo vacío."""
    from backend.servicios.vision import camara

    monkeypatch.setattr(camara, "ARCHIVO_PREFERENCIA", tmp_path / "camara_de_prueba.json")
    monkeypatch.setattr(camara, "_preferencia", None)


def test_fuente_por_defecto_es_cero_sin_variable_de_entorno(monkeypatch) -> None:
    monkeypatch.delenv("CAMARA_FUENTE", raising=False)
    assert _fuente_por_defecto() == 0


def test_fuente_por_defecto_interpreta_numero_como_indice(monkeypatch) -> None:
    monkeypatch.setenv("CAMARA_FUENTE", "2")
    assert _fuente_por_defecto() == 2


def test_fuente_por_defecto_interpreta_url_como_string(monkeypatch) -> None:
    monkeypatch.setenv("CAMARA_FUENTE", "http://192.168.1.23:8080/video")
    assert _fuente_por_defecto() == "http://192.168.1.23:8080/video"


def test_api_por_defecto_sin_configurar_deja_elegir_a_opencv(monkeypatch) -> None:
    monkeypatch.delenv("CAMARA_API", raising=False)

    assert _api_por_defecto() is None


def test_api_por_defecto_acepta_msmf_y_dshow_sin_importar_mayusculas(monkeypatch) -> None:
    monkeypatch.setenv("CAMARA_API", "msmf")
    assert _api_por_defecto() == cv2.CAP_MSMF
    monkeypatch.setenv("CAMARA_API", " DShow ")
    assert _api_por_defecto() == cv2.CAP_DSHOW
    monkeypatch.setenv("CAMARA_API", "otra")
    assert _api_por_defecto() is None


def test_rotacion_valida_y_aplica_el_giro(tmp_path, monkeypatch) -> None:
    import numpy as np

    from backend.servicios.vision import camara

    monkeypatch.setattr(camara, "ARCHIVO_PREFERENCIA", tmp_path / "camara.json")
    monkeypatch.setattr(camara, "_preferencia", {})
    monkeypatch.delenv("CAMARA_ROTACION", raising=False)
    assert camara.fijar_rotacion(180) == 180 and camara.rotacion_actual() == 180
    imagen = np.zeros((2, 3, 3), dtype=np.uint8)
    imagen[0, 0] = 255
    assert camara._rotar(imagen)[1, 2].tolist() == [255, 255, 255]
    camara.fijar_rotacion(90)
    assert camara._rotar(imagen).shape == (3, 2, 3)
    assert camara.rotacion_actual("secundaria") == 0  # cada cámara tiene su giro
    with pytest.raises(ValueError):
        camara.fijar_rotacion(45)
    with pytest.raises(ValueError):
        camara.fijar_rotacion(90, "tercera")


def test_la_camara_y_el_giro_elegidos_se_recuerdan_por_camara(tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara

    monkeypatch.setattr(camara, "ARCHIVO_PREFERENCIA", tmp_path / "camara.json")
    monkeypatch.setattr(camara, "_preferencia", None)
    monkeypatch.setenv("CAMARA_FUENTE", "3")
    monkeypatch.delenv("CAMARA_FUENTE_2", raising=False)
    assert camara._fuente_por_defecto() == 3  # sin preferencia manda el .env
    assert camara._fuente_por_defecto("secundaria") is None and camara.camaras_configuradas() == ["principal"]
    camara._guardar_preferencia("principal", fuente="1")
    camara._guardar_preferencia("secundaria", fuente="2")
    camara.fijar_rotacion(90, "secundaria")
    assert camara._fuente_por_defecto() == 1 and camara.camaras_configuradas() == ["principal", "secundaria"]
    monkeypatch.setattr(camara, "_preferencia", None)  # "reinicio": se vuelve a leer del archivo
    assert camara._fuente_por_defecto("secundaria") == 2 and camara.rotacion_actual("secundaria") == 90


def test_preferencia_en_el_formato_viejo_se_lee_como_camara_principal(tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara

    archivo = tmp_path / "camara.json"
    archivo.write_text('{"fuente": "2", "rotacion": 180}', encoding="utf-8")
    monkeypatch.setattr(camara, "ARCHIVO_PREFERENCIA", archivo)
    monkeypatch.setattr(camara, "_preferencia", None)
    assert camara._fuente_por_defecto() == 2 and camara.rotacion_actual() == 180


def test_dos_camaras_no_pueden_usar_la_misma_fuente(tmp_path, monkeypatch) -> None:
    from backend.servicios.vision import camara

    monkeypatch.setattr(camara, "ARCHIVO_PREFERENCIA", tmp_path / "camara.json")
    monkeypatch.setattr(camara, "_preferencia", {"principal": {"fuente": "0"}})
    with pytest.raises(ValueError):
        camara.seleccionar_fuente(0, "secundaria")


def test_buscar_camaras_no_se_cuelga_con_una_que_no_contesta(monkeypatch) -> None:
    """Una cámara virtual o un driver trabado que no responde se da por no disponible y la búsqueda sigue con la siguiente."""
    import threading
    import time

    import numpy as np

    from backend.servicios.vision import camara as servicio_camara

    class CamaraFalsa:
        def __init__(self, indice, *_api) -> None:
            self.indice = indice

        def isOpened(self) -> bool:  # noqa: N802 — nombre de OpenCV
            if self.indice == 1:
                threading.Event().wait(5)  # no contesta nunca (más que el tiempo máximo del test)
            return self.indice != 2

        def read(self):
            return True, np.zeros((480, 640, 3), np.uint8)

        def release(self) -> None:
            pass

    monkeypatch.setattr(servicio_camara.cv2, "VideoCapture", CamaraFalsa)
    monkeypatch.setattr(servicio_camara, "SEGUNDOS_MAXIMOS_POR_CAMARA", 0.3)
    monkeypatch.setattr(servicio_camara, "soltar_camara", lambda: None)

    inicio = time.monotonic()
    resultado = servicio_camara.listar_camaras(maximo=4)

    assert time.monotonic() - inicio < 3, "la búsqueda esperó a la cámara que no contesta"
    por_indice = {c["indice"]: c for c in resultado}
    assert por_indice[0]["disponible"] is True and (por_indice[0]["ancho"], por_indice[0]["alto"]) == (640, 480)
    assert por_indice[0]["miniatura"].startswith("data:image/jpeg;base64,")  # trae su miniatura: no hace falta una foto por cámara
    assert por_indice[1]["miniatura"] is None
    assert por_indice[1]["disponible"] is False  # no contestó a tiempo
    assert por_indice[2]["disponible"] is False  # no abre
    assert por_indice[3]["disponible"] is True


def test_una_camara_que_no_termina_de_abrir_da_un_error_claro_y_no_cuelga(monkeypatch) -> None:
    """Una webcam USB trabada no puede dejar colgado el cerrojo de la cámara: pasado el tiempo máximo se avisa y, si abre después, se suelta sola."""
    import threading
    import time

    from backend.servicios.vision import camara as servicio_camara

    soltada = threading.Event()

    class CamaraLenta:
        def __init__(self, *_args) -> None:
            threading.Event().wait(0.8)  # tarda más que el límite del test

        def release(self) -> None:
            soltada.set()

    monkeypatch.setattr(servicio_camara.cv2, "VideoCapture", CamaraLenta)
    monkeypatch.setattr(servicio_camara, "SEGUNDOS_MAXIMOS_PARA_ABRIR", 0.2)

    inicio = time.monotonic()
    with pytest.raises(RuntimeError, match="no responde"):
        servicio_camara._abrir_con_limite(4)
    assert time.monotonic() - inicio < 0.7, "esperó a la cámara trabada"
    assert soltada.wait(3), "la cámara que abrió tarde quedó tomada"


def test_la_miniatura_de_una_camara_que_no_entrega_imagen_no_cuelga_la_pantalla(monkeypatch) -> None:
    """Una cámara virtual que abre pero nunca entrega una imagen no puede dejar tomado el cerrojo de las cámaras."""
    import threading
    import time

    from backend.servicios.vision import camara as servicio_camara

    monkeypatch.setattr(servicio_camara, "soltar_camara", lambda: None)
    monkeypatch.setattr(servicio_camara, "SEGUNDOS_MAXIMOS_POR_MINIATURA", 0.3)
    monkeypatch.setattr(servicio_camara, "_capturar_sin_cerrojo", lambda indice, camara="principal": threading.Event().wait(3))

    inicio = time.monotonic()
    with pytest.raises(RuntimeError, match="no entregó una imagen"):
        servicio_camara.foto_de_indice(2)
    assert time.monotonic() - inicio < 2
    assert servicio_camara._CERROJO_CAMARA.acquire(timeout=1), "el cerrojo de las cámaras quedó tomado"
    servicio_camara._CERROJO_CAMARA.release()


def test_buscar_camaras_las_prueba_a_la_vez_y_no_una_tras_otra(monkeypatch) -> None:
    """Con 5 cámaras lentas, la búsqueda tarda lo que tarda UNA y no la suma."""
    import threading
    import time

    import numpy as np

    from backend.servicios.vision import camara as servicio_camara

    class CamaraLenta:
        def __init__(self, indice, *_api) -> None:
            threading.Event().wait(0.5)

        def isOpened(self) -> bool:  # noqa: N802 — nombre de OpenCV
            return True

        def read(self):
            return True, np.zeros((480, 640, 3), np.uint8)

        def release(self) -> None:
            pass

    monkeypatch.setattr(servicio_camara.cv2, "VideoCapture", CamaraLenta)
    monkeypatch.setattr(servicio_camara, "soltar_camara", lambda: None)

    inicio = time.monotonic()
    resultado = servicio_camara.listar_camaras(maximo=5)

    assert time.monotonic() - inicio < 1.8, "las probó una por una (5 x 0,5 s serían 2,5 s)"
    assert all(c["disponible"] for c in resultado)


def test_esperar_foto_nueva_devuelve_cada_cuadro_una_sola_vez(monkeypatch) -> None:
    """Pedir cuadros más rápido de lo que la cámara los entrega no repite ninguno: sin cuadro posterior, `None`."""
    import numpy as np

    from backend.servicios.vision import camara

    class CapturaFalsa:
        def __init__(self) -> None:
            self.numero = 5
            self.ultimo_cuadro_en = __import__("time").monotonic()  # llegó un cuadro hace un instante: la cámara no está colgada

        def ultimo_frame(self):
            return np.zeros((2, 2, 3), np.uint8)

        def esperar_frame(self, despues_de, timeout=0.25):
            return (self.numero, np.zeros((2, 2, 3), np.uint8)) if self.numero > despues_de else None

    falsa = CapturaFalsa()
    monkeypatch.setattr(camara, "_fuente_por_defecto", lambda _camara: 0)
    monkeypatch.setattr(camara, "_captura_continua", lambda fuente, _camara="principal": falsa)
    assert camara.esperar_foto_nueva(0)[0] == 5
    assert camara.esperar_foto_nueva(5) is None  # todavía no llegó otro
    falsa.numero = 6
    assert camara.esperar_foto_nueva(5)[0] == 6


def test_una_camara_que_deja_de_entregar_cuadros_se_reinicia(monkeypatch) -> None:
    """Sin cuadros nuevos durante varios segundos la cámara se da por colgada: se suelta y se avisa (el próximo pedido la vuelve a abrir)."""
    import time

    import numpy as np

    from backend.servicios.vision import camara

    class CapturaColgada:
        ultimo_cuadro_en = time.monotonic() - 10.0
        detenida = False

        def ultimo_frame(self):
            return np.zeros((2, 2, 3), np.uint8)

        def esperar_frame(self, despues_de, timeout=0.25):
            return None

        def detener(self):
            self.detenida = True

    colgada = CapturaColgada()
    monkeypatch.setattr(camara, "_fuente_por_defecto", lambda _camara: 0)
    monkeypatch.setattr(camara, "_captura_continua", lambda fuente, _camara="principal": colgada)
    monkeypatch.setitem(camara._capturas, "principal", colgada)
    with pytest.raises(RuntimeError, match="reiniciando"):
        camara.esperar_foto_nueva(7)
    assert colgada.detenida and "principal" not in camara._capturas


class _CamaraFalsa:
    """Una `cv2.VideoCapture` de mentira: guarda lo que se le pide y entrega cuadros de `forma`. Si se le pide MJPG pasa a entregar `forma_con_mjpg`."""

    def __init__(self, forma=(720, 1280, 3), forma_con_mjpg=None) -> None:
        self.forma, self.forma_con_mjpg = forma, forma_con_mjpg
        self.pedidos: list[tuple[int, float]] = []
        self.liberada = False

    def isOpened(self) -> bool:
        return True

    def set(self, propiedad, valor) -> bool:
        self.pedidos.append((propiedad, valor))
        if propiedad == cv2.CAP_PROP_FOURCC and self.forma_con_mjpg is not None:
            self.forma = self.forma_con_mjpg
        return True

    def read(self):
        import time

        import numpy as np

        time.sleep(0.002)
        return True, np.zeros(self.forma, np.uint8)

    def release(self) -> None:
        self.liberada = True


def _captura_con(monkeypatch, camaras: list) -> object:
    """Una `_CapturaContinua` armada con las cámaras falsas dadas (una por cada vez que se abre)."""
    from backend.servicios.vision import camara

    pendientes = list(camaras)
    monkeypatch.setattr(camara, "_abrir_con_limite", lambda fuente: pendientes.pop(0))
    return camara._CapturaContinua(0, "principal")


def test_la_camara_local_se_configura_en_mjpg_a_30_cuadros_por_segundo(monkeypatch) -> None:
    """MJPG es lo que permite 30 cuadros por segundo a 1280x720 por USB: se pide, con el tamaño de siempre, y se queda si la cámara lo acepta."""
    monkeypatch.delenv("CAMARA_MJPG", raising=False)
    falsa = _CamaraFalsa()
    captura = _captura_con(monkeypatch, [falsa])
    try:
        assert captura.formato == "MJPG"
        assert (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")) in falsa.pedidos
        assert (cv2.CAP_PROP_FPS, 30) in falsa.pedidos
        assert (cv2.CAP_PROP_FRAME_WIDTH, 1280) in falsa.pedidos and (cv2.CAP_PROP_FRAME_HEIGHT, 720) in falsa.pedidos
        assert not falsa.liberada
    finally:
        captura.detener()


def test_si_con_mjpg_cambia_el_tamano_del_cuadro_se_vuelve_a_la_configuracion_de_antes(monkeypatch) -> None:
    """Las esquinas del tablero están marcadas sobre 1280x720: una cámara que con MJPG entrega otro tamaño se reabre sin MJPG (nunca queda peor)."""
    monkeypatch.delenv("CAMARA_MJPG", raising=False)
    rara, normal = _CamaraFalsa(forma_con_mjpg=(480, 640, 3)), _CamaraFalsa()
    captura = _captura_con(monkeypatch, [rara, normal])
    try:
        assert captura.formato == "predeterminado"
        assert rara.liberada and not normal.liberada
        assert not any(p == cv2.CAP_PROP_FOURCC for p, _ in normal.pedidos)
        assert (cv2.CAP_PROP_FRAME_WIDTH, 1280) in normal.pedidos
    finally:
        captura.detener()


def test_camara_mjpg_cero_no_toca_el_formato(monkeypatch) -> None:
    monkeypatch.setenv("CAMARA_MJPG", "0")
    falsa = _CamaraFalsa()
    captura = _captura_con(monkeypatch, [falsa])
    try:
        assert captura.formato == "predeterminado"
        assert not any(p in (cv2.CAP_PROP_FOURCC, cv2.CAP_PROP_FPS) for p, _ in falsa.pedidos)
    finally:
        captura.detener()


def test_los_cuadros_por_segundo_se_miden_sobre_los_ultimos_cuadros_recibidos(monkeypatch) -> None:
    import time

    monkeypatch.setenv("CAMARA_MJPG", "0")
    captura = _captura_con(monkeypatch, [_CamaraFalsa()])
    try:
        deadline = time.monotonic() + 3.0
        while captura.fps is None and time.monotonic() < deadline:
            time.sleep(0.01)
        medido = captura.fps
        assert medido is not None and 20 <= medido <= 600  # la falsa entrega uno cada ~2 ms
        from backend.servicios.vision import camara

        monkeypatch.setitem(camara._capturas, "principal", captura)
        info = camara.rendimiento("principal")
        assert info["formato"] == "predeterminado" and info["fps"] > 0 and info["ancho"] == 1280 and info["alto"] == 720
    finally:
        captura.detener()
