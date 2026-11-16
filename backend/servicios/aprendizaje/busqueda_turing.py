"""Búsqueda de variantes de Turing: la intuición de la red neuronal + cálculo propio de unas pocas jugadas.

La red sola imita jugadas de maestros pero no calcula: no ve que una jugada pierde una pieza dos jugadas después. Este módulo
le suma lo que le falta, sin consultar a Stockfish (regla 1 de las reglas del proyecto):

1. La red propone y puntúa las jugadas (probabilidad de cada jugada legal).
2. Un buscador propio (negamax con poda alfa-beta, búsqueda de capturas y profundización iterativa) calcula las variantes de
   las mejores candidatas con una evaluación propia (material + posición de las piezas).
3. El puntaje final de cada candidata es `cálculo + peso_red * ln(probabilidad de la red)`: el cálculo corrige los errores
   tácticos y la red aporta el criterio posicional de los maestros.

`peso_red` regula cuánto manda la red: con 0 manda solo el cálculo; con valores altos, la red decide salvo que el cálculo
encuentre una ventaja mayor. Se elige midiendo partidas (ver `training/medir_elo.py`).

Todo es determinista y no aprende en vivo.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import chess

MATE = 100_000
"""Puntaje de un mate (en centipeones); los mates más cercanos valen más."""

VALORES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}

# Tablas de posición de cada pieza (centipeones), vistas desde las blancas con la fila 8 arriba, para estimar qué tan bien
# colocada está (centro, desarrollo, rey a salvo...). Aproximación clásica y simple; el cálculo y la red hacen el resto.
_PEON = (0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10, 5, 5, 10, 25, 25, 10, 5, 5,
         0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5, 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0)
_CABALLO = (-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30,
            -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30,
            -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50)
_ALFIL = (-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10,
          -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10,
          -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20)
_TORRE = (0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5,
          -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0)
_DAMA = (-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10,
         -5, 0, 5, 5, 5, 5, 0, -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10,
         -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20)
_REY_MEDIO = (-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
              -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
              -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10,
              20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20)
_REY_FINAL = (-50, -40, -30, -20, -20, -30, -40, -50, -30, -20, -10, 0, 0, -10, -20, -30, -30, -10, 20, 30, 30, 20, -10, -30,
              -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 20, 30, 30, 20, -10, -30,
              -30, -30, 0, 0, 0, 0, -30, -30, -50, -30, -30, -30, -30, -30, -30, -50)
_TABLAS = {chess.PAWN: _PEON, chess.KNIGHT: _CABALLO, chess.BISHOP: _ALFIL, chess.ROOK: _TORRE, chess.QUEEN: _DAMA}

_MATERIAL_FINAL = 2600
"""Si cada bando tiene menos material que esto (sin contar peones) se considera final y el rey sale al centro."""


def evaluar(tablero: chess.Board) -> int:
    """Puntaje de la posición en centipeones **desde el punto de vista de quien tiene el turno** (positivo = va ganando).

    Suma el material y la colocación de cada pieza. No mira mate ni tablas: eso lo resuelve la búsqueda.
    """
    material = {chess.WHITE: 0, chess.BLACK: 0}
    for pieza in tablero.piece_map().values():
        if pieza.piece_type not in (chess.PAWN, chess.KING):
            material[pieza.color] += VALORES[pieza.piece_type]
    es_final = material[chess.WHITE] <= _MATERIAL_FINAL and material[chess.BLACK] <= _MATERIAL_FINAL
    tabla_rey = _REY_FINAL if es_final else _REY_MEDIO

    puntaje = 0
    for casilla, pieza in tablero.piece_map().items():
        fila_vista, columna = chess.square_rank(casilla), chess.square_file(casilla)
        indice = (7 - fila_vista) * 8 + columna if pieza.color == chess.WHITE else fila_vista * 8 + columna
        tabla = tabla_rey if pieza.piece_type == chess.KING else _TABLAS[pieza.piece_type]
        valor = VALORES[pieza.piece_type] + tabla[indice]
        puntaje += valor if pieza.color == chess.WHITE else -valor
    return puntaje if tablero.turn == chess.WHITE else -puntaje


class _TiempoAgotado(Exception):
    """Se acabó el tiempo de la búsqueda."""


def _orden_de_jugada(tablero: chess.Board, jugada: chess.Move) -> int:
    """Puntaje para ordenar jugadas: probar primero las capturas ganadoras (mejor poda) y las promociones."""
    puntaje = 0
    if tablero.is_capture(jugada):
        victima = tablero.piece_type_at(jugada.to_square) or chess.PAWN  # captura al paso: la víctima es un peón
        atacante = tablero.piece_type_at(jugada.from_square) or chess.PAWN
        puntaje += 10_000 + 10 * VALORES[victima] - VALORES[atacante]
    if jugada.promotion:
        puntaje += 9_000 + VALORES[jugada.promotion]
    return puntaje


class _Buscador:
    """Negamax con poda alfa-beta y búsqueda de capturas, con un límite de tiempo."""

    _NODOS_ENTRE_RELOJES = 1024
    _PROFUNDIDAD_CAPTURAS = 8

    def __init__(self, limite: float | None, evaluador: Callable[[chess.Board], int] | None = None) -> None:
        self.limite = limite
        self.nodos = 0
        self.evaluador = evaluador or evaluar
        # Con un evaluador lento (la red de valor, ~1 ms por posición) el reloj se mira más seguido para no pasarse del tiempo.
        self._nodos_entre_relojes = self._NODOS_ENTRE_RELOJES if evaluador is None else 32

    def _contar_nodo(self) -> None:
        self.nodos += 1
        if self.limite is not None and self.nodos % self._nodos_entre_relojes == 0 and time.perf_counter() > self.limite:
            raise _TiempoAgotado

    def negamax(self, tablero: chess.Board, profundidad: int, alfa: int, beta: int, ply: int) -> int:
        self._contar_nodo()
        if tablero.is_repetition(2) or tablero.is_insufficient_material() or tablero.halfmove_clock >= 100:
            return 0
        if profundidad <= 0:
            return self.capturas(tablero, alfa, beta, ply, self._PROFUNDIDAD_CAPTURAS)

        jugadas = sorted(tablero.legal_moves, key=lambda j: _orden_de_jugada(tablero, j), reverse=True)
        if not jugadas:
            return -MATE + ply if tablero.is_check() else 0
        mejor = -MATE
        for jugada in jugadas:
            tablero.push(jugada)
            puntaje = -self.negamax(tablero, profundidad - 1, -beta, -alfa, ply + 1)
            tablero.pop()
            if puntaje > mejor:
                mejor = puntaje
                if mejor > alfa:
                    alfa = mejor
                    if alfa >= beta:
                        break
        return mejor

    def capturas(self, tablero: chess.Board, alfa: int, beta: int, ply: int, restante: int) -> int:
        """Sigue las capturas hasta que la posición se calme, para no evaluar en medio de un intercambio."""
        self._contar_nodo()
        en_jaque = tablero.is_check()
        if not en_jaque:
            estatico = self.evaluador(tablero)
            if estatico >= beta:
                return estatico
            alfa = max(alfa, estatico)
            if restante <= 0:
                return estatico
            jugadas = [j for j in tablero.legal_moves if tablero.is_capture(j) or j.promotion]
        else:
            # En jaque no se puede "quedarse quieto": hay que responder con cualquier jugada legal.
            if restante <= 0:
                return self.evaluador(tablero)
            jugadas = list(tablero.legal_moves)
            if not jugadas:
                return -MATE + ply
            estatico = -MATE
        jugadas.sort(key=lambda j: _orden_de_jugada(tablero, j), reverse=True)
        mejor = estatico
        for jugada in jugadas:
            tablero.push(jugada)
            puntaje = -self.capturas(tablero, -beta, -alfa, ply + 1, restante - 1)
            tablero.pop()
            if puntaje > mejor:
                mejor = puntaje
                if mejor > alfa:
                    alfa = mejor
                    if alfa >= beta:
                        break
        return mejor


PROBABILIDAD_MINIMA = 1e-4
"""Piso de probabilidad que usa `buscar` (evita `ln(0)`); se usa el mismo para recuperar el cálculo puro."""


@dataclass
class ResultadoBusqueda:
    """Lo que decidió la búsqueda y con qué datos."""

    jugada: chess.Move
    puntaje: float
    profundidad: int
    nodos: int
    candidatas: list[tuple[chess.Move, float]] = field(default_factory=list)
    """Candidatas con su puntaje final, de mejor a peor, de la última profundidad completada."""


def buscar(
    tablero: chess.Board,
    probabilidades: dict[chess.Move, float] | None = None,
    profundidad: int = 3,
    tiempo_limite: float | None = 2.0,
    peso_red: float = 40.0,
    candidatas_max: int = 12,
    evaluador: Callable[[chess.Board], int] | None = None,
    valor_raiz: Callable[[chess.Board], int] | None = None,
    peso_valor: float = 0.5,
) -> ResultadoBusqueda:
    """Elige la mejor jugada combinando el cálculo con la probabilidad que le da la red a cada jugada.

    Args:
        tablero: posición a decidir (no se modifica).
        probabilidades: probabilidad de la red para cada jugada legal; `None` = todas iguales (solo cuenta el cálculo).
        profundidad: media-jugadas máximas a calcular (profundización iterativa: 1, 2, ... hasta este valor).
        tiempo_limite: segundos máximos (`None` = sin límite). Si se acaba a mitad de una profundidad, se usa la anterior.
        peso_red: cuánto pesa la red frente al cálculo (centipeones por unidad de logaritmo de la probabilidad).
        candidatas_max: cuántas jugadas calcular: las mejores según la red más las capturas y los jaques.
        evaluador: función que puntúa una posición en centipeones desde quien mueve; por defecto la evaluación manual
            (`evaluar`). Se puede pasar la red de valor (`EvaluadorRedValor`).
        valor_raiz: opinión extra, barata, sobre la posición que deja cada candidata (una sola evaluación por candidata, no
            por nodo): se suma `peso_valor` veces su valor al puntaje final. Pensado para la red de valor.

    Raises:
        ValueError: si la posición no tiene jugadas legales.
    """
    legales = list(tablero.legal_moves)
    if not legales:
        raise ValueError("La posición no tiene jugadas legales")
    probabilidades = probabilidades or {}
    uniforme = 1.0 / len(legales)

    def probabilidad(jugada: chess.Move) -> float:
        return max(probabilidades.get(jugada, uniforme if not probabilidades else PROBABILIDAD_MINIMA), PROBABILIDAD_MINIMA)

    # Candidatas: las que más le gustan a la red + jugadas tácticas (capturas, jaques) que la red podría subestimar.
    por_red = sorted(legales, key=probabilidad, reverse=True)
    elegidas = por_red[: max(1, candidatas_max)]
    for jugada in por_red[candidatas_max:]:
        if len(elegidas) >= candidatas_max + 6:
            break
        if tablero.is_capture(jugada) or tablero.gives_check(jugada):
            elegidas.append(jugada)
    if len(elegidas) == 1:
        return ResultadoBusqueda(elegidas[0], 0.0, 0, 0, [(elegidas[0], 0.0)])

    limite = None if tiempo_limite is None else time.perf_counter() + tiempo_limite
    buscador = _Buscador(limite, evaluador)
    mejor_resultado: ResultadoBusqueda | None = None
    copia = tablero.copy(stack=True)
    for nivel in range(1, max(1, profundidad) + 1):
        puntajes: list[tuple[chess.Move, float]] = []
        try:
            for jugada in elegidas:
                copia.push(jugada)
                calculo = -buscador.negamax(copia, nivel - 1, -MATE, MATE, 1)
                extra = -peso_valor * valor_raiz(copia) if valor_raiz is not None and abs(calculo) < MATE - 1000 else 0.0
                copia.pop()
                puntajes.append((jugada, calculo + extra + peso_red * math.log(probabilidad(jugada))))
        except _TiempoAgotado:
            while len(copia.move_stack) > len(tablero.move_stack):
                copia.pop()
            if mejor_resultado is None and puntajes:
                # Ni la profundidad 1 se completó: se usa lo calculado hasta ahora (nunca queda sin jugada).
                puntajes.sort(key=lambda x: x[1], reverse=True)
                return ResultadoBusqueda(puntajes[0][0], puntajes[0][1], 0, buscador.nodos, puntajes)
            break
        puntajes.sort(key=lambda x: x[1], reverse=True)
        mejor_resultado = ResultadoBusqueda(puntajes[0][0], puntajes[0][1], nivel, buscador.nodos, puntajes)
        if abs(puntajes[0][1]) > MATE - 1000:
            break  # mate encontrado: no hace falta calcular más
    if mejor_resultado is None:
        return ResultadoBusqueda(por_red[0], 0.0, 0, buscador.nodos, [(por_red[0], 0.0)])
    return mejor_resultado


def historial_util(tablero: chess.Board) -> chess.Board:
    """Copia de `tablero` con solo las jugadas que pueden formar una repetición: las que van desde la última captura o avance de peón.

    Una captura o un avance de peón no se deshacen, así que ninguna posición anterior a ellos puede volver a darse: el resto del historial no aporta nada al detectar
    repeticiones y solo haría más lento cada nodo de la búsqueda (`is_repetition` recorre el historial). Con eso, la búsqueda que parte de la copia conoce las posiciones
    que ya se dieron en la partida real. Un tablero sin jugadas (armado solo desde un FEN) se devuelve igual.
    """
    cantidad = min(tablero.halfmove_clock, len(tablero.move_stack))
    if cantidad <= 0:
        return chess.Board(tablero.fen())
    base = tablero.copy()
    jugadas = [base.pop() for _ in range(cantidad)]
    jugadas.reverse()
    util = chess.Board(base.fen())
    for jugada in jugadas:
        util.push(jugada)
    return util


def elegir_jugada_con_busqueda(
    fen: str,
    ruta_checkpoint: str | Path | None = None,
    profundidad: int = 3,
    tiempo_limite: float | None = 2.0,
    peso_red: float = 40.0,
    evaluador: Callable[[chess.Board], int] | None = None,
    valor_raiz: Callable[[chess.Board], int] | None = None,
    peso_valor: float = 0.5,
    tablero: chess.Board | None = None,
) -> str:
    """Jugada de Turing (en SAN) usando la red del checkpoint y la búsqueda de variantes (con `evaluador` opcional).

    Con `tablero` (la partida real, con sus jugadas) la búsqueda conoce las posiciones que ya se dieron y trata como tablas las repeticiones (`historial_util`): evita repetir
    cuando va ganando y la busca cuando va perdiendo. Sin él parte solo del `fen`, como siempre. Si se pasa, `tablero` tiene que estar en la posición `fen`.
    """
    from backend.servicios.aprendizaje.inferencia import RUTA_CHECKPOINT_POR_DEFECTO, probabilidades_legales

    tablero = historial_util(tablero) if tablero is not None else chess.Board(fen)
    probabilidades = probabilidades_legales(tablero, ruta_checkpoint or RUTA_CHECKPOINT_POR_DEFECTO)
    resultado = buscar(
        tablero, probabilidades, profundidad, tiempo_limite, peso_red,
        evaluador=evaluador, valor_raiz=valor_raiz, peso_valor=peso_valor,
    )
    return tablero.san(resultado.jugada)


def explicar_decision(
    tablero: chess.Board,
    probabilidades: dict[chess.Move, float],
    profundidad: int = 3,
    tiempo_limite: float | None = 1.0,
    peso_red: float = 40.0,
    maximo_candidatas: int = 6,
) -> dict:
    """Decide con `buscar` y devuelve el desglose de la decisión, para mostrarlo en el panel de Razonamiento Neuronal.

    Cada candidata trae la probabilidad de la red, el cálculo puro (centipeones, desde quien mueve) y el puntaje final
    `cálculo + peso_red * ln(probabilidad)`; el cálculo se recupera restando el aporte de la red al puntaje final, así
    que no se calcula nada dos veces. Son datos reales de la búsqueda: nada se inventa ni se estima aparte.

    Returns:
        Diccionario con `jugada`/`jugada_uci` (la elegida), `jugada_red_sola` (la preferida por la red), `corrige_a_la_red`,
        `profundidad`, `nodos`, `tiempo_ms`, `peso_red` y `candidatas` (mejores primero, hasta `maximo_candidatas`).

    Raises:
        ValueError: si la posición no tiene jugadas legales.
    """
    inicio = time.perf_counter()
    resultado = buscar(tablero, probabilidades, profundidad, tiempo_limite, peso_red)
    tiempo_ms = (time.perf_counter() - inicio) * 1000

    def probabilidad_de(jugada: chess.Move) -> float:
        return probabilidades.get(jugada, 0.0)

    mejor_de_la_red = max(tablero.legal_moves, key=probabilidad_de) if probabilidades else resultado.jugada
    candidatas = []
    for posicion, (jugada, puntaje) in enumerate(resultado.candidatas[:maximo_candidatas]):
        piso = max(probabilidad_de(jugada), PROBABILIDAD_MINIMA)
        calculo = puntaje - peso_red * math.log(piso)
        candidatas.append(
            {
                "jugada": tablero.san(jugada),
                "jugada_uci": jugada.uci(),
                "probabilidad": probabilidad_de(jugada),
                "calculo_cp": round(calculo),
                "puntaje": round(puntaje),
                "mate": abs(calculo) > MATE - 1000,
                "elegida": posicion == 0,
            }
        )
    return {
        "jugada": tablero.san(resultado.jugada),
        "jugada_uci": resultado.jugada.uci(),
        "jugada_red_sola": tablero.san(mejor_de_la_red),
        "corrige_a_la_red": resultado.jugada != mejor_de_la_red,
        "profundidad": resultado.profundidad,
        "nodos": resultado.nodos,
        "tiempo_ms": tiempo_ms,
        "peso_red": peso_red,
        "candidatas": candidatas,
    }
