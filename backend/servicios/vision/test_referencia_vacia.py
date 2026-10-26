"""Casilla de una pieza sola por diferencia con el tablero vacío (`referencia_vacia`), con un tablero sintético."""
import cv2
import numpy as np
import pytest

from backend.servicios.vision import referencia_vacia as rv
from backend.servicios.vision import tablero
from backend.servicios.vision.test_validacion_orientacion import ESQUINAS

PUNTOS = {nombre: tuple(float(v) for v in p) for nombre, p in zip(tablero.ORDEN_DE_ENDEREZADO, ESQUINAS)}


@pytest.fixture(autouse=True)
def archivos_temporales(tmp_path, monkeypatch):
    monkeypatch.setattr(tablero, "ARCHIVO_ESQUINAS", tmp_path / "esquinas.json")


def _vacio() -> np.ndarray:
    """Damero sintético (a1 oscura abajo a la izquierda) dentro de las esquinas `ESQUINAS`, con algo de ruido como una cámara."""
    imagen = np.full((750, 900, 3), 120, np.uint8)
    (x0, y0), (x1, _), (_, y1) = ESQUINAS[0], ESQUINAS[1], ESQUINAS[2]
    paso_x, paso_y = (x1 - x0) / 8, (y1 - y0) / 8
    for fila in range(8):
        for columna in range(8):
            color = (60, 90, 120) if (fila + columna) % 2 else (150, 190, 220)
            cv2.rectangle(imagen, (int(x0 + columna * paso_x), int(y0 + fila * paso_y)), (int(x0 + (columna + 1) * paso_x), int(y0 + (fila + 1) * paso_y)), color, -1)
    return imagen


def _con_piezas(*casillas: str, ruido: int = 0) -> np.ndarray:
    imagen = _vacio()
    (x0, y0), (x1, _), (_, y1) = ESQUINAS[0], ESQUINAS[1], ESQUINAS[2]
    for casilla in casillas:
        columna, fila = "abcdefgh".index(casilla[0]), 8 - int(casilla[1])
        centro = (int(x0 + (columna + 0.5) * (x1 - x0) / 8), int(y0 + (fila + 0.5) * (y1 - y0) / 8))
        cv2.circle(imagen, centro, 22, (235, 235, 235), -1)
    if ruido:
        imagen = np.clip(imagen.astype(np.int16) + np.random.default_rng(1).integers(-ruido, ruido, imagen.shape), 0, 255).astype(np.uint8)
    return imagen


def _guardar_calibracion_y_referencia(camara: str = "principal") -> None:
    tablero.guardar_esquinas_calibradas({n: PUNTOS[n] for n in tablero.ORDEN_DE_CLICS}, tablero.archivo_esquinas(camara))
    rv.guardar_referencia(camara, [(_vacio(), ESQUINAS)])


def test_una_pieza_se_ubica_en_su_casilla() -> None:
    _guardar_calibracion_y_referencia()
    for casilla in ("e4", "a1", "h8", "b7"):
        assert rv.decidir(rv.puntajes(_con_piezas(casilla, ruido=6), ESQUINAS, rv.cargar_referencia())) [:2] == (casilla, "ok")


def test_sin_piezas_es_ninguna_y_dos_piezas_es_ambigua() -> None:
    _guardar_calibracion_y_referencia()
    referencia = rv.cargar_referencia()
    assert rv.decidir(rv.puntajes(_con_piezas(ruido=6), ESQUINAS, referencia))[1] == "ninguna"
    assert rv.decidir(rv.puntajes(_con_piezas("c3", "f6"), ESQUINAS, referencia))[1] == "ambigua"


def test_un_cambio_de_luz_parejo_no_parece_una_pieza() -> None:
    _guardar_calibracion_y_referencia()
    mas_luz = cv2.convertScaleAbs(_vacio(), alpha=1.0, beta=25)
    assert rv.decidir(rv.puntajes(mas_luz, ESQUINAS, rv.cargar_referencia()))[1] == "ninguna"


def test_decide_la_camara_menos_inclinada(monkeypatch) -> None:
    for camara in ("principal", "secundaria"):
        _guardar_calibracion_y_referencia(camara)
    inclinaciones = {"principal": 30.0, "secundaria": 4.0}
    monkeypatch.setattr(rv, "_inclinacion", lambda camara, imagen, esquinas: inclinaciones[camara])
    # La "diagonal" (principal) ve la pieza en d5 y además su cabeza en e5; la de arriba la ve solo en d5.
    casilla, estado, _, por_camara = rv.detectar_casilla({"principal": _con_piezas("d5", "e5"), "secundaria": _con_piezas("d5")})
    assert (casilla, estado) == ("d5", "ok") and por_camara["secundaria"] == "d5" and por_camara["principal"] != "d5"


def test_sin_referencia_no_decide() -> None:
    tablero.guardar_esquinas_calibradas({n: PUNTOS[n] for n in tablero.ORDEN_DE_CLICS})
    assert rv.detectar_casilla({"principal": _con_piezas("e4")}) is None


def test_capturar_casilla_vacia_renueva_la_referencia(tmp_path, monkeypatch) -> None:
    import training.capturar_dataset_propio as capturar
    from backend.servicios.vision import camara, dataset_propio

    tablero.guardar_esquinas_calibradas({n: PUNTOS[n] for n in tablero.ORDEN_DE_CLICS})
    monkeypatch.setattr(capturar, "CARPETA_DATASET", tmp_path / "dataset_propio")
    monkeypatch.setattr(capturar, "esquinas_y_margen", lambda imagen, *a, **k: (ESQUINAS, 0.0))
    monkeypatch.setattr(camara, "capturar_foto_tablero", lambda camara="principal": _vacio())
    monkeypatch.setattr(dataset_propio, "_carpeta", lambda: tmp_path / "dataset_propio")
    assert rv.cargar_referencia("principal") is None

    dataset_propio.capturar_foto_etiquetada("8/8/8/8/8/8/8/8", camaras=["principal"])
    assert rv.cargar_referencia("principal") is not None

    rv.ruta_referencia("principal").unlink()
    dataset_propio.capturar_foto_etiquetada("8/8/8/8/4P3/8/8/8", solo_piezas=True, camaras=["principal"])
    assert rv.cargar_referencia("principal") is None  # con una pieza no se toca la referencia


def test_ocupadas_con_muchas_piezas_como_en_la_posicion_inicial() -> None:
    _guardar_calibracion_y_referencia()
    llenas = [f"{c}{f}" for f in "1278" for c in "abcdefgh"]
    mascara, camara = rv.ocupadas({"principal": _con_piezas(*llenas, ruido=6)})
    vistas = {nombre for nombre, ocupada in zip(rv.NOMBRES, mascara) if ocupada}
    assert camara == "principal" and vistas == set(llenas)


def test_el_probador_toma_la_ocupacion_de_la_camara_de_arriba(monkeypatch) -> None:
    from backend.servicios.vision import verificacion
    from backend.servicios.vision.modelo_piezas import CLASES

    _guardar_calibracion_y_referencia()
    probs = np.full((64, len(CLASES)), 0.01)
    probs[:, CLASES.index("vacia")] = 0.9
    probs[rv.NOMBRES.index("e5"), :] = 0.01  # el modelo ve la cabeza del peón en e5 (la vecina) …
    probs[rv.NOMBRES.index("e5"), CLASES.index("white-pawn")] = 0.9
    probs[rv.NOMBRES.index("e4"), CLASES.index("white-pawn")] = 0.45  # … y en e4, donde está, duda
    probs /= probs.sum(axis=1, keepdims=True)
    casillas = verificacion._casillas_de_probabilidades(probs, rv.NOMBRES, list(CLASES), {"principal": _con_piezas("e4")})
    vistas = {c["casilla"]: c["clase"] for c in casillas if c["clase"] != "vacia"}
    assert vistas == {"e4": "white-pawn"}


def test_el_probador_solo_clasifica_las_casillas_ocupadas(monkeypatch) -> None:
    from backend.servicios.vision import piezas, verificacion
    from backend.servicios.vision.modelo_piezas import CLASES

    _guardar_calibracion_y_referencia()
    mirados = []

    def falso(recortes):
        mirados.append(len(recortes))
        salida = np.zeros((len(recortes), len(CLASES)))
        salida[:, CLASES.index("white-pawn")] = 1.0
        return salida

    monkeypatch.setattr(piezas, "probabilidades", falso)
    monkeypatch.setattr(piezas, "clases_del_modelo", lambda: list(CLASES))
    monkeypatch.setattr(piezas, "hay_dos_vistas", lambda: False)
    monkeypatch.setattr(verificacion, "recortes_de_imagen", lambda imagen, camara: ({n: imagen[:10, :10] for n in rv.NOMBRES}, ESQUINAS, 0.0))
    foto = _con_piezas("e4", "d5")
    probs, nombres, _ = verificacion.probabilidades_de_piezas({"principal": foto})
    assert mirados == [2]  # el modelo solo vio las 2 casillas con pieza
    vistas = {n for n, fila in zip(nombres, probs) if CLASES[fila.argmax()] != "vacia"}
    assert vistas == {"e4", "d5"}


def test_una_referencia_lisa_no_se_guarda_ni_se_usa(tmp_path) -> None:
    """La foto del tablero vacío sale lisa cuando las esquinas no calzan con la cámara (se recalibró después): no sirve y se descarta."""
    lisa = np.full((750, 900, 3), 60, np.uint8)
    with pytest.raises(ValueError, match="salió lisa"):
        rv.guardar_referencia("principal", [(lisa, ESQUINAS)])
    assert rv.cargar_referencia("principal") is None
    cv2.imwrite(str(rv.ruta_referencia("principal")), np.full((800, 800, 3), 60, np.uint8))  # una lisa ya guardada antes
    assert rv.cargar_referencia("principal") is None


def test_borrar_la_referencia_la_quita_del_disco_y_de_memoria() -> None:
    rv.guardar_referencia("principal", [(_vacio(), ESQUINAS)])
    assert rv.cargar_referencia("principal") is not None
    rv.borrar_referencia("principal")
    assert rv.cargar_referencia("principal") is None and not rv.ruta_referencia("principal").exists()


def test_cada_perfil_de_tablero_tiene_su_propia_referencia() -> None:
    """Dos tableros físicos calibrados para la misma cámara no se pisan ni se mezclan: activar uno usa SU referencia."""
    tablero.guardar_esquinas_calibradas({n: PUNTOS[n] for n in tablero.ORDEN_DE_CLICS}, tablero.archivo_esquinas("principal"))
    rv.guardar_referencia("principal", [(_vacio(), ESQUINAS)])  # perfil "default" (activo por ahora)
    tablero.establecer_perfil_activo("principal", "mesa_2")
    tablero.guardar_esquinas_calibradas({n: PUNTOS[n] for n in tablero.ORDEN_DE_CLICS}, tablero.archivo_esquinas("principal"))
    rv.guardar_referencia("principal", [(_con_piezas("e4", ruido=0), ESQUINAS)])  # perfil "mesa_2" (ahora activo), bien distinta

    # Con "mesa_2" activo, detectar_casilla compara contra SU referencia (la de _con_piezas, "vacía" para mesa_2) y no ve nada raro.
    assert rv.detectar_casilla({"principal": _con_piezas("e4", ruido=0)})[1] == "ninguna"
    referencia_mesa_2 = rv.ruta_referencia("principal").read_bytes()

    tablero.establecer_perfil_activo("principal", tablero.PERFIL_POR_DEFECTO)
    # Vuelta al tablero original: su referencia sigue intacta (no la pisó la de mesa_2) y una pieza en e4 ahora sí se nota.
    assert rv.ruta_referencia("principal").read_bytes() != referencia_mesa_2
    assert rv.detectar_casilla({"principal": _con_piezas("e4", ruido=6)})[:2] == ("e4", "ok")

    # Borrar el tablero "mesa_2" no toca la referencia del por defecto.
    tablero.borrar_perfil("principal", "mesa_2")
    assert rv.cargar_referencia("principal") is not None
