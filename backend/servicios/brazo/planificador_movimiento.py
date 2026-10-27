"""Convierte una jugada de ajedrez en los pasos físicos que el brazo tiene que hacer (función pura: no toca ni el brazo ni la cámara).

Una jugada simple es un solo paso (origen → destino). Las demás necesitan más: la captura retira primero la pieza capturada al cementerio, el enroque mueve el rey
y la torre, la captura al paso retira un peón que no está en el destino, y la coronación retira el peón y pone una dama de reserva. Cada paso lleva el tipo de la pieza
que se agarra, para usar la apertura de pinza medida de esa pieza.

Los lugares que no son casillas se nombran con un prefijo: `cementerio:K` (la casilla K del cementerio) y `reserva:dama_blanca` / `reserva:dama_negra`.

Hay dos formas de llenar el cementerio. Por orden de llegada (el de siempre): una sola zona, la pieza que sale va a la primera casilla libre. O, con `por_piezas`, ORDENADO en DOS zonas
con el mismo orden: el cementerio del brazo (`cementerio:K`), donde el brazo deja las piezas del color de la persona que captura, y el del jugador (`cementerio_jugador:K`), del lado contrario
del tablero, donde la persona deja las piezas del color del brazo que captura (y el brazo su peón coronado). Cada zona tiene 2 filas de 8: la primera (junto al tablero) para los peones y la
segunda en línea recta por tipo (caballos, alfiles, torres, damas). Así cada uno toma de su propia zona la dama para coronar: el brazo, de las dos casillas de dama del cementerio del jugador;
la persona, de las del cementerio del brazo.
"""
from __future__ import annotations

from dataclasses import dataclass

import chess

PREFIJO_CEMENTERIO = "cementerio:"
PREFIJO_CEMENTERIO_JUGADOR = "cementerio_jugador:"
COLOR_DEL_BRAZO = chess.BLACK
"""Con las piezas del color del brazo se llena el cementerio del jugador; con las del otro, el del brazo. La persona siempre juega con blancas, así que el brazo juega con negras."""
PREFIJO_RESERVA = "reserva:"
PIEZAS_INICIALES = 32

COLUMNAS_POR_PIEZAS = 8
"""Casillas por fila de cada cementerio ordenado: los 8 peones de un color en una fila, o caballos, alfiles, torres y damas (2 de cada una) en otra."""
_INICIALES = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}
_CAPACIDAD = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 2}
"""Lugares de cada tipo en el cementerio ordenado: la dama tiene dos (una capturada, o una de reserva para coronar, y una más)."""
_PRIMERA_COLUMNA = {chess.PAWN: 0, chess.KNIGHT: 0, chess.BISHOP: 2, chess.ROOK: 4, chess.QUEEN: 6}


@dataclass(frozen=True)
class Paso:
    """Agarrar la pieza `pieza` (`"p"`, `"N"`…: la letra decide la apertura) de `origen` y soltarla en `destino`."""

    pieza: str
    origen: str
    destino: str


def piezas_retiradas(tablero: chess.Board) -> int:
    """Cuántas piezas hay ya en el cementerio: las que faltan en el tablero (32 − las que hay) más los peones coronados, que también se retiraron.

    Sale solo de la posición y sus jugadas, así no hay un contador que se pueda desincronizar. Una partida que arrancó de un FEN con menos piezas cuenta
    como si las faltantes ya estuvieran retiradas.
    """
    coronaciones = sum(1 for jugada in tablero.move_stack if jugada.promotion is not None)
    return max(0, PIEZAS_INICIALES - len(tablero.piece_map()) + coronaciones)


def cementerio(indice: int) -> str:
    return f"{PREFIJO_CEMENTERIO}{indice}"


def reserva_dama(color: chess.Color) -> str:
    return f"{PREFIJO_RESERVA}dama_{'blanca' if color == chess.WHITE else 'negra'}"


def retiradas_de(tablero: chess.Board, color: chess.Color, tipo: chess.PieceType) -> int:
    """Cuántas piezas de `tipo` y `color` ya están fuera del tablero (en el cementerio): las que tenía al empezar más las que se coronaron menos las que hay ahora.

    Como `piezas_retiradas`, sale solo de la posición y sus jugadas. Los peones coronados también se retiraron (no están en el tablero), y una dama coronada cuenta como una más.
    """
    coronadas = 0
    if tipo != chess.PAWN:
        fila_de_coronacion = 7 if color == chess.WHITE else 0
        coronadas = sum(1 for jugada in tablero.move_stack if jugada.promotion == tipo and chess.square_rank(jugada.to_square) == fila_de_coronacion)
    return max(0, _INICIALES[tipo] + coronadas - len(tablero.pieces(tipo, color)))


def coronaciones_de(tablero: chess.Board, color: chess.Color) -> int:
    """Cuántos peones de `color` ya coronaron en esta partida (cada una usó una dama de reserva)."""
    fila_de_coronacion = 7 if color == chess.WHITE else 0
    return sum(1 for jugada in tablero.move_stack if jugada.promotion is not None and chess.square_rank(jugada.to_square) == fila_de_coronacion)


def filas_de_peones(peones_por_fila: int = COLUMNAS_POR_PIEZAS) -> int:
    """Cuántas filas ocupa el bloque de los 8 peones con `peones_por_fila` por fila: 8 es una fila; 4, un bloque de 4 × 2; 3, de 3 × 3."""
    if not 1 <= peones_por_fila <= COLUMNAS_POR_PIEZAS:
        raise ValueError(f"Los peones por fila tienen que ser de 1 a {COLUMNAS_POR_PIEZAS} (hoy {peones_por_fila})")
    return -(-_CAPACIDAD[chess.PAWN] // peones_por_fila)


def posicion_en_cementerio(
    simbolo: str,
    indice: int,
    columnas: int = COLUMNAS_POR_PIEZAS,
    color_del_brazo: chess.Color = COLOR_DEL_BRAZO,
    peones_por_fila: int = COLUMNAS_POR_PIEZAS,
) -> tuple[str, int, int]:
    """`(prefijo, fila, columna)` en la cuadrícula del cementerio ordenado donde va la `indice`-ésima (desde 0) pieza `simbolo` que se retira.

    Las piezas del color del brazo van al cementerio del jugador (`cementerio_jugador:`) y las del otro color al del brazo (`cementerio:`). En los dos el orden es el mismo: los 8 peones en un bloque
    de `peones_por_fila` por fila (las filas de arriba, junto al tablero) y, en la fila siguiente, las demás piezas en línea recta: caballos, alfiles, torres y damas (dos lugares cada uno).

    Raises:
        ValueError: el rey no se retira, ya no queda lugar para otra pieza de ese tipo, o `peones_por_fila` no es de 1 a 8.
    """
    pieza = chess.Piece.from_symbol(simbolo)
    tipo = pieza.piece_type
    if tipo not in _CAPACIDAD:
        raise ValueError("El rey no va al cementerio")
    if indice >= _CAPACIDAD[tipo]:
        raise ValueError(f"El cementerio no tiene lugar para otra pieza '{simbolo}' (ya hay {indice}): hay que vaciarlo o ampliarlo")
    filas_peones = filas_de_peones(peones_por_fila)
    if tipo == chess.PAWN:
        fila, columna = divmod(indice, peones_por_fila)
    else:
        fila, columna = filas_peones, _PRIMERA_COLUMNA[tipo] + indice
    prefijo = PREFIJO_CEMENTERIO_JUGADOR if pieza.color == color_del_brazo else PREFIJO_CEMENTERIO
    return prefijo, fila, columna


def cementerio_de_pieza(
    simbolo: str,
    indice: int,
    columnas: int = COLUMNAS_POR_PIEZAS,
    color_del_brazo: chess.Color = COLOR_DEL_BRAZO,
    peones_por_fila: int = COLUMNAS_POR_PIEZAS,
) -> str:
    """La casilla (`cementerio:K` o `cementerio_jugador:K`, con K = fila × `columnas` + columna) donde va la `indice`-ésima pieza `simbolo` que se retira. Ver `posicion_en_cementerio`."""
    prefijo, fila, columna = posicion_en_cementerio(simbolo, indice, columnas, color_del_brazo, peones_por_fila)
    return f"{prefijo}{fila * columnas + columna}"


def planificar(
    tablero_antes: chess.Board,
    jugada: chess.Move,
    por_piezas: bool = False,
    columnas: int = COLUMNAS_POR_PIEZAS,
    color_del_brazo: chess.Color = COLOR_DEL_BRAZO,
    peones_por_fila: int = COLUMNAS_POR_PIEZAS,
) -> list[Paso]:
    """Los pasos para hacer `jugada` en el tablero físico, tal como estaba la posición antes de jugarla.

    Con `por_piezas` el cementerio está ordenado en dos zonas (ver `posicion_en_cementerio`) y la dama que se usa al coronar sale de los dos lugares de dama del cementerio del jugador (la primera
    coronación usa el primero, la segunda el segundo), en vez de `reserva:dama_*`. `columnas` es lo que mide una fila del cementerio (`ParametrosAgarre.cementerio_columnas`) y `peones_por_fila`,
    cuántos peones van en cada fila del bloque de peones.

    Raises:
        ValueError: la jugada es una coronación que no es a dama (el brazo solo corona a dama), no hay pieza en el origen, o el cementerio ordenado no tiene lugar para otra pieza.
    """
    pieza = tablero_antes.piece_at(jugada.from_square)
    if pieza is None:
        raise ValueError(f"No hay ninguna pieza en {chess.square_name(jugada.from_square)}")
    if por_piezas and columnas < COLUMNAS_POR_PIEZAS:
        raise ValueError(f"El cementerio ordenado por piezas necesita filas de {COLUMNAS_POR_PIEZAS} casillas (hoy {columnas})")
    origen, destino = chess.square_name(jugada.from_square), chess.square_name(jugada.to_square)
    retiradas = piezas_retiradas(tablero_antes)

    def al_cementerio(retirada: chess.Piece) -> str:
        """El lugar del cementerio para la pieza que se retira ahora."""
        nonlocal retiradas
        if por_piezas:
            return cementerio_de_pieza(
                retirada.symbol(), retiradas_de(tablero_antes, retirada.color, retirada.piece_type), columnas, color_del_brazo, peones_por_fila
            )
        lugar = cementerio(retiradas)
        retiradas += 1
        return lugar

    pasos: list[Paso] = []
    if tablero_antes.is_en_passant(jugada):
        victima = chess.square(chess.square_file(jugada.to_square), chess.square_rank(jugada.from_square))
        pieza_victima = tablero_antes.piece_at(victima)
        pasos.append(Paso(pieza_victima.symbol(), chess.square_name(victima), al_cementerio(pieza_victima)))
    elif tablero_antes.is_capture(jugada):
        capturada = tablero_antes.piece_at(jugada.to_square)
        pasos.append(Paso(capturada.symbol(), destino, al_cementerio(capturada)))
    if tablero_antes.is_castling(jugada):
        fila = chess.square_rank(jugada.from_square) + 1
        torre_origen, torre_destino = (f"h{fila}", f"f{fila}") if tablero_antes.is_kingside_castling(jugada) else (f"a{fila}", f"d{fila}")
        pasos += [Paso(pieza.symbol(), origen, destino), Paso("r", torre_origen, torre_destino)]
    elif jugada.promotion is not None:
        if jugada.promotion != chess.QUEEN:
            raise ValueError("El brazo solo corona a dama: la reserva es una dama por bando")
        if por_piezas:
            dama = chess.Piece(chess.QUEEN, pieza.color)
            reserva = cementerio_de_pieza(dama.symbol(), coronaciones_de(tablero_antes, pieza.color), columnas, color_del_brazo, peones_por_fila)  # la dama que toca para esta coronación
        else:
            reserva = reserva_dama(pieza.color)
        pasos += [Paso(pieza.symbol(), origen, al_cementerio(pieza)), Paso("q", reserva, destino)]
    else:
        pasos.append(Paso(pieza.symbol(), origen, destino))
    return pasos


def lugares_ocupados(tablero: chess.Board, color_del_brazo: chess.Color = COLOR_DEL_BRAZO, peones_por_fila: int = COLUMNAS_POR_PIEZAS) -> list[dict]:
    """Los lugares de los cementerios ordenados que ya tienen una pieza, por lo que falta en el tablero: `[{"lugar": "cementerio_jugador:3", "pieza": "p"}, …]`.

    Las damas del color del brazo no se cuentan: esos dos lugares son las damas para coronar (`resumen_del_cementerio`): la persona deja ahí la dama que captura o una de repuesto.
    """
    ocupados = []
    for color in (chess.WHITE, chess.BLACK):
        for tipo, capacidad in _CAPACIDAD.items():
            if tipo == chess.QUEEN and color == color_del_brazo:
                continue
            simbolo = chess.Piece(tipo, color).symbol()
            for indice in range(min(retiradas_de(tablero, color, tipo), capacidad)):
                ocupados.append({"lugar": cementerio_de_pieza(simbolo, indice, COLUMNAS_POR_PIEZAS, color_del_brazo, peones_por_fila), "pieza": simbolo})
    return ocupados


def resumen_del_cementerio(
    tablero: chess.Board, por_piezas: bool, color_del_brazo: chess.Color = COLOR_DEL_BRAZO, peones_por_fila: int = COLUMNAS_POR_PIEZAS
) -> dict:
    """Lo que la pantalla necesita para dibujar los cementerios: si están ordenados por piezas, cómo se arma el bloque de peones, los lugares ocupados y las damas para coronar.

    Con `por_piezas` falso solo informa eso (`ocupados` vacío): por orden de llegada no hay un lugar fijo por pieza que mostrar.
    """
    dama = chess.Piece(chess.QUEEN, color_del_brazo).symbol()
    return {
        "por_piezas": por_piezas,
        "color_brazo": "blancas" if color_del_brazo == chess.WHITE else "negras",
        "peones_por_fila": peones_por_fila,
        "filas_de_peones": filas_de_peones(peones_por_fila),
        "ocupados": lugares_ocupados(tablero, color_del_brazo, peones_por_fila) if por_piezas else [],
        "reservas": {
            "lugares": [cementerio_de_pieza(dama, i, COLUMNAS_POR_PIEZAS, color_del_brazo, peones_por_fila) for i in range(_CAPACIDAD[chess.QUEEN])],
            "usadas": coronaciones_de(tablero, color_del_brazo),
        },
    }


def forzar_dama(jugada: chess.Move) -> chess.Move:
    """La misma jugada pero coronando a dama si coronaba a otra pieza (regla de negocio: Turing solo corona a dama)."""
    if jugada.promotion is None or jugada.promotion == chess.QUEEN:
        return jugada
    return chess.Move(jugada.from_square, jugada.to_square, promotion=chess.QUEEN)
