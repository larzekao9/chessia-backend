import chess
import pytest
from sqlalchemy import create_engine, select

from backend.database import crear_fabrica_sesiones, crear_tablas
from backend.modelos.partida import Partida
from backend.modelos.tablas_orm import JugadaORM
from backend.repositorios.repositorio_partida import RepositorioPartidasPostgres
from backend.servicios.partida import servicio_partida
from backend.servicios.partida.servicio_partida import (
    analisis_completo,
    crear_partida,
    jugadas_legales_desde,
    mover,
    mover_desde_foto,
    obtener_partida,
)


def test_crear_partida_arranca_en_posicion_inicial() -> None:
    partida = crear_partida(nivel=5)
    assert partida.fen.startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w")
    assert not partida.terminada
    assert partida.tipo_oponente == "motor"


def test_crear_partida_guarda_el_usuario_dueno() -> None:
    partida = crear_partida(nivel=5, usuario_id=7)
    assert partida.usuario_id == 7


def test_crear_partida_sin_usuario_queda_sin_dueno() -> None:
    partida = crear_partida(nivel=5)
    assert partida.usuario_id is None


def test_crear_partida_con_tipo_oponente_no_soportado_lanza_valueerror() -> None:
    # Se valida al crear, sin necesidad de Stockfish corriendo (HU10).
    with pytest.raises(ValueError):
        crear_partida(nivel=5, tipo_oponente="participante")


def test_crear_partida_con_fen_inicial_arranca_en_esa_posicion() -> None:
    # Posición tras 1. e4 e5 — simula lo que devolvería /vision/reconocer.
    fen_tablero_escaneado = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    partida = crear_partida(nivel=5, fen_inicial=fen_tablero_escaneado)
    assert partida.fen == fen_tablero_escaneado
    assert partida.jugadas_san == []  # todavía no se jugó nada *desde* que se cargó


def test_crear_partida_con_fen_inicial_invalido_lanza_valueerror() -> None:
    with pytest.raises(ValueError):
        crear_partida(nivel=5, fen_inicial="esto no es un fen")


def test_crear_partida_con_tablero_sin_piezas_lanza_valueerror_con_mensaje_propio() -> None:
    # El tablero físico puede estar vacío a propósito al probar la cámara: no es una posición imposible,
    # es que no hay nada que reconocer, y el mensaje lo dice.
    with pytest.raises(ValueError, match="vacío"):
        crear_partida(nivel=5, fen_inicial="8/8/8/8/8/8/8/8 w - - 0 1")


def test_crear_partida_con_fen_inicial_imposible_lanza_valueerror() -> None:
    # 9 damas blancas — sintácticamente válido, pero imposible en una partida
    # real (visto en vivo con un FEN mal reconocido por visión, que hacía
    # caer a Stockfish más adelante en vez de fallar acá con un error claro).
    fen_imposible = "QQQQQQQQ/QPPPPPPP/8/8/8/8/8/K6k w - - 0 1"
    with pytest.raises(ValueError):
        crear_partida(nivel=5, fen_inicial=fen_imposible)


def test_obtener_partida_inexistente_lanza_keyerror() -> None:
    with pytest.raises(KeyError):
        obtener_partida("no-existe")


def test_mover_aplica_jugada_humana_y_responde_con_stockfish() -> None:
    partida = crear_partida(nivel=5)
    resultado = mover(partida.id, "e2e4")
    assert resultado["jugada_motor"] is not None
    assert not resultado["terminada"]
    # el FEN avanzó: ya no es la posición inicial
    assert "w KQkq - 0 1" not in resultado["fen"]


def test_mover_jugada_ilegal_lanza_valueerror() -> None:
    partida = crear_partida(nivel=5)
    with pytest.raises(ValueError):
        mover(partida.id, "e2e5")


def test_mover_en_partida_inexistente_lanza_keyerror() -> None:
    with pytest.raises(KeyError):
        mover("no-existe", "e2e4")


def test_mover_si_la_estrategia_falla_no_deja_la_jugada_humana_aplicada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reproduce el bug donde, si la estrategia activa (ej. el modelo propio,
    # HU4, devolviendo una jugada SAN ilegal) explota calculando su respuesta,
    # la jugada del humano quedaba igual aplicada en `partida.tablero` — el
    # cliente ve un error y no avanza su FEN local, pero el backend ya había
    # cambiado de turno, y la partida quedaba trabada (cualquier jugada de
    # blancas después se rechazaba como ilegal, porque en el servidor ya le
    # tocaba a negras).
    partida = crear_partida(nivel=5)
    fen_antes = partida.fen

    class _EstrategiaRota:
        def decidir_jugada(self, fen: str) -> str:
            raise ValueError("jugada ilegal simulada")

    monkeypatch.setattr(servicio_partida, "crear_estrategia_jugada", lambda *a, **k: _EstrategiaRota())

    with pytest.raises(ValueError):
        mover(partida.id, "e2e4")

    partida_tras_el_error = obtener_partida(partida.id)
    assert partida_tras_el_error.fen == fen_antes
    assert partida_tras_el_error.jugadas_san == []

    # y se puede reintentar sin que la partida haya quedado corrompida
    monkeypatch.undo()
    resultado = mover(partida.id, "e2e4")
    assert resultado["jugadas"][0] == "e4"


def test_mover_guarda_la_jugada_en_el_repositorio(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reproduce el bug donde `mover()` devolvía el FEN correcto en la
    # respuesta HTTP (armado a mano desde el objeto `Partida` en memoria que
    # ya estaba mutado), pero nunca llamaba a `_repositorio.guardar(...)` —
    # con `RepositorioPartidasEnMemoria` no se notaba, porque `obtener()`
    # devuelve el mismo objeto por referencia, pero con un repositorio real
    # (Postgres/SQLite, activo en cuanto se configura `DATABASE_URL`)
    # `obtener()` reconstruye la partida desde la base en cada llamada, así
    # que la siguiente vez que alguien la pedía (recargar la pantalla, la
    # app móvil al reabrir) volvía a ver la posición de antes de la jugada.
    engine = create_engine("sqlite:///:memory:")
    crear_tablas(engine)
    repositorio_real = RepositorioPartidasPostgres(crear_fabrica_sesiones(engine))
    monkeypatch.setattr(servicio_partida, "_repositorio", repositorio_real)

    partida = crear_partida(nivel=5)
    resultado = mover(partida.id, "e2e4")

    partida_releida = obtener_partida(partida.id)
    assert partida_releida.fen == resultado["fen"]
    assert partida_releida.jugadas_san == resultado["jugadas"]


def test_repositorio_postgres_conserva_el_fen_inicial_custom() -> None:
    # Bug real: `_fila_a_partida` reconstruía el tablero siempre desde
    # `chess.Board()` (posición inicial estándar) e ignoraba por completo
    # `partida.fen_inicial` — cualquier partida armada desde un tablero
    # físico escaneado (HU1/HU9, `POST /partida` con `fen_inicial`) perdía su
    # posición de arranque real apenas se releía de un repositorio Postgres
    # real (con `RepositorioPartidasEnMemoria`, que devuelve el mismo objeto
    # por referencia, no se notaba).
    engine = create_engine("sqlite:///:memory:")
    crear_tablas(engine)
    repositorio = RepositorioPartidasPostgres(crear_fabrica_sesiones(engine))

    fen_inicial_custom = "rnb1kbnr/pppppppp/8/8/3q4/8/PPNPPPPP/RNBQKBNR w - - 0 1"
    tablero = chess.Board(fen_inicial_custom)
    tablero.push_san("Nxd4")
    partida = Partida(tablero=tablero, fen_inicial=fen_inicial_custom, nivel=5)
    repositorio.guardar(partida)

    partida_releida = repositorio.obtener(partida.id)

    assert partida_releida.fen_inicial == fen_inicial_custom
    assert partida_releida.jugadas_san == ["Nxd4"]


def test_mover_registra_una_fila_de_jugada_por_cada_movimiento_aplicado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Con `RepositorioPartidasEnMemoria` (el default sin `DATABASE_URL`) esto es
    # un no-op silencioso — acá se prueba contra un repositorio real (RF34/HU4)
    # para confirmar que `mover()` popula la tabla `jugada`: una fila para la
    # jugada del humano y otra para la respuesta de la estrategia activa.
    engine = create_engine("sqlite:///:memory:")
    crear_tablas(engine)
    fabrica_sesiones = crear_fabrica_sesiones(engine)
    repositorio_real = RepositorioPartidasPostgres(fabrica_sesiones)
    monkeypatch.setattr(servicio_partida, "_repositorio", repositorio_real)

    partida = crear_partida(nivel=5)
    mover(partida.id, "e2e4")

    with fabrica_sesiones() as sesion:
        filas = sesion.scalars(
            select(JugadaORM).where(JugadaORM.partida_id == partida.id).order_by(JugadaORM.numero)
        ).all()

    assert len(filas) == 2
    assert filas[0].numero == 1
    assert filas[0].movimiento == "e2e4"
    assert filas[0].decidido_por == "jugador"
    assert filas[1].numero == 2
    assert filas[1].decidido_por == "motor"


def test_mover_sin_usa_brazo_no_expone_error_de_brazo() -> None:
    # usa_brazo=False (default) hace que ejecutar_respuesta_en_brazo sea un
    # no-op real, sin necesidad de mockear nada ni tocar pybullet/sockets.
    partida = crear_partida(nivel=5)
    assert partida.usa_brazo is False

    resultado = mover(partida.id, "e2e4")

    assert resultado["error_brazo"] is None


def test_mover_con_usa_brazo_invoca_el_ejecutor_con_la_jugada_de_la_estrategia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partida = crear_partida(nivel=5)
    partida.usa_brazo = True
    servicio_partida._repositorio.guardar(partida)

    llamadas = []

    def _ejecutar_respuesta_en_brazo_fake(partida_arg, tablero_antes, jugada):
        llamadas.append((partida_arg.id, tablero_antes.fen(), jugada.uci()))
        return None

    monkeypatch.setattr(
        servicio_partida, "ejecutar_respuesta_en_brazo", _ejecutar_respuesta_en_brazo_fake
    )

    resultado = mover(partida.id, "e2e4")

    assert len(llamadas) == 1
    partida_id_recibido, fen_antes_recibido, jugada_uci_recibida = llamadas[0]
    assert partida_id_recibido == partida.id
    # tablero_antes es la posición justo antes de la jugada de la estrategia
    # (después de aplicar la del humano), no la posición inicial de la partida.
    assert "b KQkq" in fen_antes_recibido
    # la jugada que recibe el ejecutor es la respuesta de la estrategia, no
    # la jugada del humano ("e2e4") — nunca se le pide al brazo reproducirla.
    assert jugada_uci_recibida != "e2e4"
    assert resultado["error_brazo"] is None


def test_mover_con_usa_brazo_expone_el_error_del_brazo_sin_romper_la_jugada_digital(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partida = crear_partida(nivel=5)
    partida.usa_brazo = True
    servicio_partida._repositorio.guardar(partida)

    monkeypatch.setattr(
        servicio_partida,
        "ejecutar_respuesta_en_brazo",
        lambda *args, **kwargs: "brazo desconectado",
    )

    resultado = mover(partida.id, "e2e4")

    assert resultado["error_brazo"] == "brazo desconectado"
    assert resultado["jugada_motor"] is not None
    assert not resultado["terminada"]


def test_mover_en_partida_ya_terminada_lanza_valueerror() -> None:
    # Fool's mate armado directo en el tablero, sin pasar por Stockfish, para dejar la
    # partida en jaque mate y probar que `mover` no deja seguir jugando después.
    partida = crear_partida(nivel=1)
    for jugada_san in ["f3", "e5", "g4", "Qh4#"]:
        partida.tablero.push_san(jugada_san)
    assert partida.terminada

    with pytest.raises(ValueError):
        mover(partida.id, "a2a3")


def test_jugadas_legales_desde_devuelve_destinos_del_peon() -> None:
    partida = crear_partida(nivel=5)
    assert jugadas_legales_desde(partida.id, "e2") == ["e3", "e4"]


def test_jugadas_legales_desde_casilla_vacia_devuelve_lista_vacia() -> None:
    partida = crear_partida(nivel=5)
    assert jugadas_legales_desde(partida.id, "e4") == []


def test_jugadas_legales_desde_casilla_invalida_lanza_valueerror() -> None:
    partida = crear_partida(nivel=5)
    with pytest.raises(ValueError):
        jugadas_legales_desde(partida.id, "z9")


def test_jugadas_legales_desde_partida_inexistente_lanza_keyerror() -> None:
    with pytest.raises(KeyError):
        jugadas_legales_desde("no-existe", "e2")


def test_mover_desde_foto_detecta_y_aplica_la_jugada(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    # Simula que la cámara/reconocimiento ya vieron la posición tras 1. e4 —
    # detectar_jugada no usa el turno de fen_despues, así que el valor exacto no importa acá.
    fen_despues_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    monkeypatch.setattr(servicio_partida, "_detectar_por_ocupacion", lambda fen, camaras=None: False)  # método de respaldo
    monkeypatch.setattr(servicio_partida, "capturar_foto_tablero", lambda: None)
    monkeypatch.setattr(servicio_partida, "reconocer_tablero", lambda imagen, turno: fen_despues_e4)

    resultado = mover_desde_foto(partida.id)

    assert resultado["jugadas"][0] == "e4"
    assert resultado["jugada_motor"] is not None  # requiere Stockfish


def test_mover_desde_foto_sin_jugada_legal_que_coincida_lanza_valueerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partida = crear_partida(nivel=5)
    # Posición imposible de alcanzar con una sola jugada legal desde la inicial.
    fen_irreconciliable = "8/8/8/8/8/8/8/8 b - - 0 1"
    monkeypatch.setattr(servicio_partida, "_detectar_por_ocupacion", lambda fen, camaras=None: False)  # método de respaldo
    monkeypatch.setattr(servicio_partida, "capturar_foto_tablero", lambda: None)
    monkeypatch.setattr(servicio_partida, "reconocer_tablero", lambda imagen, turno: fen_irreconciliable)

    with pytest.raises(ValueError):
        mover_desde_foto(partida.id)


def test_mover_desde_foto_por_ocupacion_aplica_la_jugada(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    monkeypatch.setattr(servicio_partida, "_detectar_por_ocupacion", lambda fen, camaras=None: "e4")

    assert mover_desde_foto(partida.id)["jugadas"][0] == "e4"


def test_mover_desde_foto_por_ocupacion_sin_jugada_clara_pide_reintentar(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    monkeypatch.setattr(servicio_partida, "_detectar_por_ocupacion", lambda fen, camaras=None: None)

    with pytest.raises(ValueError, match="sacá la mano"):
        mover_desde_foto(partida.id)


def test_mover_desde_foto_en_partida_ya_terminada_lanza_valueerror() -> None:
    partida = crear_partida(nivel=1)
    for jugada_san in ["f3", "e5", "g4", "Qh4#"]:
        partida.tablero.push_san(jugada_san)
    assert partida.terminada

    with pytest.raises(ValueError):
        mover_desde_foto(partida.id)


def test_mover_desde_foto_en_partida_inexistente_lanza_keyerror() -> None:
    with pytest.raises(KeyError):
        mover_desde_foto("no-existe")


def test_analisis_completo_devuelve_un_item_por_jugada_con_evaluaciones_invertidas() -> None:
    partida = crear_partida(nivel=1)
    mover(partida.id, "e2e4")

    resultado = analisis_completo(partida.id, tiempo_limite=0.1)

    assert resultado["partida_id"] == partida.id
    assert len(resultado["jugadas"]) == len(partida.jugadas_san)

    primera = resultado["jugadas"][0]
    assert primera["numero_ply"] == 1
    assert primera["color"] == "blanco"
    assert primera["jugada_san"] == "e4"
    assert primera["fen_antes"].startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w")
    assert "evaluacion_cp" in primera
    assert "mejor_jugada_motor" in primera
    assert len(primera["variantes_candidatas"]) > 0


def test_analisis_completo_ignora_el_nivel_de_la_partida_y_evalua_a_fuerza_maxima() -> None:
    # Bug real de QA: `analisis_completo` usaba `partida.nivel` (la fuerza
    # débil configurada para que Stockfish juegue EN VIVO contra un
    # principiante) también para el análisis retrospectivo — a nivel bajo,
    # Stockfish no ve una dama regalada y devuelve evaluaciones sin sentido
    # (ej. "0 cp" en una posición con una dama de desventaja). Esta trampa de
    # apertura (1.e4 e5 2.Qh5 Nc6 3.Qxf7+ Kxf7) deja a blancas dama por peón
    # abajo tras "Kxf7" — una posición objetivamente mala que un nivel bajo
    # mal configurado evaluaría como equilibrada.
    jugadas_san = ["e4", "e5", "Qh5", "Nc6", "Qxf7+", "Kxf7"]

    def construir_partida_con_dama_perdida(nivel: int):
        partida = crear_partida(nivel=nivel)
        for jugada in jugadas_san:
            partida.tablero.push_san(jugada)
        servicio_partida._repositorio.guardar(partida)
        return partida

    partida_nivel_bajo = construir_partida_con_dama_perdida(nivel=1)
    partida_nivel_alto = construir_partida_con_dama_perdida(nivel=20)

    resultado_bajo = analisis_completo(partida_nivel_bajo.id, tiempo_limite=0.1)
    resultado_alto = analisis_completo(partida_nivel_alto.id, tiempo_limite=0.1)

    jugada_bajo = resultado_bajo["jugadas"][-1]
    jugada_alto = resultado_alto["jugadas"][-1]
    assert jugada_bajo["jugada_san"] == jugada_alto["jugada_san"] == "Kxf7"

    eval_bajo = jugada_bajo["evaluacion_cp"]
    eval_alto = jugada_alto["evaluacion_cp"]
    assert eval_bajo is not None and eval_alto is not None

    # Mismo signo (negras arriba de material tras capturar la dama) y del
    # mismo orden de magnitud — no "0 cp" sin sentido en la partida de nivel
    # bajo, que es justo lo que reportó QA.
    assert (eval_bajo > 0) == (eval_alto > 0)
    assert abs(eval_bajo) > 400
    assert abs(eval_bajo - eval_alto) < 200


def _partida_con_jugadas(*jugadas_san: str):
    partida = crear_partida(nivel=5)
    for jugada in jugadas_san:
        partida.tablero.push_san(jugada)
    servicio_partida._repositorio.guardar(partida)
    return partida


def test_analisis_completo_pide_todas_las_posiciones_al_motor_en_una_sola_llamada(monkeypatch) -> None:
    partida = _partida_con_jugadas("e4", "e5", "Nf3")
    llamadas = []

    def motor_falso(fens, nivel, tiempo_limite):
        llamadas.append((list(fens), nivel, tiempo_limite))
        return [
            {
                "jugada": "e4", "evaluacion_cp": 20 + i, "mate_en": None, "profundidad": 10,
                "nodos": 100, "variacion_principal": ["e4"], "variantes_candidatas": [],
            }
            for i, _ in enumerate(fens)
        ]

    monkeypatch.setattr(servicio_partida, "analizar_posiciones", motor_falso)

    resultado = analisis_completo(partida.id, tiempo_limite=0.2)

    assert len(llamadas) == 1
    fens, nivel, tiempo_limite = llamadas[0]
    assert len(fens) == len(partida.jugadas_san) + 1
    assert fens[0] == partida.fen_inicial and fens[-1] == partida.fen
    assert (nivel, tiempo_limite) == (20, 0.2)
    assert [j["jugada_san"] for j in resultado["jugadas"]] == ["e4", "e5", "Nf3"]
    assert resultado["jugadas"][0]["evaluacion_cp"] == -21
    assert resultado["resumen"]["total_jugadas"] == 3


def test_analisis_completo_propaga_el_error_del_motor(monkeypatch) -> None:
    partida = _partida_con_jugadas("e4", "e5")

    def motor_roto(fens, nivel, tiempo_limite):
        raise RuntimeError("Stockfish se cayó")

    monkeypatch.setattr(servicio_partida, "analizar_posiciones", motor_roto)

    with pytest.raises(RuntimeError, match="se cayó"):
        analisis_completo(partida.id)


def test_analisis_completo_en_partida_inexistente_lanza_keyerror() -> None:
    with pytest.raises(KeyError):
        analisis_completo("no-existe")


def test_partida_nueva_arranca_en_curso_sin_fechas_de_juego() -> None:
    partida = crear_partida(nivel=5, usuario_id=900)
    assert partida.estado == "en_curso"
    assert partida.iniciada_en is None
    assert partida.actualizada_en is None
    assert partida.jugadas_jugador == 0


def test_mover_fija_iniciada_en_actualizada_en_y_no_pisa_iniciada_en_de_nuevo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EstrategiaGuionada:
        def __init__(self) -> None:
            self._respuestas = iter(["e5", "Nc6"])

        def decidir_jugada(self, fen: str) -> str:
            return next(self._respuestas)

    # Una sola instancia reutilizada entre llamadas: si el lambda creara una
    # instancia nueva por llamada a `crear_estrategia_jugada` (como pasa
    # dentro de `mover()`), el iterador se reiniciaría en cada jugada.
    oponente = _EstrategiaGuionada()
    monkeypatch.setattr(servicio_partida, "crear_estrategia_jugada", lambda *a, **k: oponente)

    partida = crear_partida(nivel=5, usuario_id=901)

    mover(partida.id, "e2e4")
    partida_tras_primera = obtener_partida(partida.id)
    assert partida_tras_primera.iniciada_en is not None
    assert partida_tras_primera.actualizada_en is not None
    assert partida_tras_primera.jugadas_jugador == 1

    iniciada_en_original = partida_tras_primera.iniciada_en
    mover(partida.id, "g1f3")
    partida_tras_segunda = obtener_partida(partida.id)
    assert partida_tras_segunda.iniciada_en == iniciada_en_original
    assert partida_tras_segunda.jugadas_jugador == 2


def test_mover_marca_estado_terminada_cuando_la_partida_termina(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5, tipo_oponente="motor", usuario_id=902)

    class _EstrategiaMateDelTonto:
        def __init__(self) -> None:
            self._respuestas = iter(["e5", "Qh4#"])

        def decidir_jugada(self, fen: str) -> str:
            return next(self._respuestas)

    # Misma precaución que arriba: una sola instancia para las dos llamadas.
    oponente = _EstrategiaMateDelTonto()
    monkeypatch.setattr(servicio_partida, "crear_estrategia_jugada", lambda *a, **k: oponente)

    mover(partida.id, "f2f3")
    resultado = mover(partida.id, "g2g4")

    assert resultado["terminada"] is True
    assert obtener_partida(partida.id).estado == "terminada"


def test_crear_partida_borra_partida_pendiente_del_mismo_usuario_sin_jugadas() -> None:
    vieja = crear_partida(nivel=5, usuario_id=910)

    crear_partida(nivel=5, usuario_id=910)

    with pytest.raises(KeyError):
        obtener_partida(vieja.id)


def test_crear_partida_abandona_partida_pendiente_con_jugadas_suficientes() -> None:
    from backend.servicios.calibracion import MIN_JUGADAS_PARTIDA_VALIDA

    vieja = crear_partida(nivel=5, usuario_id=911)
    # Ruy López hasta el enroque corto — secuencia real, siempre legal, sin
    # pasar por el motor (evita depender de qué responda Stockfish).
    for jugada_san in ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7"]:
        vieja.tablero.push_san(jugada_san)
    servicio_partida._repositorio.guardar(vieja)
    assert vieja.jugadas_jugador == MIN_JUGADAS_PARTIDA_VALIDA

    crear_partida(nivel=5, usuario_id=911)

    assert obtener_partida(vieja.id).estado == "abandonada"


def test_crear_partida_no_toca_partida_en_demostracion_del_mismo_usuario() -> None:
    from backend.servicios.partida.servicio_partida import actualizar_permisos

    demo = crear_partida(nivel=5, usuario_id=912)
    actualizar_permisos(demo.id, {"es_demostracion": True}, usuario_id_facilitador=912)

    crear_partida(nivel=5, usuario_id=912)

    partida_demo_tras_crear = obtener_partida(demo.id)
    assert partida_demo_tras_crear.estado == "en_curso"
    assert partida_demo_tras_crear.es_demostracion is True


def test_crear_partida_no_afecta_partidas_de_otros_usuarios() -> None:
    ajena = crear_partida(nivel=5, usuario_id=920)

    crear_partida(nivel=5, usuario_id=921)

    assert obtener_partida(ajena.id).estado == "en_curso"


def test_partida_en_curso_de_devuelve_none_sin_partidas() -> None:
    from backend.servicios.partida.servicio_partida import partida_en_curso_de

    assert partida_en_curso_de(930) is None


def test_partida_en_curso_de_ignora_partida_recien_creada_sin_jugar() -> None:
    from backend.servicios.partida.servicio_partida import partida_en_curso_de

    crear_partida(nivel=5, usuario_id=931)

    assert partida_en_curso_de(931) is None


def test_partida_en_curso_de_devuelve_la_que_tiene_jugadas() -> None:
    from backend.servicios.partida.servicio_partida import partida_en_curso_de

    partida = crear_partida(nivel=5, usuario_id=932)
    mover(partida.id, "e2e4")

    en_curso = partida_en_curso_de(932)

    assert en_curso is not None
    assert en_curso.id == partida.id


def _analisis_falso(evaluacion_cp: int, jugada: str = "e4", mate_en: int | None = None) -> dict:
    return {
        "evaluacion_cp": evaluacion_cp,
        "mate_en": mate_en,
        "jugada": jugada,
        "variantes_candidatas": [],
    }


def test_mover_califica_de_verdad_la_jugada_del_jugador(monkeypatch: pytest.MonkeyPatch) -> None:
    # Antes de su jugada el jugador estaba +30; después, el rival (que mueve ahora) ve +900:
    # el jugador cayó a -900 de golpe, o sea que regaló material. Tiene que salir blunder,
    # no el "buena" fijo que devolvía antes.
    monkeypatch.setattr(
        servicio_partida,
        "analizar_posiciones",
        lambda fens, nivel, tiempo: [_analisis_falso(30, jugada="d4"), _analisis_falso(900, jugada="d5")],
    )
    partida = crear_partida(nivel=5)

    resultado = mover(partida.id, "e2e4")

    en_vivo = resultado["retroalimentacion_en_vivo"]
    assert en_vivo["calidad"] == "blunder"
    assert en_vivo["perdida_cp"] == 930
    assert en_vivo["mejor_alternativa"] == "d4"


def test_mover_con_una_jugada_precisa_no_marca_perdida(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        servicio_partida,
        "analizar_posiciones",
        lambda fens, nivel, tiempo: [_analisis_falso(30), _analisis_falso(-30, jugada="e5")],
    )
    partida = crear_partida(nivel=5)

    en_vivo = mover(partida.id, "e2e4")["retroalimentacion_en_vivo"]

    assert en_vivo["perdida_cp"] == 0
    assert en_vivo["calidad"] in {"brillante", "mejor", "excelente"}


def test_mover_si_falla_el_motor_al_calificar_la_jugada_igual_queda_aplicada(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _motor_caido(*args, **kwargs):
        raise RuntimeError("motor caído")

    monkeypatch.setattr(servicio_partida, "analizar_posiciones", _motor_caido)
    partida = crear_partida(nivel=5)

    resultado = mover(partida.id, "e2e4")

    assert resultado["retroalimentacion_en_vivo"] is None
    assert resultado["jugadas"][0] == "e4"


def test_analisis_completo_no_marca_blunder_a_la_jugada_que_da_mate() -> None:
    # Mate del pasillo: las blancas juegan Te8# y la partida termina ahí.
    partida = crear_partida(nivel=5, fen_inicial="6k1/5ppp/8/8/8/8/5PPP/4R1K1 w - - 0 1")
    mover(partida.id, "e1e8")

    jugada_final = analisis_completo(partida.id)["jugadas"][-1]

    assert jugada_final["jugada_san"] == "Re8#"
    assert jugada_final["calidad"] == "mejor"


def test_analisis_completo_incluye_las_jugadas_en_casillas_y_el_conteo_del_jugador() -> None:
    partida = crear_partida(nivel=5)
    mover(partida.id, "e2e4")

    resultado = analisis_completo(partida.id)

    primera = resultado["jugadas"][0]
    assert primera["jugada_uci"] == "e2e4"
    assert len(primera["mejor_jugada_uci"]) in (4, 5)  # la mejor del motor, ej. "e2e4" o "d2d4"
    # Solo la jugada del estudiante cuenta en su conteo (la de la contraparte va aparte).
    assert sum(resultado["resumen"]["conteo_jugador"].values()) == 1
    assert sum(resultado["resumen"]["conteo_calidad"].values()) == 2


def test_mover_informa_el_tablero_justo_despues_de_la_jugada_del_humano() -> None:
    partida = crear_partida(nivel=5)

    resultado = mover(partida.id, "e2e4")

    # Antes de la respuesta del rival: el peón ya está en e4 y todavía le toca a las negras.
    assert resultado["fen_tras_jugada"].startswith("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b")
    # El estado final sí incluye la respuesta (vuelven a mover las blancas).
    assert resultado["fen"] != resultado["fen_tras_jugada"]
    assert resultado["fen"].split()[1] == "w"


def test_terminar_por_tiempo_da_la_victoria_al_otro_lado() -> None:
    from backend.servicios.partida.servicio_partida import terminar_por_tiempo

    partida = crear_partida(nivel=5)
    mover(partida.id, "e2e4")

    terminada = terminar_por_tiempo(partida.id, "blancas")

    assert terminada.terminada and terminada.resultado == "0-1" and terminada.estado == "terminada"
    # Ya no admite jugadas, y el estado queda guardado.
    with pytest.raises(ValueError):
        mover(partida.id, "d2d4")
    assert obtener_partida(partida.id).resultado == "0-1"


def test_terminar_por_tiempo_si_se_le_acaba_al_rival_ganan_las_blancas() -> None:
    from backend.servicios.partida.servicio_partida import terminar_por_tiempo

    partida = crear_partida(nivel=5)
    mover(partida.id, "e2e4")

    assert terminar_por_tiempo(partida.id, "negras").resultado == "1-0"


def test_terminar_por_tiempo_rechaza_los_casos_invalidos() -> None:
    from backend.servicios.partida.servicio_partida import terminar_por_tiempo

    partida = crear_partida(nivel=5)
    with pytest.raises(ValueError):  # todavía sin jugadas
        terminar_por_tiempo(partida.id, "blancas")
    mover(partida.id, "e2e4")
    with pytest.raises(ValueError):  # lado inválido
        terminar_por_tiempo(partida.id, "verdes")
    terminar_por_tiempo(partida.id, "blancas")
    with pytest.raises(ValueError):  # ya terminada
        terminar_por_tiempo(partida.id, "blancas")
    with pytest.raises(KeyError):
        terminar_por_tiempo("no-existe", "blancas")


def test_una_partida_terminada_por_tiempo_se_restaura_terminada_desde_la_base() -> None:
    """Guardada en la tabla `partida` el resultado queda en `resultado`; al reconstruirla sigue terminada."""
    from backend.repositorios.repositorio_partida import _fila_a_partida, _partida_a_fila
    from backend.servicios.partida.servicio_partida import terminar_por_tiempo

    partida = crear_partida(nivel=5)
    mover(partida.id, "e2e4")
    terminar_por_tiempo(partida.id, "blancas")

    restaurada = _fila_a_partida(_partida_a_fila(obtener_partida(partida.id)))

    assert restaurada.terminada and restaurada.resultado == "0-1"
    assert not restaurada.tablero.is_game_over()  # el tablero no tiene final: es por tiempo


def test_crear_partida_con_tipo_rechazado_no_cierra_la_partida_pendiente() -> None:
    pendiente = crear_partida(nivel=5, usuario_id=4321)
    with pytest.raises(ValueError):
        crear_partida(nivel=5, tipo_oponente="humano", usuario_id=4321)
    assert servicio_partida.obtener_partida(pendiente.id).estado == "en_curso"


def test_detectar_jugada_en_tablero_devuelve_el_resultado_sin_aplicarlo(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    pedidas = {}
    resultado_visto = {"estado": "dudosa", "jugada": None, "uci": None, "margen": 0.5, "motivo": "x", "alternativas": [], "coronacion_asumida": False}

    def _falso(fen, camaras=None):
        pedidas["camaras"] = camaras
        return resultado_visto

    monkeypatch.setattr(servicio_partida, "_leer_jugada_probable", _falso)

    assert servicio_partida.detectar_jugada_en_tablero(partida.id, ["secundaria"]) == resultado_visto
    assert pedidas["camaras"] == ["secundaria"]
    assert obtener_partida(partida.id).fen == partida.fen  # no se movió nada


def test_detectar_jugada_en_tablero_sin_modelo_de_ocupacion_pide_el_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    monkeypatch.setattr(servicio_partida, "_leer_jugada_probable", lambda fen, camaras=None: False)

    with pytest.raises(FileNotFoundError):
        servicio_partida.detectar_jugada_en_tablero(partida.id)


def test_mover_desde_foto_pasa_las_camaras_elegidas(monkeypatch: pytest.MonkeyPatch) -> None:
    partida = crear_partida(nivel=5)
    pedidas = {}

    def _falso(fen, camaras=None):
        pedidas["camaras"] = camaras
        return "e4"

    monkeypatch.setattr(servicio_partida, "_detectar_por_ocupacion", _falso)
    mover_desde_foto(partida.id, ["principal"])

    assert pedidas["camaras"] == ["principal"]
