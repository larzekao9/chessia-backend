"""Enseñar el cementerio llevando una pieza real: de dónde sale cada valor guardado (funciones puras, sin el brazo).

El cementerio es una cuadrícula: `origen` (el centro de la primera casilla), `paso` (de una casilla a la siguiente de la fila) y `paso_fila` (de una fila a la siguiente). En vez de escribir
milímetros, el brazo toma una pieza, la lleva a su lugar, se la corrige con las flechas (de a 1 mm) y al soltarla se guarda dónde quedó. Lo que define esa posición depende de qué
pieza se llevó, es decir, de su lugar (fila y columna) en la cuadrícula:

- la primera (fila 0, columna 0): el `origen`;
- otra de la primera fila (columna c): el `paso` (la distancia al origen dividida c; cuanto más lejos, más preciso);
- la primera de otra fila (fila f): el `paso_fila` (la distancia al origen dividida f);
- cualquier otra: el `paso_fila`, descontando lo que ya se sabe del paso.
"""
from __future__ import annotations

import math

PASO_MINIMO_MM = 30.0
"""Menos que esto entre dos casillas y las piezas se chocan en el cementerio (la base más ancha mide unos 30 mm): se avisa en vez de guardarlo."""
PASO_MAXIMO_MM = 1000.0
"""Más que esto es casi seguro que se llevó la pieza a otro lugar del que se estaba ajustando."""
DESNIVEL_MAXIMO_MM = 60.0


def prefijo_de_parametros(zona: str) -> str:
    """El comienzo del nombre de los parámetros de una zona: `cementerio_` (el del brazo) o `cementerio_jugador_` (el del jugador)."""
    return "cementerio_jugador_" if zona == "jugador" else "cementerio_"


def cambios_para(
    zona: str, fila: int, columna: int, origen: list[float] | None, paso: list[float] | None, x: float, y: float
) -> dict[str, list[float]]:
    """Qué parámetros del cementerio de `zona` cambia soltar la pieza en (`x`, `y`) estando en el lugar (`fila`, `columna`) de la cuadrícula.

    Raises:
        ValueError: no hay origen todavía (se enseña primero la primera pieza), o el paso saldría fuera de lo razonable (probablemente se soltó en otro lugar).
    """
    prefijo = prefijo_de_parametros(zona)
    x, y = round(x, 1), round(y, 1)
    if fila == 0 and columna == 0:
        return {f"{prefijo}origen": [x, y]}
    if origen is None:
        raise ValueError("Primero enseñá la primera pieza (peón 1) de este cementerio: es la referencia de las demás.")
    if fila == 0:
        valor, clave = [(x - origen[0]) / columna, (y - origen[1]) / columna], f"{prefijo}paso"
    else:
        sobre_el_paso = [columna * (paso[0] if paso else 0.0), columna * (paso[1] if paso else 0.0)]
        valor, clave = [(x - origen[0] - sobre_el_paso[0]) / fila, (y - origen[1] - sobre_el_paso[1]) / fila], f"{prefijo}paso_fila"
    valor = [round(valor[0], 2), round(valor[1], 2)]
    largo = math.hypot(*valor)
    if not PASO_MINIMO_MM <= largo <= PASO_MAXIMO_MM:
        raise ValueError(
            f"La separación saldría de {largo:.1f} mm (lo razonable es entre {PASO_MINIMO_MM:g} y {PASO_MAXIMO_MM:g} mm): ¿la soltaste en el lugar de la pieza que elegiste?"
        )
    return {clave: valor}


def desnivel_para(z_agarre: float, holgura: float, z_actual: float) -> float:
    """El desnivel entre el tablero y la mesa que hace que, con la pinza a `z_actual`, la base de la pieza quede a `holgura` de la mesa.

    `z_agarre` es la altura de la punta al agarrar esa pieza en el tablero (con su margen propio): la base de la pieza está esa distancia (el margen) por debajo de la punta.

    Raises:
        ValueError: el resultado queda fuera de 0 a `DESNIVEL_MAXIMO_MM`.
    """
    desnivel = round(z_agarre + holgura - z_actual, 1)
    if not 0.0 <= desnivel <= DESNIVEL_MAXIMO_MM:
        raise ValueError(f"El desnivel saldría de {desnivel:g} mm (tiene que estar entre 0 y {DESNIVEL_MAXIMO_MM:g}): revisá la altura de la pinza.")
    return desnivel
