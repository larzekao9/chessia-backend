"""Partida física: el ciclo humano → Turing → brazo → verificación, con cámara, estrategia y brazo de mentira."""
from types import SimpleNamespace

import chess
import numpy as np
import pytest

from backend.servicios.brazo import servicio_brazo
from backend.servicios.partida import partida_fisica as pf
from backend.servicios.partida import servicio_partida
from backend.servicios.vision import referencia_vacia as rv
from backend.servicios.vision import seguimiento_vivo, tablero
from backend.servicios.vision.test_seguimiento_jugadas import ESQ, _frame


@pytest.fixture
def mundo(monkeypatch):
    """El tablero físico (`fisico`, un FEN que ve la cámara), lo que responde Turing y lo que hace el brazo."""
    mundo = SimpleNamespace(fisico=chess.STARTING_FEN, mano=False, cuadros=0, respuestas=["e5", "Nc6", "Nf6"], brazo=[], falla_brazo=None, ocupacion_torcida=None)

    def capturar(camara="principal"):
        mundo.cuadros += 1
        return _frame(mundo.fisico, mano=mundo.mano, semilla=mundo.cuadros)

    def ocupadas(fotos):
        presentes = chess.Board(mundo.fisico).piece_map()
        vistas = np.array([chess.parse_square(n) in presentes for n in rv.NOMBRES])
        if mundo.ocupacion_torcida:
            for nombre in mundo.ocupacion_torcida:
                vistas[rv.NOMBRES.index(nombre)] = not vistas[rv.NOMBRES.index(nombre)]
        return vistas, "principal"

    class Turing:
        def decidir_jugada(self, fen):
            return mundo.respuestas.pop(0)

    def brazo(antes, jugada):
        if mundo.falla_brazo:
            raise RuntimeError(mundo.falla_brazo)
        mundo.brazo.append(antes.san(jugada))
        despues = antes.copy()
        despues.push(jugada)
        mundo.fisico = despues.fen()

    monkeypatch.setattr(tablero, "cargar_esquinas_calibradas", lambda *_a, **_k: ESQ)
    monkeypatch.setattr(seguimiento_vivo, "capturar_foto_tablero", capturar)
    monkeypatch.setattr(pf._camara, "capturar_foto_tablero", capturar)
    monkeypatch.setattr(pf.referencia_vacia, "ocupadas", ocupadas)
    monkeypatch.setattr(servicio_partida, "crear_estrategia_jugada", lambda *_a, **_k: Turing())
    monkeypatch.setattr(servicio_partida, "analizar_posicion", lambda *_a, **_k: {})
    monkeypatch.setattr(servicio_partida, "_calidad_de_la_jugada_humana", lambda *_a, **_k: None)
    monkeypatch.setattr(servicio_brazo, "ejecutar_jugada_fisica", brazo)
    yield mundo
    pf.detener()


def _empezar(mundo, usa_brazo: bool = True, fen: str | None = None) -> pf.PartidaFisica:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo", fen_inicial=fen)
    partida.usa_brazo = usa_brazo
    mundo.fisico = partida.fen
    pf.iniciar(partida.id, intervalo=0, con_hilo=False)
    sesion = pf.sesion()
    for _ in range(4):  # foto base con el tablero quieto
        sesion.paso()
    return sesion


def _mover_a_mano(mundo, sesion: pf.PartidaFisica, uci: str, cuadros: int = 8) -> None:
    """El humano mueve una pieza: la mano tapa un cuadro y el tablero queda quieto."""
    tablero_fisico = chess.Board(mundo.fisico)
    tablero_fisico.push_uci(uci)
    mundo.fisico, mundo.mano = tablero_fisico.fen(), True
    sesion.paso()
    mundo.mano = False
    for _ in range(cuadros):
        sesion.paso()


def test_ciclo_completo_humano_turing_brazo_y_verificacion(mundo) -> None:
    sesion = _empezar(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert mundo.brazo == ["e5"]  # el brazo hizo la respuesta de Turing, una sola vez
    assert sesion.estado == pf.ESPERANDO_HUMANO
    assert sesion.estado_actual()["jugadas"] == ["e4", "e5"]
    _mover_a_mano(mundo, sesion, "g1f3")
    assert mundo.brazo == ["e5", "Nc6"] and sesion.estado == pf.ESPERANDO_HUMANO


def test_una_jugada_invalida_del_humano_no_mueve_el_brazo(mundo) -> None:
    sesion = _empezar(mundo)
    ilegal = chess.Board(mundo.fisico)
    ilegal.set_piece_at(chess.E2, None)
    ilegal.set_piece_at(chess.E5, chess.Piece(chess.PAWN, chess.WHITE))
    mundo.fisico = ilegal.fen()
    for _ in range(8):
        sesion.paso()
    assert mundo.brazo == [] and sesion.estado == pf.ESPERANDO_HUMANO and sesion.ultimo_seguimiento["estado"] == "invalida"
    assert sesion.estado_actual()["jugadas"] == []


def test_si_el_brazo_falla_hay_incidente_y_se_puede_reintentar(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.falla_brazo = "La pinza cerró en e7 y no sintió ninguna pieza"
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.INCIDENTE and "pinza" in sesion.incidente
    assert sesion.estado_actual()["puede_reintentar"] and sesion.estado_actual()["jugadas"] == ["e4", "e5"]  # la partida digital sí avanzó
    mundo.falla_brazo = None
    mundo.fisico = chess.Board(sesion.pendiente[0].fen()).fen()  # la persona dejó el tablero como antes de la jugada del brazo
    sesion.resolver_incidente("reintentar")
    assert mundo.brazo == ["e5"] and sesion.estado == pf.ESPERANDO_HUMANO and sesion.incidente == ""


def test_un_incidente_se_resuelve_confirmando_que_el_tablero_ya_coincide(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.falla_brazo = "sin conexión"
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.INCIDENTE
    despues = chess.Board(sesion.pendiente[0].fen())
    despues.push(sesion.pendiente[1])
    mundo.fisico = despues.fen()  # la persona movió la pieza del robot a mano
    mundo.falla_brazo = None
    sesion.resolver_incidente("continuar")
    assert sesion.estado == pf.ESPERANDO_HUMANO
    for _ in range(4):
        sesion.paso()
    _mover_a_mano(mundo, sesion, "g1f3")
    assert sesion.estado_actual()["jugadas"] == ["e4", "e5", "Nf3", "Nc6"] and sesion.estado == pf.ESPERANDO_HUMANO


def test_no_se_resuelve_lo_que_no_es_un_incidente(mundo) -> None:
    sesion = _empezar(mundo)
    with pytest.raises(ValueError, match="incidente"):
        sesion.resolver_incidente("continuar")
    with pytest.raises(ValueError, match="acción"):
        sesion.resolver_incidente("otra")


def test_si_el_tablero_no_coincide_tras_la_jugada_del_brazo_hay_incidente(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.ocupacion_torcida = ["a3", "h3"]  # la cámara ve dos casillas distintas a la posición
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.INCIDENTE and "a3" in sesion.incidente


def test_una_casilla_dudosa_se_avisa_pero_no_frena(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.ocupacion_torcida = ["a3"]
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.ESPERANDO_HUMANO and any("a3" in e["texto"] for e in sesion.eventos)


def test_sin_brazo_espera_a_que_la_jugada_del_robot_se_haga_a_mano(mundo) -> None:
    sesion = _empezar(mundo, usa_brazo=False)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.ESPERANDO_ROBOT_MANUAL and mundo.brazo == [] and "e5" in sesion.mensaje
    _mover_a_mano(mundo, sesion, "d7d6")  # otra cosa distinta de la que tocaba
    assert sesion.estado == pf.ESPERANDO_ROBOT_MANUAL and "e5" in sesion.mensaje
    mundo.fisico = chess.Board(sesion.pendiente[0].fen()).fen()  # se deja como estaba
    for _ in range(6):
        sesion.paso()
    _mover_a_mano(mundo, sesion, "e7e5")
    assert sesion.estado == pf.ESPERANDO_HUMANO


def test_la_jugada_manual_respalda_a_la_camara(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.fisico = _despues(mundo.fisico, "e2e4")
    sesion.jugada_manual("e4")
    assert mundo.brazo == ["e5"] and sesion.estado == pf.ESPERANDO_HUMANO
    with pytest.raises(ValueError):
        sesion.jugada_manual("Qh8")  # ilegal


def _despues(fen: str, uci: str) -> str:
    tablero_fisico = chess.Board(fen)
    tablero_fisico.push_uci(uci)
    return tablero_fisico.fen()


def test_si_el_humano_da_mate_la_partida_termina_sin_mover_el_brazo(mundo) -> None:
    sesion = _empezar(mundo, fen="7k/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    _mover_a_mano(mundo, sesion, "a1a8")
    assert sesion.estado == pf.FIN and mundo.brazo == [] and sesion.estado_actual()["terminada"]


def test_si_turing_da_mate_el_brazo_igual_lo_ejecuta_y_termina(mundo) -> None:
    mundo.respuestas = ["Qh4#"]
    sesion = _empezar(mundo, fen="rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2")
    _mover_a_mano(mundo, sesion, "g2g4")
    assert mundo.brazo == ["Qh4#"] and sesion.estado == pf.FIN and sesion.estado_actual()["terminada"]


def test_no_se_repite_una_jugada_ya_ejecutada(mundo) -> None:
    sesion = _empezar(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    antes = chess.Board()
    antes.push_uci("e2e4")
    sesion.pendiente = (antes, chess.Move.from_uci("e7e5"))  # la jugada número 2, que el brazo ya hizo
    sesion._ejecutar_y_verificar("e5")
    assert sesion.estado == pf.INCIDENTE and "ya se había ejecutado" in sesion.incidente and mundo.brazo == ["e5"]


def test_pausar_congela_el_seguimiento(mundo) -> None:
    sesion = _empezar(mundo)
    sesion.pausar()
    antes = mundo.cuadros
    sesion.paso()
    assert mundo.cuadros == antes
    sesion.reanudar()
    sesion.paso()
    assert mundo.cuadros > antes


def test_la_partida_fisica_pide_cinco_fallos_de_camara_seguidos_para_dar_incidente(mundo, monkeypatch) -> None:
    sesion = _empezar(mundo)

    def sin_camara(camara="principal"):
        raise RuntimeError("cámara desconectada")

    monkeypatch.setattr(seguimiento_vivo, "capturar_foto_tablero", sin_camara)
    for _ in range(sesion.fallos_maximos - 1):
        sesion.paso()
    assert sesion.estado == pf.ESPERANDO_HUMANO
    sesion.paso()
    assert sesion.estado == pf.INCIDENTE and "cámara" in sesion.incidente


def test_iniciar_valida_la_partida(mundo) -> None:
    humano = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    with pytest.raises(KeyError):
        pf.iniciar("no-existe", con_hilo=False)
    assert pf.iniciar(humano.id, intervalo=0, con_hilo=False)["estado"] == pf.ESPERANDO_HUMANO
    assert pf.estado()["activa"] and pf.detener() == {"activa": False} and pf.estado() == {"activa": False}
    with pytest.raises(pf.SinPartidaFisica):
        pf.sesion()


def test_mover_corona_solo_a_dama_si_se_pide(mundo) -> None:
    mundo.respuestas = ["a1=N"]
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo", fen_inicial="8/8/8/8/8/k7/p7/2K5 w - - 0 1")
    resultado = servicio_partida.mover(partida.id, "c1d1", ejecutar_brazo=False, coronar_solo_a_dama=True)
    assert resultado["jugada_motor"].startswith("a1=Q")


def test_sin_brazo_conectado_la_jugada_del_brazo_es_un_incidente_y_no_se_finge(mundo, monkeypatch) -> None:
    """Con el ejecutor real de verdad (no el de mentira) y sin brazo conectado en el panel ni AJEDREZ_MODO_BRAZO=real, avisa en vez de «simular» que movió."""
    monkeypatch.undo()  # quita los parches de la cámara y del brazo: se prueba `servicio_brazo` tal cual
    monkeypatch.delenv("AJEDREZ_MODO_BRAZO", raising=False)
    with pytest.raises(RuntimeError, match="ningún brazo conectado"):
        servicio_brazo.ejecutar_jugada_fisica(chess.Board(), chess.Move.from_uci("e2e4"))


def test_el_estado_puede_traer_la_vista_de_la_comparacion(mundo) -> None:
    sesion = _empezar(mundo)
    datos = pf.estado(con_vista=True)
    assert datos["vista"]["disponible"] and datos["vista"]["base"].startswith("data:image/jpeg")
    assert "vista" not in pf.estado()
    assert sesion.estado == pf.ESPERANDO_HUMANO
