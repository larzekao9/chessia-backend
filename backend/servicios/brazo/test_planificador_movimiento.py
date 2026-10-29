import chess
import pytest
from backend.servicios.brazo.planificador_movimiento import (
    repuestos_para_coronar,
    Paso,
    cementerio_de_pieza,
    coronaciones_de,
    forzar_dama,
    lugares_ocupados,
    piezas_retiradas,
    planificar,
    resumen_del_cementerio,
    retiradas_de,
)


def _pasos(fen: str, uci: str) -> list[Paso]:
    tablero = chess.Board(fen)
    return planificar(tablero, tablero.parse_uci(uci))


def test_jugada_simple_es_un_solo_paso() -> None:
    assert _pasos(chess.STARTING_FEN, "e2e4") == [Paso("P", "e2", "e4")]
    assert _pasos(chess.STARTING_FEN, "g1f3") == [Paso("N", "g1", "f3")]


def test_captura_retira_primero_la_capturada_al_cementerio() -> None:
    fen = "rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2"
    assert _pasos(fen, "e4d5") == [Paso("p", "d5", "cementerio:0"), Paso("P", "e4", "d5")]


def test_el_cementerio_sigue_donde_quedo() -> None:
    tablero = chess.Board()
    for uci in ("e2e4", "d7d5", "e4d5"):
        tablero.push_uci(uci)
    assert piezas_retiradas(tablero) == 1
    tablero.push_uci("d8d5")
    assert planificar(tablero, tablero.parse_uci("b1c3")) == [Paso("N", "b1", "c3")]
    tablero.push_uci("b1c3")
    assert planificar(tablero, tablero.parse_uci("d5d2"))[0] == Paso("P", "d2", "cementerio:2")


def test_enroque_mueve_rey_y_torre() -> None:
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    assert _pasos(fen, "e1g1") == [Paso("K", "e1", "g1"), Paso("r", "h1", "f1")]
    assert _pasos(fen, "e1c1") == [Paso("K", "e1", "c1"), Paso("r", "a1", "d1")]
    assert _pasos(fen.replace(" w ", " b "), "e8c8") == [Paso("k", "e8", "c8"), Paso("r", "a8", "d8")]


def test_captura_al_paso_retira_el_peon_de_otra_casilla() -> None:
    fen = "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 3"
    assert _pasos(fen, "d4e3") == [Paso("P", "e4", "cementerio:0"), Paso("p", "d4", "e3")]


def test_coronacion_retira_el_peon_y_pone_la_dama_de_reserva() -> None:
    # con pocas piezas en el FEN, las que faltan para 32 cuentan como ya retiradas: el peón va a la siguiente casilla libre del cementerio
    assert _pasos("8/P6k/8/8/8/8/8/K7 w - - 0 1", "a7a8q") == [Paso("P", "a7", "cementerio:29"), Paso("q", "reserva:dama_blanca", "a8")]
    assert _pasos("7k/8/8/8/8/8/p7/7K b - - 0 1", "a2a1q")[1] == Paso("q", "reserva:dama_negra", "a1")


def test_coronacion_con_captura_retira_primero_la_capturada() -> None:
    pasos = _pasos("1r5k/P7/8/8/8/8/8/K7 w - - 0 1", "a7b8q")
    assert pasos == [Paso("r", "b8", "cementerio:28"), Paso("P", "a7", "cementerio:29"), Paso("q", "reserva:dama_blanca", "b8")]


def test_solo_corona_a_dama() -> None:
    with pytest.raises(ValueError, match="dama"):
        _pasos("8/P6k/8/8/8/8/8/K7 w - - 0 1", "a7a8n")
    assert forzar_dama(chess.Move.from_uci("a7a8n")) == chess.Move.from_uci("a7a8q")
    assert forzar_dama(chess.Move.from_uci("e2e4")) == chess.Move.from_uci("e2e4")


# --- los dos cementerios ordenados por piezas ------------------------------------------------------------------------------------------------


def test_cada_tipo_tiene_su_lugar_en_los_dos_cementerios() -> None:
    # el del brazo (`cementerio:K`) recibe las blancas; el del jugador (`cementerio_jugador:K`), las negras; los dos con el mismo orden
    assert [cementerio_de_pieza("P", i) for i in (0, 7)] == ["cementerio:0", "cementerio:7"]  # fila de peones, junto al tablero
    assert [cementerio_de_pieza("N", i) for i in (0, 1)] == ["cementerio:8", "cementerio:9"]  # fila de piezas, en línea recta: caballos, alfiles, torres, damas
    assert [cementerio_de_pieza("B", i) for i in (0, 1)] == ["cementerio:10", "cementerio:11"]
    assert [cementerio_de_pieza("R", i) for i in (0, 1)] == ["cementerio:12", "cementerio:13"]
    assert [cementerio_de_pieza("Q", i) for i in (0, 1)] == ["cementerio:14", "cementerio:15"]
    assert [cementerio_de_pieza("p", 0), cementerio_de_pieza("n", 0), cementerio_de_pieza("q", 1)] == ["cementerio_jugador:0", "cementerio_jugador:8", "cementerio_jugador:15"]


def test_el_cementerio_ordenado_avisa_si_no_hay_lugar_y_no_acepta_al_rey() -> None:
    with pytest.raises(ValueError, match="no tiene lugar"):
        cementerio_de_pieza("N", 2)
    with pytest.raises(ValueError, match="rey"):
        cementerio_de_pieza("K", 0)


def test_las_retiradas_por_tipo_salen_de_la_posicion() -> None:
    tablero = chess.Board()
    assert retiradas_de(tablero, chess.WHITE, chess.KNIGHT) == 0
    tablero.remove_piece_at(chess.B1)
    assert retiradas_de(tablero, chess.WHITE, chess.KNIGHT) == 1 and retiradas_de(tablero, chess.BLACK, chess.KNIGHT) == 0
    tablero.remove_piece_at(chess.A7)
    assert retiradas_de(tablero, chess.BLACK, chess.PAWN) == 1


def test_una_captura_va_al_cementerio_del_color_de_la_pieza() -> None:
    tablero = chess.Board()
    for uci in ("e2e4", "d7d5"):
        tablero.push_uci(uci)
    # el peón negro que se captura: cementerio del jugador (lo deja la persona), fila de peones, primer lugar
    assert planificar(tablero, tablero.parse_uci("e4d5"), por_piezas=True) == [Paso("p", "d5", "cementerio_jugador:0"), Paso("P", "e4", "d5")]
    # una blanca capturada por el brazo va al cementerio del brazo
    otro = chess.Board("rnbqkbnr/ppp1pppp/8/8/3p4/2N5/PPPP1PPP/R1BQKBNR b KQkq - 0 3")
    assert planificar(otro, otro.parse_uci("d4c3"), por_piezas=True)[0] == Paso("N", "c3", "cementerio:8")


def test_la_coronacion_toma_la_dama_del_cementerio_del_jugador() -> None:
    tablero = chess.Board("k7/8/8/8/8/8/p7/7K b - - 0 1")  # un solo peón negro: faltan 7, así que el peón coronado va al lugar 8 de su fila
    jugada = tablero.parse_uci("a2a1q")
    assert planificar(tablero, jugada, por_piezas=True) == [Paso("p", "a2", "cementerio_jugador:7"), Paso("q", "cementerio_jugador:14", "a1")]  # la primera dama del cementerio del jugador
    assert planificar(tablero, jugada) == [Paso("p", "a2", "cementerio:29"), Paso("q", "reserva:dama_negra", "a1")]  # sin `por_piezas` sigue como siempre (por orden de llegada: 32 - 3 piezas)


def test_la_segunda_coronacion_toma_la_segunda_dama() -> None:
    tablero = chess.Board("k7/8/8/8/8/8/pp5K/8 b - - 0 1")
    tablero.push_uci("a2a1q")
    tablero.push_uci("h2h3")
    assert coronaciones_de(tablero, chess.BLACK) == 1 and coronaciones_de(tablero, chess.WHITE) == 0
    pasos = planificar(tablero, tablero.parse_uci("b2b1q"), por_piezas=True)
    assert pasos[1] == Paso("q", "cementerio_jugador:15", "b1")  # la segunda dama
    tablero.push_uci("b2b1q")
    assert coronaciones_de(tablero, chess.BLACK) == 2
    with pytest.raises(ValueError, match="no tiene lugar"):  # una tercera coronación ya no tendría dama
        cementerio_de_pieza("q", coronaciones_de(tablero, chess.BLACK))


def test_el_cementerio_ordenado_necesita_filas_de_ocho() -> None:
    tablero = chess.Board()
    with pytest.raises(ValueError, match="filas de 8"):
        planificar(tablero, tablero.parse_uci("e2e4"), por_piezas=True, columnas=6)


def test_el_resumen_para_la_pantalla_dice_que_lugares_estan_ocupados_y_las_damas() -> None:
    tablero = chess.Board()
    tablero.remove_piece_at(chess.B1)  # falta un caballo blanco
    tablero.remove_piece_at(chess.D7)  # y un peón negro
    resumen = resumen_del_cementerio(tablero, por_piezas=True)
    assert {"lugar": "cementerio:8", "pieza": "N"} in resumen["ocupados"] and {"lugar": "cementerio_jugador:0", "pieza": "p"} in resumen["ocupados"]
    assert resumen["color_brazo"] == "negras" and resumen["reservas"] == {"lugares": ["cementerio_jugador:14", "cementerio_jugador:15"], "usadas": 0}
    assert not any(o["pieza"] == "q" for o in lugares_ocupados(chess.Board("rnb1kbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")))  # la dama negra capturada no ocupa un lugar de dama del jugador
    assert resumen_del_cementerio(tablero, por_piezas=False)["ocupados"] == []


def test_el_bloque_de_peones_puede_ser_un_cuadrado_y_las_demas_piezas_siguen_en_linea() -> None:
    # 4 peones por fila: un bloque de 4 × 2; la fila de piezas pasa a ser la tercera
    assert [cementerio_de_pieza("P", i, peones_por_fila=4) for i in range(8)] == ["cementerio:0", "cementerio:1", "cementerio:2", "cementerio:3", "cementerio:8", "cementerio:9", "cementerio:10", "cementerio:11"]
    assert [cementerio_de_pieza("N", i, peones_por_fila=4) for i in (0, 1)] == ["cementerio:16", "cementerio:17"]
    assert cementerio_de_pieza("q", 1, peones_por_fila=4) == "cementerio_jugador:23"
    assert cementerio_de_pieza("n", 0, peones_por_fila=8) == "cementerio_jugador:8"  # con 8 por fila queda como siempre
    with pytest.raises(ValueError, match="de 1 a 8"):
        cementerio_de_pieza("P", 0, peones_por_fila=9)


def test_la_coronacion_y_el_resumen_respetan_el_bloque_de_peones() -> None:
    tablero = chess.Board("k7/8/8/8/8/8/p7/7K b - - 0 1")
    pasos = planificar(tablero, tablero.parse_uci("a2a1q"), por_piezas=True, peones_por_fila=4)
    assert pasos == [Paso("p", "a2", "cementerio_jugador:11"), Paso("q", "cementerio_jugador:22", "a1")]  # el peón 8 cae en la 2.ª fila del bloque; la dama 1, en la fila de piezas (3.ª)
    resumen = resumen_del_cementerio(chess.Board(), por_piezas=True, peones_por_fila=4)
    assert resumen["peones_por_fila"] == 4 and resumen["filas_de_peones"] == 2
    assert resumen["reservas"]["lugares"] == ["cementerio_jugador:22", "cementerio_jugador:23"]


def _cadena(paso: Paso) -> list[tuple[str, str]]:
    """(pieza, origen) de un paso y de sus alternativas, en el orden en que el brazo los prueba."""
    cadena = []
    while paso is not None:
        cadena.append((paso.pieza, paso.origen))
        paso = paso.si_falla
    return cadena


def test_al_coronar_busca_damas_y_despues_piezas_negras_capturadas_y_al_final_su_peon() -> None:
    tablero = chess.Board("k7/8/8/8/8/8/p7/7K b - - 0 1")  # a las negras les faltan torres, alfiles, caballos, dama y 7 peones
    pasos = planificar(tablero, tablero.parse_uci("a2a1q"), por_piezas=True)
    assert pasos[0] == Paso("p", "a2", "cementerio_jugador:7")
    assert _cadena(pasos[1]) == [
        ("q", "cementerio_jugador:14"),  # las dos damas (la capturada o la de repuesto)
        ("q", "cementerio_jugador:15"),
        ("r", "cementerio_jugador:12"),  # después una torre negra que capturó la persona
        ("p", "cementerio_jugador:7"),  # y si nada está, su propio peón, que acaba de dejar
    ]
    assert all(p.destino == "a1" for p in (pasos[1], pasos[1].si_falla))


def test_cada_coronacion_usa_un_repuesto_distinto() -> None:
    tablero = chess.Board("k7/8/8/8/8/8/pp5K/8 b - - 0 1")
    tablero.push_uci("a2a1q")
    tablero.push_uci("h2h3")
    tablero.push_uci("a1a5")  # la dama coronada se aparta
    tablero.push_uci("h3h4")
    assert repuestos_para_coronar(tablero, chess.BLACK)[0] == "cementerio_jugador:15"  # la primera ya se usó


def test_sin_piezas_negras_fuera_solo_quedan_las_damas_de_repuesto() -> None:
    tablero = chess.Board()
    assert repuestos_para_coronar(tablero, chess.BLACK) == ["cementerio_jugador:14", "cementerio_jugador:15"]
