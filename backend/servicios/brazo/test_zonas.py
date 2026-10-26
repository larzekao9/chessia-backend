"""Límites de la mesa y zonas prohibidas: qué puntos, tramos y poses del brazo se rechazan."""
import pytest

from backend.servicios.brazo import control_brazo, zonas
from backend.servicios.brazo.calibracion_brazo import ParametrosAgarre

TRIPODE = {"nombre": "Trípode de la cámara", "forma": "circulo", "x": -300.0, "y": -500.0, "radio": 100.0}
MESA = [-400.0, -800.0, 300.0, -100.0]


def _parametros(**campos) -> ParametrosAgarre:
    return ParametrosAgarre(z_tope=100.0, **campos)


def test_una_zona_circular_rechaza_lo_que_cae_dentro_y_su_holgura() -> None:
    p = _parametros(zonas_prohibidas=[TRIPODE])
    assert zonas.problemas_de_punto(-300.0, -500.0, 150.0, p)
    assert zonas.problemas_de_punto(-300.0 + 100.0 + zonas.HOLGURA_MM - 1, -500.0, 150.0, p)  # en el borde, dentro de la holgura
    assert not zonas.problemas_de_punto(-300.0 + 100.0 + zonas.HOLGURA_MM + 5, -500.0, 150.0, p)
    assert "Trípode de la cámara" in zonas.problemas_de_punto(-300.0, -500.0, 150.0, p)[0]


def test_una_zona_rectangular_con_altura_solo_vale_en_su_rango_de_alturas() -> None:
    camara = {"nombre": "Cámara", "forma": "rectangulo", "x1": 0.0, "y1": -600.0, "x2": 200.0, "y2": -400.0, "z_min": 400.0}  # el brazo de la cámara, a 400 mm de altura
    p = _parametros(zonas_prohibidas=zonas.validar_zonas([camara]))
    assert not zonas.problemas_de_punto(100.0, -500.0, 150.0, p)  # por debajo pasa
    assert zonas.problemas_de_punto(100.0, -500.0, 450.0, p)  # a esa altura no


def test_los_limites_de_la_mesa_rechazan_la_punta_que_se_sale() -> None:
    p = _parametros(limites_mesa=MESA)
    assert not zonas.problemas_de_punto(0.0, -400.0, 150.0, p)
    assert "límites de la mesa" in zonas.problemas_de_punto(400.0, -400.0, 150.0, p)[0]


def test_un_tramo_recto_que_atraviesa_una_zona_se_rechaza_aunque_los_extremos_esten_libres() -> None:
    p = _parametros(zonas_prohibidas=[TRIPODE])
    assert zonas.problemas_de_tramo((-500.0, -500.0, 150.0), (-100.0, -500.0, 150.0), p)  # pasa justo por encima del trípode
    assert not zonas.problemas_de_tramo((-500.0, -200.0, 150.0), (-100.0, -200.0, 150.0), p)


def test_un_brazo_con_el_codo_sobre_una_zona_se_rechaza() -> None:
    angulos = [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]
    brazo = zonas.punta_en_mm(angulos)
    grande = {"nombre": "Todo", "forma": "circulo", "x": brazo[0], "y": brazo[1], "radio": 2000.0}  # una zona enorme alrededor de la punta
    assert zonas.problemas_de_brazo(angulos, _parametros(zonas_prohibidas=[grande]))
    assert not zonas.problemas_de_brazo(angulos, _parametros())  # sin zonas ni límites no hay nada que comprobar


def test_se_validan_las_zonas_y_los_limites_que_llegan_de_la_pantalla() -> None:
    assert zonas.validar_zonas([TRIPODE])[0]["radio"] == 100.0
    assert zonas.validar_limites([300, -100, -400, -800]) == [-400.0, -800.0, 300.0, -100.0]  # los ordena
    assert zonas.validar_limites(None) is None
    for malo in ([{"forma": "triangulo"}], [{"forma": "circulo", "x": 1, "y": 2}], [{"forma": "circulo", "x": 1, "y": 2, "radio": 0}], "no es una lista"):
        with pytest.raises(ValueError):
            zonas.validar_zonas(malo)
    for malo in ([1, 2, 3], [0, 0, 10, 10], "x"):
        with pytest.raises(ValueError):
            zonas.validar_limites(malo)


def test_la_sugerencia_de_cementerio_evita_el_lado_con_el_tripode_y_los_bordes_de_la_mesa() -> None:
    paso = 40.0
    posiciones = {"a1": (0.0, 0.0, 234.0), "h1": (7 * paso, 0.0, 234.0), "a8": (0.0, 7 * paso, 234.0)}
    libre = control_brazo._sugerencia_de_cementerio(posiciones, _parametros())
    assert libre["lado"] == "derecha"  # sin restricciones, el de siempre
    con_tripode = {"nombre": "Trípode", "forma": "circulo", "x": 7 * paso + 2 * paso + 100.0, "y": 100.0, "radio": 200.0}
    # a la derecha está el trípode (y con el cementerio a la izquierda, las reservas irían a la derecha, también sobre el trípode): se elige otro lado
    assert control_brazo._sugerencia_de_cementerio(posiciones, _parametros(zonas_prohibidas=[con_tripode]))["lado"] in ("atras", "delante")
    mesa_justa = _parametros(limites_mesa=[-250.0, -100.0, 450.0, 7 * paso + 100.0])  # a la derecha las filas del cementerio se saldrían de la mesa (x hasta 480)
    assert control_brazo._sugerencia_de_cementerio(posiciones, mesa_justa)["lado"] == "izquierda"
    sin_lugar = control_brazo._sugerencia_de_cementerio(posiciones, _parametros(limites_mesa=[-10.0, -10.0, 300.0, 300.0]))
    assert "aviso" in sin_lugar
