"""Pruebas de los cálculos de enseñar el cementerio llevando una pieza (`ajuste_cementerio.py`)."""
import pytest

from backend.servicios.brazo.ajuste_cementerio import cambios_para, desnivel_para


def test_la_primera_pieza_define_el_origen_de_cada_cementerio() -> None:
    assert cambios_para("brazo", 0, 0, None, None, 257.14, -683.06) == {"cementerio_origen": [257.1, -683.1]}
    assert cambios_para("jugador", 0, 0, None, None, -205.0, -620.0) == {"cementerio_jugador_origen": [-205.0, -620.0]}


def test_otra_pieza_de_la_primera_fila_define_el_paso_dividiendo_por_las_columnas() -> None:
    origen = [100.0, 0.0]
    assert cambios_para("brazo", 0, 1, origen, None, 100.0, 42.0) == {"cementerio_paso": [0.0, 42.0]}
    assert cambios_para("brazo", 0, 7, origen, None, 100.0, 294.0) == {"cementerio_paso": [0.0, 42.0]}  # la más lejana es más precisa: 294 / 7


def test_la_primera_pieza_de_otra_fila_define_el_paso_de_fila() -> None:
    assert cambios_para("jugador", 1, 0, [100.0, 0.0], [0.0, 42.0], 142.0, -1.0) == {"cementerio_jugador_paso_fila": [42.0, -1.0]}
    assert cambios_para("jugador", 2, 0, [100.0, 0.0], [0.0, 42.0], 184.0, 0.0) == {"cementerio_jugador_paso_fila": [42.0, 0.0]}  # dos filas más abajo: 84 / 2


def test_una_pieza_en_el_medio_ajusta_el_paso_de_fila_descontando_el_paso() -> None:
    # fila 1, columna 3: el origen más 3 pasos de [0, 42] más un paso de fila de [42, 0]
    assert cambios_para("brazo", 1, 3, [100.0, 0.0], [0.0, 42.0], 142.0, 126.0) == {"cementerio_paso_fila": [42.0, 0.0]}


def test_sin_origen_no_se_puede_ensenar_otra_pieza() -> None:
    with pytest.raises(ValueError, match="Primero enseñá la primera pieza"):
        cambios_para("brazo", 0, 1, None, None, 100.0, 42.0)


def test_una_separacion_absurda_se_rechaza() -> None:
    with pytest.raises(ValueError, match="separación"):
        cambios_para("brazo", 0, 1, [100.0, 0.0], None, 100.0, 2.0)  # dos milímetros: se soltó en otro lugar
    with pytest.raises(ValueError, match="separación"):
        cambios_para("brazo", 0, 1, [100.0, 0.0], None, 100.0, 1500.0)


def test_el_desnivel_sale_de_la_altura_de_la_pinza() -> None:
    assert desnivel_para(z_agarre=245.6, holgura=2.0, z_actual=227.6) == 20.0  # el peón con su margen de 20 mm sobre z_tope 225,6
    assert desnivel_para(z_agarre=245.6, holgura=2.0, z_actual=230.6) == 17.0
    with pytest.raises(ValueError, match="desnivel"):
        desnivel_para(z_agarre=245.6, holgura=2.0, z_actual=300.0)
