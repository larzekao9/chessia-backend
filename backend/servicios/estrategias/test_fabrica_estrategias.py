import chess
import pytest

from backend.servicios.aprendizaje.niveles import NIVEL_MAX_MODELO
from backend.servicios.estrategias.estrategia_jugada import EstrategiaModelo, EstrategiaStockfish
from backend.servicios.estrategias.fabrica_estrategias import crear_estrategia_jugada

POSICION_INICIAL = chess.STARTING_FEN


@pytest.fixture(autouse=True)
def _sin_apertura_variada(monkeypatch) -> None:
    """Estas pruebas miran a qué función llega la jugada desde la posición inicial: con la apertura variada encendida el modo maestro iría por otro camino (se prueba aparte)."""
    monkeypatch.setenv("AJEDREZ_TURING_APERTURA_VARIADA", "0")


def test_crear_estrategia_jugada_motor_devuelve_estrategia_stockfish() -> None:
    estrategia = crear_estrategia_jugada("motor", nivel=5)
    assert isinstance(estrategia, EstrategiaStockfish)
    assert estrategia.nivel == 5


def test_crear_estrategia_jugada_modelo_devuelve_estrategia_modelo() -> None:
    estrategia = crear_estrategia_jugada("modelo")
    assert isinstance(estrategia, EstrategiaModelo)


def test_crear_estrategia_jugada_tipo_no_soportado_lanza_error() -> None:
    with pytest.raises(ValueError):
        crear_estrategia_jugada("participante")


def test_estrategia_stockfish_decide_una_jugada_legal() -> None:
    estrategia = EstrategiaStockfish(nivel=5)
    tablero = chess.Board(POSICION_INICIAL)
    jugada_san = estrategia.decidir_jugada(POSICION_INICIAL)
    assert tablero.parse_san(jugada_san) in tablero.legal_moves


def test_estrategia_modelo_decide_una_jugada_legal(tmp_path) -> None:
    torch = pytest.importorskip("torch")

    from backend.servicios.aprendizaje.modelo_jugadas import NUM_CLASES, RedPrediccionJugadas

    ruta_checkpoint = tmp_path / "checkpoint_prueba.pt"
    torch.save(
        {"state_dict": RedPrediccionJugadas().state_dict(), "num_clases": NUM_CLASES},
        ruta_checkpoint,
    )

    estrategia = EstrategiaModelo(ruta_checkpoint=ruta_checkpoint)
    tablero = chess.Board(POSICION_INICIAL)
    jugada_san = estrategia.decidir_jugada(POSICION_INICIAL)
    assert tablero.parse_san(jugada_san) in tablero.legal_moves


def test_crear_estrategia_jugada_modelo_pasa_el_nivel_a_estrategia_modelo() -> None:
    estrategia = crear_estrategia_jugada("modelo", nivel=7)
    assert isinstance(estrategia, EstrategiaModelo)
    assert estrategia.nivel == 7


def test_crear_estrategia_jugada_modelo_sin_nivel_explicito_usa_el_techo_del_modelo() -> None:
    assert crear_estrategia_jugada("modelo").nivel == NIVEL_MAX_MODELO == 18


@pytest.mark.parametrize("nivel", [19, 20])
def test_crear_estrategia_jugada_modelo_recorta_el_nivel_al_techo_del_modelo(nivel: int) -> None:
    assert crear_estrategia_jugada("modelo", nivel=nivel).nivel == NIVEL_MAX_MODELO


@pytest.mark.parametrize("nivel", [0, 17, 18])
def test_crear_estrategia_jugada_modelo_no_toca_los_niveles_hasta_el_techo(nivel: int) -> None:
    assert crear_estrategia_jugada("modelo", nivel=nivel).nivel == nivel


def test_crear_estrategia_jugada_motor_conserva_la_escala_completa() -> None:
    assert crear_estrategia_jugada("motor", nivel=20).nivel == 20


def test_techo_del_modelo_coincide_con_el_nivel_maestro_de_inferencia() -> None:
    pytest.importorskip("torch")

    from backend.servicios.aprendizaje import inferencia

    assert inferencia.NIVEL_MAX_MODELO == inferencia.NIVEL_MAESTRO == NIVEL_MAX_MODELO
    assert inferencia.temperatura_y_pool_por_nivel(19) == inferencia.temperatura_y_pool_por_nivel(
        NIVEL_MAX_MODELO
    )


def test_estrategia_modelo_sin_nivel_es_retrocompatible() -> None:
    assert EstrategiaModelo().nivel is None


def test_estrategia_modelo_pasa_su_nivel_a_predecir_jugada_maestra(monkeypatch) -> None:
    """`decidir_jugada` reenvía el nivel guardado: es lo que adapta el modelo al jugador."""
    pytest.importorskip("torch")

    from backend.servicios.aprendizaje import inferencia

    recibido = {}

    def falsa_prediccion(fen, ruta, **kwargs):
        recibido["fen"] = fen
        recibido["kwargs"] = kwargs
        return "e4"

    monkeypatch.setattr(inferencia, "predecir_jugada_maestra", falsa_prediccion)

    assert crear_estrategia_jugada("modelo", nivel=9).decidir_jugada(POSICION_INICIAL) == "e4"
    assert recibido["fen"] == POSICION_INICIAL
    assert recibido["kwargs"] == {"nivel": 9}

    EstrategiaModelo(profundidad_busqueda=0).decidir_jugada(POSICION_INICIAL)
    assert recibido["kwargs"] == {"nivel": None}


def test_estrategia_modelo_nivel_bajo_decide_jugadas_legales_y_no_usa_stockfish(
    tmp_path, monkeypatch
) -> None:
    torch = pytest.importorskip("torch")

    from backend.servicios.aprendizaje.modelo_jugadas import NUM_CLASES, RedPrediccionJugadas
    from backend.servicios.estrategias import estrategia_jugada

    def stockfish_prohibido(*args, **kwargs):
        raise AssertionError("el modelo propio no debe consultar a Stockfish")

    monkeypatch.setattr(estrategia_jugada, "calcular_jugada", stockfish_prohibido)

    ruta_checkpoint = tmp_path / "checkpoint_prueba.pt"
    torch.save(
        {"state_dict": RedPrediccionJugadas().state_dict(), "num_clases": NUM_CLASES},
        ruta_checkpoint,
    )

    estrategia = crear_estrategia_jugada("modelo", nivel=3)
    estrategia.ruta_checkpoint = ruta_checkpoint
    tablero = chess.Board(POSICION_INICIAL)
    for _ in range(10):
        jugada_san = estrategia.decidir_jugada(POSICION_INICIAL)
        assert tablero.parse_san(jugada_san) in tablero.legal_moves


# --- búsqueda de variantes de Turing (solo en modo maestro) ----------------------------------------------------------
def _falsa_busqueda(monkeypatch) -> dict:
    """Reemplaza la búsqueda por una de mentira que registra con qué parámetros la llamaron."""
    pytest.importorskip("torch")

    from backend.servicios.aprendizaje import busqueda_turing, inferencia

    llamadas: dict = {"busqueda": [], "red_sola": []}

    def falsa_busqueda(fen, ruta, profundidad, tiempo, peso):
        llamadas["busqueda"].append((profundidad, tiempo, peso))
        return "e4"

    def falsa_red_sola(fen, ruta, **kwargs):
        llamadas["red_sola"].append(kwargs)
        return "d4"

    monkeypatch.setattr(busqueda_turing, "elegir_jugada_con_busqueda", falsa_busqueda)
    monkeypatch.setattr(inferencia, "predecir_jugada_maestra", falsa_red_sola)
    return llamadas


@pytest.mark.parametrize("nivel", [None, 18, 20])
def test_modo_maestro_usa_la_busqueda_con_los_valores_medidos(monkeypatch, nivel) -> None:
    monkeypatch.delenv("AJEDREZ_TURING_PROFUNDIDAD", raising=False)
    llamadas = _falsa_busqueda(monkeypatch)

    assert EstrategiaModelo(nivel=nivel).decidir_jugada(POSICION_INICIAL) == "e4"
    assert llamadas["busqueda"] == [(3, 1.0, 40.0)] and not llamadas["red_sola"]


@pytest.mark.parametrize("nivel", [0, 9, 17])
def test_por_debajo_del_maestro_no_usa_la_busqueda(monkeypatch, nivel) -> None:
    llamadas = _falsa_busqueda(monkeypatch)

    assert EstrategiaModelo(nivel=nivel).decidir_jugada(POSICION_INICIAL) == "d4"
    assert not llamadas["busqueda"] and llamadas["red_sola"] == [{"nivel": nivel}]


def test_con_profundidad_cero_la_busqueda_queda_desactivada(monkeypatch) -> None:
    llamadas = _falsa_busqueda(monkeypatch)

    assert EstrategiaModelo(nivel=18, profundidad_busqueda=0).decidir_jugada(POSICION_INICIAL) == "d4"
    assert not llamadas["busqueda"]


def test_la_busqueda_se_configura_por_variables_de_entorno(monkeypatch) -> None:
    monkeypatch.setenv("AJEDREZ_TURING_PROFUNDIDAD", "2")
    monkeypatch.setenv("AJEDREZ_TURING_TIEMPO", "0.5")
    monkeypatch.setenv("AJEDREZ_TURING_PESO_RED", "60")
    llamadas = _falsa_busqueda(monkeypatch)

    EstrategiaModelo(nivel=18).decidir_jugada(POSICION_INICIAL)

    assert llamadas["busqueda"] == [(2, 0.5, 60.0)]


def test_una_variable_de_entorno_invalida_cae_en_el_valor_por_defecto(monkeypatch) -> None:
    monkeypatch.setenv("AJEDREZ_TURING_PROFUNDIDAD", "mucha")
    monkeypatch.setenv("AJEDREZ_TURING_TIEMPO", "")
    llamadas = _falsa_busqueda(monkeypatch)

    EstrategiaModelo(nivel=18).decidir_jugada(POSICION_INICIAL)

    assert llamadas["busqueda"] == [(3, 1.0, 40.0)]


def test_con_busqueda_el_modelo_tampoco_consulta_a_stockfish(tmp_path, monkeypatch) -> None:
    torch = pytest.importorskip("torch")

    from backend.servicios.aprendizaje.modelo_jugadas import NUM_CLASES, RedPrediccionJugadas
    from backend.servicios.estrategias import estrategia_jugada

    def stockfish_prohibido(*args, **kwargs):
        raise AssertionError("el modelo propio no debe consultar a Stockfish")

    monkeypatch.setattr(estrategia_jugada, "calcular_jugada", stockfish_prohibido)
    ruta_checkpoint = tmp_path / "checkpoint_prueba.pt"
    torch.save({"state_dict": RedPrediccionJugadas().state_dict(), "num_clases": NUM_CLASES}, ruta_checkpoint)

    jugada = EstrategiaModelo(ruta_checkpoint=ruta_checkpoint, nivel=18, profundidad_busqueda=2).decidir_jugada(
        POSICION_INICIAL
    )

    assert chess.Board(POSICION_INICIAL).parse_san(jugada) in chess.Board(POSICION_INICIAL).legal_moves
