"""Partida física: el ciclo humano → Turing → brazo → verificación, con cámara, estrategia y brazo de mentira."""
from types import SimpleNamespace

import chess
import pytest

from backend.servicios.brazo import servicio_brazo
from backend.servicios.partida import partida_fisica as pf
from backend.servicios.partida import servicio_partida
from backend.servicios.vision import seguimiento_vivo, tablero
from backend.servicios.vision.test_seguimiento_jugadas import ESQ, _frame


@pytest.fixture
def mundo(monkeypatch):
    """El tablero físico (`fisico`, un FEN que ve la cámara), lo que responde Turing y lo que hace el brazo."""
    mundo = SimpleNamespace(fisico=chess.STARTING_FEN, mano=False, cuadros=0, respuestas=["e5", "Nc6", "Nf6"], brazo=[], falla_brazo=None)

    def capturar(camara="principal"):
        mundo.cuadros += 1
        return _frame(mundo.fisico, mano=mundo.mano, semilla=mundo.cuadros)

    def esperar_nuevo(_despues_de=0, _camara="principal", timeout=0.25):
        """La cámara continua del ensayo entrega enseguida el siguiente cuadro, sin abrir una webcam real."""
        del timeout
        mundo.cuadros += 1
        return mundo.cuadros, _frame(mundo.fisico, mano=mundo.mano, semilla=mundo.cuadros)

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
    monkeypatch.setattr(seguimiento_vivo, "esperar_foto_nueva", esperar_nuevo)
    monkeypatch.setattr(pf._camara, "capturar_foto_tablero", capturar)
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


def test_el_relevo_del_brazo_no_espera_estabilidad_ni_repite_la_jugada(mundo, monkeypatch) -> None:
    """El brazo confirmó descanso: se fija la base con el cuadro disponible y el turno vuelve enseguida a la persona."""
    sesion = _empezar(mundo)
    monkeypatch.setattr(seguimiento_vivo, "avanzar", lambda **_k: pytest.fail("El relevo del brazo no debe esperar otro cuadro"))

    sesion._verificar()

    assert sesion.estado == pf.ESPERANDO_HUMANO and not sesion.incidente


def test_no_se_resuelve_lo_que_no_es_un_incidente(mundo) -> None:
    sesion = _empezar(mundo)
    with pytest.raises(ValueError, match="incidente"):
        sesion.resolver_incidente("continuar")
    with pytest.raises(ValueError, match="acción"):
        sesion.resolver_incidente("otra")


def test_la_partida_fisica_no_expone_ni_consulta_ocupacion_tras_la_jugada_del_brazo(mundo) -> None:
    """Sombras y reflejos no entran en el flujo físico: sólo decide el seguimiento entre fotos."""
    sesion = _empezar(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.ESPERANDO_HUMANO and not sesion.incidente
    assert "referencia_vacia" not in sesion.estado_actual()


def test_si_la_partida_desaparece_el_estado_responde_que_se_descarto_y_no_falla(mundo) -> None:
    sesion = _empezar(mundo)
    servicio_partida._repositorio.eliminar(sesion.partida_id)  # por ejemplo, otra pantalla la borró

    primero = pf.estado()
    segundo = pf.estado()  # y las consultas siguientes tampoco fallan

    assert primero["activa"] is False and primero["descartada"] is True and "desapareció" in primero["mensaje"]
    assert segundo["activa"] is False and segundo["descartada"] is True


def test_abrir_otra_pantalla_que_crea_una_partida_no_borra_la_de_la_partida_fisica(mundo) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo", usuario_id=7)
    mundo.fisico = partida.fen
    pf.iniciar(partida.id, intervalo=0, con_hilo=False)

    servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo", usuario_id=7)  # la Sala de Control crea una al abrirse y cierra las pendientes del usuario

    assert servicio_partida.obtener_partida(partida.id).id == partida.id
    assert pf.estado()["activa"] is True


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
    seguimiento_vivo.fijar_posicion(sesion._partida().fen, rapido=True)
    assert seguimiento_vivo.vista() == datos["vista"]  # mientras toma la base nueva, conserva las tres imágenes anteriores
    assert sesion.estado == pf.ESPERANDO_HUMANO


def test_tras_la_jugada_del_brazo_la_foto_base_se_toma_enseguida_y_no_hace_esperar_a_la_persona(mundo, monkeypatch) -> None:
    """Cuando el brazo termina y vuelve a su descanso no hay ninguna mano en el tablero: la verificación toma la foto base en unos pocos cuadros (no un segundo
    de quietud) para que la jugada siguiente de la persona se reconozca sin demora."""
    bases_inmediatas, usados = [], []
    fijar = seguimiento_vivo.fijar_base_inmediata
    verificar = pf.PartidaFisica._verificar

    def fijar_registrando(fen):
        bases_inmediatas.append(fen)
        return fijar(fen)

    def verificar_midiendo(self):
        antes = mundo.cuadros
        verificar(self)
        usados.append(mundo.cuadros - antes)

    monkeypatch.setattr(seguimiento_vivo, "fijar_base_inmediata", fijar_registrando)
    monkeypatch.setattr(pf.PartidaFisica, "_verificar", verificar_midiendo)
    sesion = _empezar(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert len(bases_inmediatas) == 1 and sesion.estado == pf.ESPERANDO_HUMANO
    assert len(usados) == 1 and usados[0] == 2  # se descarta el disponible y el siguiente cuadro pasa a ser la base
    _mover_a_mano(mundo, sesion, "g1f3")  # y la jugada siguiente se reconoce con esa base
    assert sesion.estado_actual()["jugadas"] == ["e4", "e5", "Nf3", "Nc6"]


def test_el_estado_de_la_partida_fisica_informa_el_rendimiento_sin_consultar_tablero_vacio(mundo) -> None:
    """La partida física informa el ritmo de cámara, pero ya no depende de una foto vacía vulnerable a sombras."""
    sesion = _empezar(mundo)
    datos = sesion.estado_actual()
    assert "referencia_vacia" not in datos and set(datos["rendimiento"]) == {"fps_camara", "formato", "ms_cuadro"}


def _con_base_sucia(mundo, sesion: pf.PartidaFisica) -> str:
    """La foto base posterior al brazo se toma con dos peones de menos (algo los tapaba) y después el tablero se ve completo: dos casillas «cambiadas» que ninguna jugada explica."""
    fen = sesion._partida().fen
    sucio = chess.Board(fen)
    sucio.remove_piece_at(chess.E2)
    sucio.remove_piece_at(chess.D2)
    mundo.fisico = sucio.fen()
    sesion._verificar()  # toma la base con el tablero sucio (como al terminar el brazo)
    mundo.fisico = fen
    return fen


def test_una_base_sucia_tras_el_brazo_se_rehace_sola_una_vez(mundo) -> None:
    sesion = _empezar(mundo)
    _con_base_sucia(mundo, sesion)
    for _ in range(14):
        sesion.paso()
    assert any("se vuelve a tomar" in e["texto"] for e in sesion.eventos)
    assert sesion.ultimo_seguimiento["estado"] == "esperando" and sesion.estado == pf.ESPERANDO_HUMANO and not sesion.incidente
    assert sum("se vuelve a tomar" in e["texto"] for e in sesion.eventos) == 1  # una sola vez


def test_pasado_el_plazo_una_alerta_de_posicion_invalida_no_se_rehace_sola(mundo) -> None:
    """Después del plazo la alerta es de la persona (una pieza movida de más): rehacer la base la daría por buena."""
    sesion = _empezar(mundo)
    _con_base_sucia(mundo, sesion)
    sesion._cura_base_hasta = 0.0
    for _ in range(14):
        sesion.paso()
    assert sesion.ultimo_seguimiento["estado"] == "invalida"
    assert not any("se vuelve a tomar" in e["texto"] for e in sesion.eventos)


def test_la_persona_puede_volver_a_tomar_la_foto_base_con_una_alerta_que_no_se_va(mundo) -> None:
    sesion = _empezar(mundo)
    _con_base_sucia(mundo, sesion)
    sesion._cura_base_hasta = 0.0
    for _ in range(14):
        sesion.paso()
    assert sesion.ultimo_seguimiento["estado"] == "invalida"
    sesion.retomar_foto_base()
    assert any("a pedido" in e["texto"] for e in sesion.eventos)
    for _ in range(14):
        sesion.paso()
    assert sesion.ultimo_seguimiento["estado"] == "esperando" and sesion.estado == pf.ESPERANDO_HUMANO


def test_la_foto_base_solo_se_vuelve_a_tomar_mientras_se_espera_una_jugada(mundo) -> None:
    sesion = _empezar(mundo)
    sesion._fijar(pf.INCIDENTE, "Incidente: prueba")
    with pytest.raises(pf.Ocupado, match="se espera una jugada"):
        sesion.retomar_foto_base()


# --- lectura provisional de la jugada (ventana para cambiar de idea) --------------------------------------------------------------------------
def _con_ventana(mundo, ventana: float = 5.0) -> pf.PartidaFisica:
    sesion = _empezar(mundo)
    sesion.ventana_s = ventana
    return sesion


def _pasar_la_ventana(sesion: pf.PartidaFisica) -> None:
    sesion._lectura["inicio"] -= 100.0
    sesion.paso()


def test_la_jugada_leida_espera_la_ventana_antes_de_moverse_el_brazo(mundo) -> None:
    sesion = _con_ventana(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.LEYENDO and mundo.brazo == [] and sesion.estado_actual()["lectura"]["jugada"] == "e4"
    assert sesion.estado_actual()["jugadas"] == []  # todavía no se aplicó
    _pasar_la_ventana(sesion)
    assert mundo.brazo == ["e5"] and sesion.estado == pf.ESPERANDO_HUMANO and sesion.estado_actual()["jugadas"] == ["e4", "e5"]


def test_si_la_persona_cambia_la_pieza_durante_la_ventana_se_lee_de_nuevo(mundo) -> None:
    sesion = _con_ventana(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    assert sesion.estado == pf.LEYENDO
    # cambia de idea: devuelve el peón y mueve el de d
    tablero_fisico = chess.Board(chess.STARTING_FEN)
    tablero_fisico.push_uci("d2d4")
    mundo.fisico, mundo.mano = tablero_fisico.fen(), True
    sesion.paso()
    assert sesion.estado == pf.ESPERANDO_HUMANO and mundo.brazo == []
    mundo.mano = False
    for _ in range(10):
        sesion.paso()
    assert sesion.estado == pf.LEYENDO and sesion.estado_actual()["lectura"]["jugada"] == "d4"
    _pasar_la_ventana(sesion)
    assert sesion.estado_actual()["jugadas"] == ["d4", "e5"] and mundo.brazo == ["e5"]


def test_el_boton_no_era_otra_anula_la_lectura_y_despues_ya_no_se_puede(mundo) -> None:
    sesion = _con_ventana(mundo)
    _mover_a_mano(mundo, sesion, "e2e4")
    sesion.cancelar_lectura()
    assert sesion.estado == pf.ESPERANDO_HUMANO and mundo.brazo == [] and sesion.estado_actual()["jugadas"] == []
    with pytest.raises(pf.Ocupado, match="Ya no se puede anular"):
        sesion.cancelar_lectura()
    mundo.fisico = chess.STARTING_FEN  # la persona devuelve la pieza y juega otra cosa
    mundo.mano = True
    sesion.paso()
    _mover_a_mano(mundo, sesion, "g1f3")
    assert sesion.estado == pf.LEYENDO and sesion.estado_actual()["lectura"]["jugada"] == "Nf3"


# --- corregir la posición sin reiniciar la partida --------------------------------------------------------------------------------------------
def test_corregir_la_posicion_sigue_la_partida_sin_reiniciar(mundo) -> None:
    sesion = _empezar(mundo)
    mundo.respuestas = ["Nc6"]  # tras la posición corregida, Turing responde a Nf3
    fisico = chess.Board(chess.STARTING_FEN)
    fisico.push_uci("e2e4")
    fisico.push_uci("e7e5")  # lo que hay de verdad sobre el tablero (por ejemplo, tras una lectura equivocada)
    mundo.fisico = fisico.fen()
    sesion.corregir_posicion(fisico.fen())
    assert sesion.estado == pf.ESPERANDO_HUMANO and sesion.estado_actual()["fen"] == fisico.fen() and mundo.brazo == []
    for _ in range(8):  # la foto base nueva, con el tablero quieto
        sesion.paso()
    _mover_a_mano(mundo, sesion, "g1f3")
    assert mundo.brazo == ["Nc6"] and sesion.estado_actual()["jugadas"] == ["Nf3", "Nc6"]  # la partida siguió; las jugadas arrancan en la posición corregida


def test_corregir_la_posicion_con_turno_de_turing_hace_que_responda(mundo) -> None:
    sesion = _empezar(mundo)
    fisico = chess.Board(chess.STARTING_FEN)
    fisico.push_uci("e2e4")
    mundo.fisico = fisico.fen()
    sesion.corregir_posicion(fisico.fen())
    assert mundo.brazo == ["e5"] and sesion.estado == pf.ESPERANDO_HUMANO and sesion.estado_actual()["jugadas"] == ["e5"]
    assert len(sesion.ejecutadas) == 1  # el brazo hizo su jugada una sola vez, con el historial nuevo


def test_corregir_la_posicion_rechaza_lo_imposible_y_no_interrumpe_al_brazo(mundo) -> None:
    sesion = _empezar(mundo)
    with pytest.raises(ValueError, match="rey"):
        sesion.corregir_posicion("8/8/8/8/8/8/8/8 w - - 0 1")
    sesion.estado = pf.BRAZO_EJECUTA
    with pytest.raises(pf.Ocupado, match="terminen"):
        sesion.corregir_posicion(chess.STARTING_FEN)


# --- reloj ------------------------------------------------------------------------------------------------------------------------------------------
class _Tiempo:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_el_reloj_descuenta_solo_al_que_tiene_el_turno() -> None:
    tiempo = _Tiempo()
    reloj = pf.Reloj(600, 180, ahora=tiempo)
    reloj.correr("persona")
    tiempo.t += 30
    assert reloj.como_dict()["persona_ms"] == 570_000 and reloj.como_dict()["turing_ms"] == 180_000
    reloj.correr("turing")
    tiempo.t += 10
    reloj.correr(None)
    tiempo.t += 500  # parado: no descuenta
    datos = reloj.como_dict()
    assert datos["persona_ms"] == 570_000 and datos["turing_ms"] == 170_000 and datos["corre"] is None and reloj.agotado() is None
    reloj.correr("persona")
    tiempo.t += 600
    assert reloj.agotado() == "persona"


def test_un_lado_sin_limite_nunca_se_agota() -> None:
    tiempo = _Tiempo()
    reloj = pf.Reloj(None, 60, ahora=tiempo)
    reloj.correr("persona")
    tiempo.t += 99999
    assert reloj.agotado() is None and reloj.como_dict()["persona_ms"] is None


def test_el_reloj_de_la_partida_corre_segun_el_turno_y_el_brazo_no_cuenta_por_defecto(mundo, monkeypatch) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    partida.usa_brazo = True
    mundo.fisico = partida.fen
    pf.iniciar(partida.id, intervalo=0, con_hilo=False, tiempo_persona_s=600, tiempo_turing_s=180)
    sesion = pf.sesion()
    for _ in range(4):
        sesion.paso()
    assert sesion.reloj.corre == "persona"
    viendo = []
    original = servicio_brazo.ejecutar_jugada_fisica
    monkeypatch.setattr(servicio_brazo, "ejecutar_jugada_fisica", lambda antes, jugada: (viendo.append(sesion.reloj.corre), original(antes, jugada)))
    _mover_a_mano(mundo, sesion, "e2e4")
    assert viendo == [None]  # mientras el brazo mueve no corre el reloj de nadie
    assert sesion.reloj.corre == "persona"
    sesion.pausar()
    assert sesion.reloj.corre is None
    sesion.reanudar()
    assert sesion.reloj.corre == "persona"


def test_al_agotarse_el_tiempo_la_partida_termina(mundo) -> None:
    partida = servicio_partida.crear_partida(nivel=5, tipo_oponente="modelo")
    mundo.fisico = partida.fen
    pf.iniciar(partida.id, intervalo=0, con_hilo=False, tiempo_persona_s=600, tiempo_turing_s=180)
    sesion = pf.sesion()
    sesion.reloj.restante["persona"] = 0.0
    sesion.paso()
    datos = sesion.estado_actual()
    assert sesion.estado == pf.FIN and datos["fin_tiempo"] == "persona" and datos["terminada"] and datos["resultado"] == "0-1"
    assert "Turing" in sesion.mensaje
