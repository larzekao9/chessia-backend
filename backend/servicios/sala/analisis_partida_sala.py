"""Análisis general (neutro, de los dos bandos) de una sala terminada (PLAN_MULTIJUGADOR.md §6).

Stockfish sigue siendo la fuente de las evaluaciones: se reutiliza `analizar_partida` con
perspectiva neutra y SIN persistir nada en la tabla `jugada` (un análisis de sala no debe tocar
estadísticas ni calibración). El resultado se cachea en memoria del proceso (no persistente)
porque lo piden varios usuarios y Stockfish tarda varios segundos en una partida larga.
"""
from __future__ import annotations

import copy
import threading

from backend.modelos.partida import Partida
from backend.servicios.retroalimentacion.servicio_retroalimentacion import generar_resumen_partida

_cache_analisis: dict[str, dict] = {}
_lock_cache = threading.Lock()

CANTIDAD_MOMENTOS_CLAVE = 5


def motivo_fin(partida: Partida) -> str | None:
    """Por qué terminó la sala: `"mate"`, `"tablas"`, `"acuerdo"` (tablas aceptadas), `"abandono"`, `"tiempo"`, `"ausencia"`,
    `"sin_respuesta"`, `"inactividad"` o `None`.

    - `"abandono"`: un jugador abandonó (estado `terminada` con `resultado_por_tiempo`; hay ganador).
    - `"tiempo"`: a un jugador se le acabó el reloj (estado `terminada`, el que perdió quedó con 0 ms).
    - `"ausencia"`: un jugador se fue de una sala con reloj y la perdió (estado `abandonada` con `resultado_por_tiempo`; hay ganador).
    - `"sin_respuesta"`: en una sala sin reloj, a quien le tocaba jugar no respondió en `limpieza_salas.HORAS_SALA_INACTIVA` (estado
      `abandonada` con `resultado_por_tiempo`; hay ganador, perdió quien tenía el turno).
    - `"inactividad"`: la sala (con jugadas de los dos, pero sin ninguna reciente) venció sin que nadie tuviera el turno claro — en la
      práctica ya no ocurre desde que `"sin_respuesta"` siempre asigna un ganador; queda por compatibilidad con salas viejas.
    """
    if partida.estado == "abandonada":
        if partida.resultado_por_tiempo is None:
            return "inactividad"
        return "ausencia" if partida.control_tiempo_ms else "sin_respuesta"
    if not partida.terminada and partida.estado != "terminada":
        return None
    if partida.resultado_por_tiempo == "1/2-1/2" and not partida.tablero.is_game_over():
        return "acuerdo"
    if partida.resultado_por_tiempo is not None and not partida.tablero.is_game_over():
        sin_tiempo = partida.tiempo_negras_ms if partida.resultado_por_tiempo == "1-0" else partida.tiempo_blancas_ms
        return "tiempo" if partida.control_tiempo_ms and sin_tiempo == 0 else "abandono"
    tablero = partida.tablero
    if tablero.is_checkmate():
        return "mate"
    if (
        tablero.is_stalemate()
        or tablero.is_insufficient_material()
        or tablero.is_seventyfive_moves()
        or tablero.is_fivefold_repetition()
    ):
        return "tablas"
    return None


def _corregir_mate_dado(jugadas: list[dict]) -> None:
    """La jugada que da mate (`mate_en == 0`) es brillante: sin pérdida y nunca blunder."""
    for jugada in jugadas:
        if jugada.get("mate_en") == 0:
            jugada["calidad"] = "brillante"
            jugada["perdida_cp"] = 0


def _resumen_bando(resumen: dict, clave_precision: str, clave_conteo: str) -> dict:
    conteo = resumen[clave_conteo]
    return {
        "precision": resumen[clave_precision],
        "mejores": conteo["brillante"] + conteo["mejor"],
        "imprecisiones": conteo["imprecision"],
        "errores": conteo["error"],
        "blunders": conteo["blunder"],
    }


def _curva_desde_blancas(jugadas: list[dict]) -> list[dict]:
    """Evaluación tras cada jugada desde el punto de vista de las blancas (las negras se invierten)."""
    curva = []
    for jugada in jugadas:
        signo = 1 if jugada["color"] == "blanco" else -1
        cp, mate = jugada["evaluacion_cp"], jugada["mate_en"]
        curva.append({
            "ply": jugada["numero_ply"],
            "evaluacion_cp": None if cp is None else signo * cp,
            "mate_en": None if mate is None else signo * mate,
        })
    return curva


def _momentos_clave(jugadas: list[dict]) -> list[dict]:
    """Las jugadas con mayor pérdida (máximo 5), de mayor a menor; las que no perdieron nada no entran."""
    candidatas = [j for j in jugadas if j["perdida_cp"] > 0]
    candidatas.sort(key=lambda j: j["perdida_cp"], reverse=True)
    return [
        {
            "ply": j["numero_ply"],
            "color": j["color"],
            "jugada_san": j["jugada_san"],
            "calidad": j["calidad"],
            "perdida_cp": j["perdida_cp"],
            "mejor_jugada_san": j["mejor_jugada_motor"],
            "explicacion": j["explicacion"],
        }
        for j in candidatas[:CANTIDAD_MOMENTOS_CLAVE]
    ]


def construir_analisis(partida: Partida, rango: str = "Intermedio") -> dict:
    """Arma el análisis neutro de una sala terminada (contrato de §6.3, sin `codigo` ni nombres).

    La ruta agrega `codigo` y el `nombre` de cada bando. Cacheado por `partida.id`.
    """
    with _lock_cache:
        en_cache = _cache_analisis.get(partida.id)
    if en_cache is not None:
        return copy.deepcopy(en_cache)

    from backend.servicios.partida.servicio_partida import analizar_partida

    bruto = analizar_partida(partida, rango=rango, perspectiva="neutra", persistir=False)
    jugadas = bruto["jugadas"]
    _corregir_mate_dado(jugadas)
    resumen = generar_resumen_partida(jugadas, rango=rango)

    analisis = {
        "resultado": partida.resultado,
        "motivo_fin": motivo_fin(partida),
        "total_jugadas": len(jugadas),
        "blancas": _resumen_bando(resumen, "precision_jugador", "conteo_jugador"),
        "negras": _resumen_bando(resumen, "precision_contraparte", "conteo_contraparte"),
        "curva": _curva_desde_blancas(jugadas),
        "momentos_clave": _momentos_clave(jugadas),
        "jugadas": jugadas,
    }
    with _lock_cache:
        _cache_analisis[partida.id] = analisis
    return copy.deepcopy(analisis)
