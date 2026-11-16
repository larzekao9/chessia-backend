"""Pruebas del generador de piezas con cabeza delgada, con el modelo real de la torre del simulador."""
import math
import struct

import pytest

from scripts.generar_pieza_cabeza_delgada import (
    OBJ_TORRE,
    adelgazar_cabeza,
    aristas_abiertas,
    escalar_a_mm,
    generar,
    leer_obj,
    perfil_radial,
    radio_maximo_desde,
    volumen_mm3,
)

ALTURA_MM = 44.0
DESDE_MM, HASTA_MM = 30.0, 36.0


@pytest.fixture(scope="module")
def torre():
    vertices, triangulos = leer_obj(OBJ_TORRE)
    return escalar_a_mm(vertices, ALTURA_MM), triangulos


def test_el_modelo_escalado_mide_lo_pedido_y_apoya_en_cero(torre):
    vertices, _ = torre
    alturas = [v[1] for v in vertices]
    assert min(alturas) == pytest.approx(0, abs=1e-9)
    assert max(alturas) == pytest.approx(ALTURA_MM)


def test_la_cabeza_queda_con_el_diametro_pedido_y_el_alto_no_cambia(torre):
    vertices, _ = torre
    nuevo = adelgazar_cabeza(vertices, DESDE_MM, HASTA_MM, 16.0)
    assert 2 * radio_maximo_desde(nuevo, HASTA_MM) == pytest.approx(16.0)
    assert max(v[1] for v in nuevo) == pytest.approx(ALTURA_MM)


def test_por_debajo_de_desde_no_se_mueve_ningun_punto(torre):
    vertices, _ = torre
    nuevo = adelgazar_cabeza(vertices, DESDE_MM, HASTA_MM, 16.0)
    for antes, despues in zip(vertices, nuevo):
        if antes[1] <= DESDE_MM:
            assert antes == despues


def test_la_silueta_no_se_angosta_por_encima_del_cuello(torre):
    """La transición del cuello a la cabeza no deja una cintura: el radio solo crece desde el punto más angosto."""
    vertices, _ = torre
    perfil = perfil_radial(adelgazar_cabeza(vertices, DESDE_MM, HASTA_MM, 16.0))
    arriba_del_cuello = [r for d, h, r in perfil if d >= 28.0]
    assert arriba_del_cuello == sorted(arriba_del_cuello)


def test_la_malla_sigue_cerrada_y_con_volumen_positivo_menor_que_el_original(torre):
    vertices, triangulos = torre
    nuevo = adelgazar_cabeza(vertices, DESDE_MM, HASTA_MM, 16.0)
    assert aristas_abiertas(triangulos) == 0
    assert 0 < volumen_mm3(nuevo, triangulos) < volumen_mm3(vertices, triangulos)


def test_pedir_una_cabeza_mas_ancha_que_la_del_modelo_da_error(torre):
    vertices, _ = torre
    with pytest.raises(ValueError, match="ya mide"):
        adelgazar_cabeza(vertices, DESDE_MM, HASTA_MM, 30.0)


def test_desde_tiene_que_ser_menor_que_hasta(torre):
    vertices, _ = torre
    with pytest.raises(ValueError, match="menor"):
        adelgazar_cabeza(vertices, 36.0, 30.0, 16.0)


def test_el_stl_es_binario_valido_con_z_arriba_y_la_base_en_cero(tmp_path):
    salida = tmp_path / "torre.stl"
    medidas = generar(OBJ_TORRE, salida, ALTURA_MM, DESDE_MM, HASTA_MM, 16.0)
    datos = salida.read_bytes()
    n = struct.unpack("<I", datos[80:84])[0]
    assert len(datos) == 84 + 50 * n
    puntos = [struct.unpack("<3f", datos[84 + 50 * i + 12 + 12 * j:84 + 50 * i + 24 + 12 * j]) for i in range(n) for j in range(3)]
    zs = [p[2] for p in puntos]
    assert min(zs) == pytest.approx(0, abs=1e-4)
    assert max(zs) == pytest.approx(ALTURA_MM, abs=1e-3)
    assert max(math.hypot(p[0], p[1]) for p in puntos if p[2] > HASTA_MM) == pytest.approx(8.0, abs=1e-3)
    assert medidas["aristas_abiertas"] == 0
