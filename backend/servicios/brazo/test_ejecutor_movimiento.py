import chess
import pytest

from backend.servicios.brazo.calibracion_brazo import ParametrosAgarre
from backend.servicios.brazo.calibracion_tablero import (
    PuntoCalibracion,
    calcular_posiciones_casillas,
)
from backend.servicios.brazo.ejecutor_movimiento import EjecutorReal, PiezaNoAgarrada
from backend.servicios.brazo.pinza_dh import AGARRE_LLEGO, AGARRE_SOSTIENE, EstadoPinza

HOST_DOCUMENTACION = "192.0.2.1"

PUNTOS_CALIBRACION_SINTETICOS = [
    PuntoCalibracion(casilla="a1", x=0.0, y=0.0, z=10.0),
    PuntoCalibracion(casilla="h1", x=700.0, y=0.0, z=10.0),
    PuntoCalibracion(casilla="a8", x=0.0, y=700.0, z=10.0),
]
APERTURA = 560
PARAMETROS = ParametrosAgarre(z_tope=100.0, margen_agarre_mm=3.0, giro_diagonal_grados=45.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0)


class ClienteFalso:
    """Reemplaza a `ClienteDobot`: anota lo que se le pide en orden, sin red ni brazo."""

    def __init__(self, historial: list) -> None:
        self.llamadas = historial

    def mover_a_pose(self, pose, lineal=False, **_):
        self.llamadas.append(("MovL" if lineal else "MovJ", tuple(pose)))

    def mover_a_angulos(self, angulos, **_):
        self.llamadas.append(("joint", tuple(angulos)))

    def conectar(self):
        self.llamadas.append(("conectar",))

    def cerrar(self):
        self.llamadas.append(("cerrar",))


class PinzaFalsa:
    """Reemplaza a `PinzaDH`: cerrar sobre una pieza deja `agarre = 2`; sin pieza, `agarre = 1` (cerró en el aire)."""

    def __init__(self, historial: list, inicializada: bool = True, hay_pieza: bool = True, se_cae_al_subir: bool = False) -> None:
        self.llamadas = historial
        self.inicializada = inicializada
        self.hay_pieza = hay_pieza
        self.se_cae_al_subir = se_cae_al_subir
        self._agarre = AGARRE_LLEGO

    def conectar(self):
        self.llamadas.append(("pinza_conectar",))

    def desconectar(self):
        self.llamadas.append(("pinza_desconectar",))

    def inicializar(self, **_):
        self.llamadas.append(("pinza_inicializar",))
        self.inicializada = True

    def estado(self) -> EstadoPinza:
        if self.se_cae_al_subir and any(l[0] == "MovL" for l in self.llamadas[self._marca_cierre():]):
            self._agarre = AGARRE_LLEGO
        return EstadoPinza(self.inicializada, self._agarre, 0)

    def _marca_cierre(self) -> int:
        cierres = [i for i, l in enumerate(self.llamadas) if l == ("pinza_cerrar",)]
        return cierres[-1] if cierres else len(self.llamadas)

    def mover_a(self, posicion):
        self.llamadas.append(("pinza_abrir_a", posicion))
        self._agarre = AGARRE_LLEGO

    def cerrar(self) -> EstadoPinza:
        self.llamadas.append(("pinza_cerrar",))
        self._agarre = AGARRE_SOSTIENE if self.hay_pieza else AGARRE_LLEGO
        return EstadoPinza(self.inicializada, self._agarre, 0)


def _ejecutor(parametros=PARAMETROS, **kwargs_pinza) -> tuple[EjecutorReal, list]:
    historial: list = []
    posiciones = calcular_posiciones_casillas(PUNTOS_CALIBRACION_SINTETICOS)
    ejecutor = EjecutorReal(
        host=HOST_DOCUMENTACION,
        posiciones_casillas=posiciones,
        orientacion=(-180.0, 0.0, -120.0),
        parametros=parametros,
        cliente=ClienteFalso(historial),
        pinza=PinzaFalsa(historial, **kwargs_pinza),
    )
    return ejecutor, historial


def _jugar(ejecutor: EjecutorReal, uci: str = "e2e4") -> None:
    tablero = chess.Board()
    ejecutor.ejecutar_movimiento(tablero, tablero.parse_uci(uci))


def test_parametros_de_agarre_z_agarre_es_tope_mas_margen():
    assert PARAMETROS.z_agarre == pytest.approx(103.0)


def test_apertura_por_pieza_usa_la_medida_de_cada_una_y_nunca_la_del_peon_por_defecto():
    parametros = ParametrosAgarre(apertura_aproximacion=560, aperturas_por_pieza={"n": 800})

    assert parametros.apertura_para("p") == 560 and parametros.apertura_para("N") == 800
    with pytest.raises(RuntimeError, match="'q'"):
        parametros.apertura_para("q")


def test_ejecutor_real_baja_con_la_apertura_de_la_pieza_y_suelta_mas_abierta():
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=560, apertura_extra_soltar=150, aperturas_por_pieza={"n": 800})
    ejecutor, historial = _ejecutor(parametros=parametros)

    tablero = chess.Board()
    ejecutor.ejecutar_movimiento(tablero, tablero.parse_uci("b1c3"))

    assert [l[1] for l in historial if l[0] == "pinza_abrir_a"] == [800, 950]  # al soltar se abre 150 más, hasta 1000 como mucho


def test_ejecutor_real_sin_apertura_medida_para_la_pieza_no_mueve_nada():
    ejecutor, historial = _ejecutor()

    tablero = chess.Board()
    with pytest.raises(RuntimeError, match="'n'"):
        ejecutor.ejecutar_movimiento(tablero, tablero.parse_uci("b1c3"))
    assert historial == []


def test_parametros_de_agarre_sin_altura_del_tablero_falla_con_mensaje_claro():
    with pytest.raises(RuntimeError, match="z_tope"):
        ParametrosAgarre().z_agarre


def test_ejecutor_real_sin_calibracion_lanza_runtime_error():
    ejecutor, historial = _ejecutor()
    ejecutor.posiciones_casillas = None

    with pytest.raises(RuntimeError, match="calibración"):
        _jugar(ejecutor)
    assert historial == []


def test_ejecutor_real_sin_orientacion_lanza_runtime_error():
    ejecutor, historial = _ejecutor()
    ejecutor.orientacion = None

    with pytest.raises(RuntimeError, match="orientación"):
        _jugar(ejecutor)
    assert historial == []  # no movió nada


def test_ejecutor_real_sin_altura_del_tablero_no_mueve_nada():
    ejecutor, historial = _ejecutor(parametros=ParametrosAgarre())

    with pytest.raises(RuntimeError, match="z_tope"):
        _jugar(ejecutor)
    assert historial == []


def test_ejecutor_real_con_pinza_sin_inicializar_no_mueve_nada():
    ejecutor, historial = _ejecutor(inicializada=False)

    with pytest.raises(RuntimeError, match="inicializada"):
        _jugar(ejecutor)
    assert historial == []


PARAMETROS_COMPLETOS = ParametrosAgarre(
    z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, aperturas_por_pieza={"n": 600, "b": 600, "r": 620, "q": 640, "k": 660},
    cementerio_origen=[900.0, 0.0], reservas={"dama_blanca": [900.0, 200.0], "dama_negra": [900.0, 300.0]},
)


def _destinos_en_xy(historial: list) -> list[tuple[float, float]]:
    """(x, y) de cada vez que el brazo baja a agarrar o soltar (MovL a la altura de trabajo)."""
    return [(l[1][0], l[1][1]) for l in historial if l[0] == "MovL" and l[1][2] == 103.0]


def test_ejecutor_real_con_captura_retira_primero_la_pieza_capturada_al_cementerio():
    ejecutor, historial = _ejecutor(PARAMETROS_COMPLETOS)
    tablero_antes = chess.Board()
    tablero_antes.push_san("e4")
    tablero_antes.push_san("d5")

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("e4d5"))

    destinos = _destinos_en_xy(historial)
    posiciones = ejecutor.posiciones_casillas
    assert len(destinos) == 4  # (d5 → cementerio) y (e4 → d5): bajar a agarrar y a soltar en cada uno
    assert destinos[0] == posiciones["d5"][:2] and destinos[1] == (900.0, 0.0)  # la capturada va al primer lugar del cementerio
    assert destinos[2] == posiciones["e4"][:2] and destinos[3] == posiciones["d5"][:2]
    assert [l for l in historial if l[0] == "pinza_cerrar"] and len([l for l in historial if l[0] == "pinza_cerrar"]) == 2


def test_ejecutor_real_en_partida_deja_la_pieza_en_el_lugar_ensenado_del_cementerio_ordenado():
    import dataclasses

    parametros = dataclasses.replace(
        PARAMETROS_COMPLETOS, cementerio_por_piezas=True, cementerio_lugares={"cementerio:9": [950.0, 75.0]}
    )  # el caballo 2 del cementerio del brazo se enseñó ahí, fuera de la cuadrícula
    ejecutor, historial = _ejecutor(parametros)
    tablero_antes = chess.Board("4k3/8/8/3n4/5N2/8/8/4K3 b - - 0 1")  # a las blancas ya les falta un caballo: el capturado es el segundo

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("d5f4"))

    assert _destinos_en_xy(historial)[1] == (950.0, 75.0)  # el caballo blanco capturado va al lugar enseñado, no al de la cuadrícula
    assert ejecutor._xy("cementerio:8") != (950.0, 75.0)  # los no enseñados siguen saliendo de la cuadrícula


def test_ejecutor_real_enroque_mueve_el_rey_y_despues_la_torre():
    ejecutor, historial = _ejecutor(PARAMETROS_COMPLETOS)
    tablero_antes = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("e1g1"))

    p = ejecutor.posiciones_casillas
    assert _destinos_en_xy(historial) == [p["e1"][:2], p["g1"][:2], p["h1"][:2], p["f1"][:2]]


def test_ejecutor_real_corona_con_la_dama_de_reserva():
    ejecutor, historial = _ejecutor(PARAMETROS_COMPLETOS)
    tablero_antes = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("a7a8q"))

    p = ejecutor.posiciones_casillas
    assert _destinos_en_xy(historial) == [p["a7"][:2], ejecutor._xy("cementerio:29"), (900.0, 200.0), p["a8"][:2]]  # el peón al cementerio y la dama de reserva a a8


def test_ejecutor_real_sin_dama_de_reserva_trae_de_vuelta_el_peon_que_dejo_en_el_cementerio():
    ejecutor, historial = _ejecutor(PARAMETROS_COMPLETOS)
    cierres = iter([True, False, False, True])  # agarra el peón; la dama no está (y el reintento tampoco); vuelve a agarrar el peón
    cerrar_original = ejecutor.pinza.cerrar

    def cerrar():
        ejecutor.pinza.hay_pieza = next(cierres)
        return cerrar_original()

    ejecutor.pinza.cerrar = cerrar
    ejecutor.reintentos_agarre = 1
    tablero_antes = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("a7a8q"))

    p = ejecutor.posiciones_casillas
    lugar_del_peon = ejecutor._xy("cementerio:29")
    destinos = _destinos_en_xy(historial)
    assert destinos[:2] == [p["a7"][:2], lugar_del_peon]
    assert destinos[-2:] == [lugar_del_peon, p["a8"][:2]]  # el peón vuelve del cementerio a a8 y queda como dama


def test_ejecutor_real_sin_cementerio_calibrado_no_mueve_nada_en_una_captura():
    ejecutor, historial = _ejecutor()
    tablero_antes = chess.Board()
    tablero_antes.push_san("e4")
    tablero_antes.push_san("d5")

    with pytest.raises(RuntimeError, match="cementerio"):
        ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("e4d5"))
    assert historial == []


def test_ejecutor_real_reintenta_el_agarre_una_vez_si_cerro_en_el_aire():
    ejecutor, historial = _ejecutor(hay_pieza=False)
    ejecutor.reintentos_agarre = 1

    with pytest.raises(PiezaNoAgarrada):
        _jugar(ejecutor)

    assert len([l for l in historial if l == ("pinza_cerrar",)]) == 2  # lo intentó dos veces y recién después avisó


def test_ejecutor_real_no_reintenta_si_se_le_cayo_la_pieza():
    ejecutor, historial = _ejecutor(se_cae_al_subir=True)
    ejecutor.reintentos_agarre = 3

    with pytest.raises(PiezaNoAgarrada) as error:
        _jugar(ejecutor)

    assert not error.value.reintentable and len([l for l in historial if l == ("pinza_cerrar",)]) == 1


def test_ejecutor_real_secuencia_de_una_jugada_sin_captura():
    ejecutor, historial = _ejecutor()

    _jugar(ejecutor)

    pasos = [(l[0], l[1][2] if l[0] in ("MovJ", "MovL") else None) for l in historial]
    assert pasos == [
        ("pinza_abrir_a", None),
        ("MovJ", 183.0),  # sobre e2, a altura segura
        ("MovL", 103.0),  # baja hasta 3 mm del tablero
        ("pinza_cerrar", None),
        ("MovL", 183.0),  # sube con la pieza
        ("MovL", 183.0),  # se traslada a e4
        ("MovL", 103.0),  # baja
        ("pinza_abrir_a", None),  # suelta
        ("MovL", 183.0),
    ]
    assert [l[1] for l in historial if l[0] == "pinza_abrir_a"] == [APERTURA, min(1000, APERTURA + ParametrosAgarre().apertura_extra_soltar)]


def test_ejecutor_real_bajar_y_subir_no_cierran_la_pinza():
    ejecutor, historial = _ejecutor()

    ejecutor.bajar_a("e2")
    ejecutor.subir_de("e2")

    assert ("pinza_cerrar",) not in historial
    alturas = [l[1][2] for l in historial if l[0] in ("MovJ", "MovL")]
    assert alturas == [183.0, 103.0, 183.0]


def test_ejecutor_real_gira_la_herramienta_en_diagonal_y_respeta_la_orientacion_medida():
    ejecutor, historial = _ejecutor()

    _jugar(ejecutor)

    poses = [l[1] for l in historial if l[0] in ("MovJ", "MovL")]
    assert all(p[3:] == (-180.0, 0.0, -75.0) for p in poses)  # rz = -120 + 45


def test_ejecutor_real_vuelve_a_la_posicion_cero_si_hay_angulos():
    cero = (303.8, -1.1, -83.3, 6.1, 16.0, 227.0)
    ejecutor, historial = _ejecutor()
    ejecutor.angulos_cero = cero

    _jugar(ejecutor)

    assert historial[-1] == ("joint", cero)


def test_ejecutor_real_si_no_agarra_nada_abre_sube_y_avisa_sin_trasladarse():
    ejecutor, historial = _ejecutor(hay_pieza=False)

    with pytest.raises(PiezaNoAgarrada, match="e2"):
        _jugar(ejecutor)

    movimientos = [l for l in historial if l[0] in ("MovJ", "MovL")]
    assert len(movimientos) == 3  # sobre e2, baja, sube: nunca fue a e4
    assert historial[-2] == ("pinza_abrir_a", APERTURA)
    assert historial[-1][0] == "MovL" and historial[-1][1][2] == pytest.approx(183.0)


def test_ejecutor_real_si_se_le_cae_la_pieza_al_subir_no_sigue():
    ejecutor, historial = _ejecutor(se_cae_al_subir=True)

    with pytest.raises(PiezaNoAgarrada, match="cayó"):
        _jugar(ejecutor)

    destino_y = ejecutor.posiciones_casillas["e4"][1]  # e2 y e4 comparten columna: se distinguen por la Y
    assert not any(l[0] == "MovL" and l[1][1] == pytest.approx(destino_y) for l in historial)


def test_ejecutor_real_conectar_inicializa_la_pinza_solo_si_hace_falta():
    ejecutor, historial = _ejecutor(inicializada=False)
    ejecutor.conectar()
    assert historial == [("conectar",), ("pinza_conectar",), ("pinza_inicializar",)]

    ejecutor, historial = _ejecutor(inicializada=True)
    ejecutor.conectar()
    assert historial == [("conectar",), ("pinza_conectar",)]


def test_ejecutor_real_desconectar_cierra_pinza_y_cliente():
    ejecutor, historial = _ejecutor()

    ejecutor.desconectar()

    assert historial == [("pinza_desconectar",), ("cerrar",)]


def test_ejecutor_real_al_soltar_no_baja_del_todo_si_hay_elevacion_al_soltar():
    """Al agarrar baja hasta la altura de agarre; al soltar se queda `elevacion_al_soltar_mm` más arriba (y abre más la pinza), para no chocar con el tablero."""
    parametros = ParametrosAgarre(z_tope=100.0, margen_agarre_mm=3.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, apertura_extra_soltar=400, elevacion_al_soltar_mm=4.0)
    ejecutor, historial = _ejecutor(parametros)

    _jugar(ejecutor)

    bajadas = [l[1][2] for l in historial if l[0] == "MovL" and l[1][2] < 150]
    assert bajadas == [103.0, 107.0]  # agarra a 3 mm del tablero y suelta 4 mm más arriba
    assert [l[1] for l in historial if l[0] == "pinza_abrir_a"] == [APERTURA, min(1000, APERTURA + 400)]  # y suelta con la pinza más abierta


def test_ejecutor_real_no_mueve_nada_si_el_recorrido_pasa_por_una_zona_prohibida():
    """El trípode de la cámara (una zona prohibida) sobre el destino: se rechaza ANTES de mover la primera pieza."""
    tripode = {"nombre": "Trípode", "forma": "circulo", "x": 400.0, "y": 300.0, "radio": 20.0}  # sobre e4
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, zonas_prohibidas=[tripode])
    ejecutor, historial = _ejecutor(parametros)

    with pytest.raises(RuntimeError, match="lugar prohibido.*Trípode"):
        _jugar(ejecutor, "e2e4")

    assert historial == []


def test_ejecutor_real_no_mueve_nada_si_el_destino_sale_de_los_limites_de_la_mesa():
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, limites_mesa=[-50.0, -50.0, 750.0, 250.0])  # la fila 4 (y=300) queda fuera
    ejecutor, historial = _ejecutor(parametros)

    with pytest.raises(RuntimeError, match="límites de la mesa"):
        _jugar(ejecutor, "e2e4")

    assert historial == []


def test_ejecutor_real_con_zonas_lejanas_se_mueve_normal():
    lejos = {"nombre": "Lejos", "forma": "circulo", "x": 5000.0, "y": 5000.0, "radio": 100.0}
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, zonas_prohibidas=[lejos], limites_mesa=[-1000.0, -1000.0, 2000.0, 2000.0])
    ejecutor, historial = _ejecutor(parametros)

    _jugar(ejecutor, "e2e4")

    assert any(l[0] == "pinza_cerrar" for l in historial)


def test_ejecutor_real_baja_y_sube_despacio_cerca_del_tablero_y_rapido_arriba():
    """Los tramos verticales cerca del tablero (bajar a agarrar o soltar, y subir de ahí) van a la velocidad lenta; los traslados por arriba, a la general."""
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, velocidad_bajada=8, aceleracion_bajada=4)
    ejecutor, historial = _ejecutor(parametros)
    registro = []
    original = ejecutor.cliente.mover_a_pose

    def mover_a_pose(pose, lineal=False, aceleracion=None, velocidad=None, **_):
        registro.append((round(pose[2]), velocidad, aceleracion))
        return original(pose, lineal=lineal)

    ejecutor.cliente.mover_a_pose = mover_a_pose
    ejecutor.cliente.velocidad, ejecutor.cliente.aceleracion = 40, 15  # las generales del brazo, más altas que las de bajada

    _jugar(ejecutor)

    por_altura = {z: (v, a) for z, v, a in registro}
    assert por_altura[103] == (8, 4)  # abajo (agarre y soltado): lento
    assert [v for z, v, a in registro if z == 183][0] is None  # el traslado por arriba, a la velocidad general del brazo
    lentas = [z for z, v, a in registro if v == 8]
    assert lentas.count(103) == 2 and lentas.count(183) >= 2  # baja a agarrar y a soltar, y las dos primeras subidas son lentas


def test_ejecutor_real_traslada_por_encima_de_la_pieza_mas_alta_con_holgura():
    """Con las alturas de las piezas medidas (el rey, 74 mm) el traslado sube lo necesario para pasar con 15 mm de aire aunque `altura_segura_mm` sea menor."""
    alturas = {"p": 42.0, "n": 55.0, "r": 44.0, "b": 58.0, "q": 63.0, "k": 74.0}
    parametros = ParametrosAgarre(z_tope=100.0, margen_agarre_mm=3.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, alturas_piezas_mm=alturas, holgura_traslado_mm=15.0)
    assert parametros.altura_de_traslado_para("p") == 86.0  # 74 + 15 − 3 (el margen de agarre)
    ejecutor, historial = _ejecutor(parametros)

    _jugar(ejecutor)

    altos = {l[1][2] for l in historial if l[0] in ("MovJ", "MovL")}
    assert 189.0 in altos and 183.0 not in altos  # sube a 103 + 86, no a 103 + 80


def test_ejecutor_real_en_las_casillas_del_borde_no_baja_mas_que_el_marco_del_tablero():
    """El marco sobresale 7 mm de la superficie: en a1 los dedos no bajan por debajo del marco (más 2 mm); en e4, que está lejos, bajan a su altura de agarre normal."""
    parametros = ParametrosAgarre(z_tope=100.0, margen_agarre_mm=3.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, altura_borde_mm=7.0, cementerio_origen=[900.0, 0.0])
    ejecutor, _ = _ejecutor(parametros)

    assert ejecutor._pose("a1")[2] == 109.0  # 100 + 7 + 2, no 103
    assert ejecutor._pose("h8")[2] == 109.0 and ejecutor._pose("a5")[2] == 109.0
    assert ejecutor._pose("e4")[2] == 103.0
    assert ejecutor._pose("cementerio:0")[2] == 103.0  # el cementerio no es del tablero


def test_ejecutor_real_aplica_el_corrimiento_medido_a_las_casillas_del_tablero_y_no_al_cementerio():
    """El corrimiento (lo que hay que correr la pinza para que baje centrada) se suma a todas las casillas del tablero; el cementerio y las reservas ya se calibran donde se quieren."""
    parametros = ParametrosAgarre(z_tope=100.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, corrimiento_x_mm=3.0, corrimiento_y_mm=-2.0, cementerio_origen=[900.0, 0.0])
    ejecutor, historial = _ejecutor(parametros)
    nominal = ejecutor.posiciones_casillas["e2"]

    assert ejecutor._xy("e2") == (nominal[0] + 3.0, nominal[1] - 2.0)
    assert ejecutor._xy("cementerio:0") == (900.0, 0.0)

    _jugar(ejecutor)
    destinos = [(l[1][0], l[1][1]) for l in historial if l[0] in ("MovJ", "MovL")]
    assert destinos[0] == pytest.approx((nominal[0] + 3.0, nominal[1] - 2.0))  # va sobre e2 con el corrimiento


def test_ejecutor_real_en_el_cementerio_con_desnivel_baja_hasta_la_mesa_segun_la_altura_de_cada_pieza():
    """La mesa del cementerio queda más baja que el tablero: al soltar, cada pieza baja ese desnivel (menos la holgura) desde SU altura de agarre, así su base queda a pocos mm de la mesa."""
    parametros = ParametrosAgarre(
        z_tope=100.0, margen_agarre_mm=20.0, margenes_por_pieza={"n": 27.5}, apertura_aproximacion=APERTURA, aperturas_por_pieza={"n": 840},
        altura_segura_mm=80.0, desnivel_cementerio_mm=20.0, holgura_cementerio_mm=2.0, cementerio_origen=[900.0, 0.0],
    )
    ejecutor, _ = _ejecutor(parametros)

    assert ejecutor._pose_de_soltar("cementerio:0", "p")[2] == pytest.approx(102.0)  # 100 + 20 − 20 + 2: la base del peón queda a 2 mm de la mesa
    assert ejecutor._pose_de_soltar("cementerio:0", "n")[2] == pytest.approx(109.5)  # 100 + 27.5 − 20 + 2: el caballo se sostiene más arriba, así que la punta queda más alta
    assert ejecutor._pose_de_soltar("e4", "p")[2] == pytest.approx(120.0)  # en el tablero no cambia: la altura de agarre más la elevación al soltar (0)


def test_ejecutor_real_sin_desnivel_suelta_en_el_cementerio_como_siempre():
    parametros = ParametrosAgarre(z_tope=100.0, margen_agarre_mm=3.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, elevacion_al_soltar_mm=4.0, cementerio_origen=[900.0, 0.0])
    ejecutor, _ = _ejecutor(parametros)

    assert ejecutor._pose_de_soltar("cementerio:0", "p")[2] == pytest.approx(107.0)  # 103 + 4: sin `desnivel_cementerio_mm` el cementerio se trata como parte del tablero


def test_ejecutor_real_lleva_la_pieza_capturada_hasta_la_mesa_del_cementerio_y_en_el_tablero_la_suelta_a_su_altura_de_siempre():
    parametros = ParametrosAgarre(
        z_tope=100.0, margen_agarre_mm=20.0, apertura_aproximacion=APERTURA, altura_segura_mm=80.0, elevacion_al_soltar_mm=4.0,
        desnivel_cementerio_mm=20.0, holgura_cementerio_mm=2.0, cementerio_origen=[900.0, 0.0],
    )
    ejecutor, historial = _ejecutor(parametros)
    tablero_antes = chess.Board()
    tablero_antes.push_san("e4")
    tablero_antes.push_san("d5")

    ejecutor.ejecutar_movimiento(tablero_antes, tablero_antes.parse_uci("e4d5"))

    en_cementerio = [l[1][2] for l in historial if l[0] == "MovL" and (l[1][0], l[1][1]) == (900.0, 0.0)]
    assert min(en_cementerio) == pytest.approx(102.0)  # el peón capturado baja hasta 2 mm de la mesa
    assert 124.0 in [l[1][2] for l in historial if l[0] == "MovL"]  # y en d5 la suelta 4 mm sobre la altura de agarre (100 + 20 + 4)


def test_ejecutor_real_aplica_giro_especifico_por_pieza():
    parametros = ParametrosAgarre(
        z_tope=100.0,
        giro_diagonal_grados=45.0,
        giros_por_pieza={"r": 35.0, "n": 20.0},
    )
    ejecutor, _ = _ejecutor(parametros)
    rz_base = ejecutor.orientacion[2]

    assert ejecutor._pose("e4", tipo_pieza="p")[5] == pytest.approx(rz_base + 45.0)
    assert ejecutor._pose("a1", tipo_pieza="r")[5] == pytest.approx(rz_base + 35.0)
    assert ejecutor._pose("b1", tipo_pieza="n")[5] == pytest.approx(rz_base + 20.0)
    assert ejecutor._pose("c1", tipo_pieza="b")[5] == pytest.approx(rz_base + 45.0)
