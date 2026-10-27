"""Patrón Strategy: intercambia quién decide la jugada de respuesta sin que
`servicio_partida.py` lo note (PLAN_IMPLEMENTACION_COMPLETO.md, sección 4.1).
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

import chess

from backend.servicios.aprendizaje.niveles import NIVEL_MAX_MODELO
from backend.servicios.motor.motor_ajedrez import calcular_jugada

# Búsqueda de variantes de Turing (ver `busqueda_turing.py`). Valores medidos en la v6: profundidad 3 con peso 40
# (Elo ≈ 1808 frente a ≈ 1355 de la red sola, ver `docs/turing_hoja_de_ruta.md`). `AJEDREZ_TURING_PROFUNDIDAD=0`
# la desactiva y deja la red con el filtro táctico de siempre.
PROFUNDIDAD_BUSQUEDA_POR_DEFECTO = 3
TIEMPO_BUSQUEDA_POR_DEFECTO = 1.0
PESO_RED_POR_DEFECTO = 40.0


def _numero_de_entorno(nombre: str, por_defecto: float, tipo: type = float):
    """Lee un número de una variable de entorno; si falta o es inválido, usa `por_defecto`."""
    try:
        return tipo(os.environ[nombre])
    except (KeyError, ValueError):
        return por_defecto


def parametros_de_busqueda() -> tuple[int, float, float]:
    """`(profundidad, tiempo en segundos, peso de la red)` de la búsqueda de Turing, con las variables de entorno aplicadas.

    Profundidad 0 significa búsqueda desactivada. Los mismos valores los usan la jugada real y el panel de Razonamiento.
    """
    return (
        _numero_de_entorno("AJEDREZ_TURING_PROFUNDIDAD", PROFUNDIDAD_BUSQUEDA_POR_DEFECTO, int),
        _numero_de_entorno("AJEDREZ_TURING_TIEMPO", TIEMPO_BUSQUEDA_POR_DEFECTO),
        _numero_de_entorno("AJEDREZ_TURING_PESO_RED", PESO_RED_POR_DEFECTO),
    )


class EstrategiaJugada(ABC):
    """Interfaz común para cualquier forma de decidir la jugada de respuesta."""

    @abstractmethod
    def decidir_jugada(self, fen: str) -> str:
        """Devuelve la jugada elegida para la posición dada, en notación SAN."""


class EstrategiaStockfish(EstrategiaJugada):
    """Stockfish decide la jugada.

    `EstrategiaModelo` usa el modelo propio de HU3/HU4. `EstrategiaHumano`,
    para partida contra otro jugador (HU10), sigue siendo alcance de tesis —
    se agrega recién cuando exista lo que necesita.
    """

    def __init__(self, nivel: int = 20):
        self.nivel = nivel

    def decidir_jugada(self, fen: str) -> str:
        return calcular_jugada(fen, nivel=self.nivel)


JUGADAS_DE_APERTURA_VARIADA = 5
"""Hasta esta jugada (número de jugada de la partida, la de cada bando) Turing en modo maestro elige entre las mejores candidatas de la red en vez de la primera."""
NIVEL_PARA_VARIAR_APERTURA = 16
"""Con qué «nivel de muestreo» elige en esas primeras jugadas: la temperatura (≈1,2) y el pool (3 candidatas) de ese nivel, con la penalidad por pieza colgada."""


def apertura_variada_activa() -> bool:
    """Si en modo maestro Turing varía su apertura (`AJEDREZ_TURING_APERTURA_VARIADA=0` la apaga, por ejemplo para medir su fuerza con jugadas siempre iguales)."""
    return os.environ.get("AJEDREZ_TURING_APERTURA_VARIADA", "1").strip().lower() not in ("0", "false", "no", "off")


class EstrategiaModelo(EstrategiaJugada):
    """El modelo propio (HU4) decide la jugada, en vez de Stockfish.

    El import de `inferencia` es diferido a `decidir_jugada` porque esa
    dependencia carga `torch`, no instalado en todas las máquinas del equipo
    (ver `pytest.importorskip("torch")` en `test_modelo_jugadas.py`) — un
    import a nivel de módulo rompería el oponente `"motor"` para quien no
    tenga torch instalado.

    `nivel` (0-20, el del perfil del jugador) calibra qué tan fuerte juega el
    modelo: `None` o >= 18 es el modo maestro determinista; por debajo, el modelo
    muestrea entre sus mejores candidatas (ver `predecir_jugada_maestra`). En
    ningún caso interviene Stockfish.

    En modo maestro (`nivel` `None` o >= 18) Turing combina la red con una búsqueda de
    variantes propia (`profundidad_busqueda` > 0): la red propone y pondera, el cálculo
    evita errores tácticos. Por debajo de ese nivel la búsqueda NO se usa: ahí la
    gracia es justamente que el modelo juegue de forma más humana y falible.

    **Apertura variada.** El modo maestro es determinista: con la misma posición elige siempre la misma jugada, y todas las partidas empezarían igual (siempre la misma
    siciliana, por ejemplo). Por eso, en las primeras `JUGADAS_DE_APERTURA_VARIADA` jugadas elige entre las mejores candidatas de SU red —que aprendió de partidas humanas, así
    que sus candidatas son las aperturas que juegan las personas: siciliana, Petrov, española, francesa…— y desde ahí sigue con su juego de siempre. Sigue decidiendo el modelo.
    """

    def __init__(
        self,
        ruta_checkpoint: str | Path | None = None,
        nivel: int | None = None,
        profundidad_busqueda: int | None = None,
    ):
        self.ruta_checkpoint = ruta_checkpoint
        self.nivel = nivel
        self.profundidad_busqueda = (
            profundidad_busqueda
            if profundidad_busqueda is not None
            else parametros_de_busqueda()[0]
        )

    def _usa_busqueda(self) -> bool:
        modo_maestro = self.nivel is None or self.nivel >= NIVEL_MAX_MODELO
        return modo_maestro and self.profundidad_busqueda > 0

    def decidir_jugada(self, fen: str) -> str:
        from backend.servicios.aprendizaje.inferencia import (
            RUTA_CHECKPOINT_POR_DEFECTO,
            predecir_jugada_maestra,
        )

        ruta = self.ruta_checkpoint if self.ruta_checkpoint is not None else RUTA_CHECKPOINT_POR_DEFECTO
        modo_maestro = self.nivel is None or self.nivel >= NIVEL_MAX_MODELO
        if modo_maestro and apertura_variada_activa() and chess.Board(fen).fullmove_number <= JUGADAS_DE_APERTURA_VARIADA:
            return predecir_jugada_maestra(fen, ruta, nivel=NIVEL_PARA_VARIAR_APERTURA)
        if self._usa_busqueda():
            from backend.servicios.aprendizaje.busqueda_turing import elegir_jugada_con_busqueda

            _, tiempo, peso_red = parametros_de_busqueda()
            return elegir_jugada_con_busqueda(fen, ruta, self.profundidad_busqueda, tiempo, peso_red)
        return predecir_jugada_maestra(fen, ruta, nivel=self.nivel)
