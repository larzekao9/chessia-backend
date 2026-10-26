"""Limpieza de salas `humano` inactivas (PLAN_MULTIJUGADOR.md §5.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import chess

from backend.repositorios.repositorio_partida import RepositorioPartidas
from backend.servicios.sala import presencia_sala

MINUTOS_SALA_VACIA = 30
"""Una sala `esperando` sin segundo jugador se elimina si pasó este tiempo."""

MINUTOS_SALA_SIN_JUGADAS = 60
"""Una sala `en_curso` sin ninguna jugada se elimina si pasó este tiempo."""

HORAS_SALA_INACTIVA = 48
"""Una sala `en_curso` sin reloj, con jugadas pero sin ninguna nueva, pasa a `abandonada`: pierde quien tenía el turno (no respondió
en 2 días). Con reloj no hace falta este plazo: `revisar_reloj_y_ausencia` ya la cierra por tiempo o por ausencia mucho antes."""


def limpiar_salas_inactivas(repositorio: RepositorioPartidas | None = None) -> None:
    """Barrido de salas `humano` inactivas.

    Reglas:
    - `esperando` > 30 min sin segundo jugador → eliminar.
    - `en_curso` sin jugadas > 60 min → eliminar.
    - `en_curso` sin reloj, con jugadas pero sin ninguna nueva en `HORAS_SALA_INACTIVA` (2 días) → pierde quien tenía el turno
      (`abandonada` con ganador), si tiene al menos `MIN_JUGADAS_PARTIDA_VALIDA` jugadas; con menos se elimina sin asignar resultado
      (no llegó a ser una partida real).

    Contrato de una sala vencida por inactividad sin reloj (`estado == "abandonada"`): la respuesta de la API trae `terminada: true`,
    `resultado_por_tiempo` con quien ganó y `motivo_fin: "sin_respuesta"`. Es distinto de una sala con reloj que se cierra por ausencia
    (`motivo_fin: "ausencia"`, a los 20 s) o de un abandono manual (`estado == "terminada"`, `motivo_fin: "abandono"`).

    Se llama al arrancar el backend y en `GET /sala/activa` y `GET /sala/{codigo}` (igual que
    `limpiar_partidas_inactivas` para partidas contra IA), y antes de listar las partidas en vivo de un docente
    (`servicio_sala` ya resuelve el caso con reloj solo).
    """
    from backend.servicios.sala import servicio_sala

    repo = repositorio or servicio_sala._repositorio
    ahora = datetime.now(timezone.utc)
    for partida in repo.listar_salas_abiertas():
        if partida.estado == "esperando":
            ref = servicio_sala._a_datetime_utc(partida.creada_en)
            if ahora - ref > timedelta(minutes=MINUTOS_SALA_VACIA):
                repo.eliminar(partida.id)
                presencia_sala.olvidar_sala(partida.id)
        elif partida.estado == "en_curso":
            # Con reloj: si a alguien se le acabó el tiempo o se fue (nadie consulta la sala), se cierra acá.
            if partida.control_tiempo_ms and servicio_sala.revisar_reloj_y_ausencia(partida.codigo_sala).estado != "en_curso":
                continue
            if not partida.tablero.move_stack:
                ref = servicio_sala._a_datetime_utc(partida.creada_en)
                if ahora - ref > timedelta(minutes=MINUTOS_SALA_SIN_JUGADAS):
                    repo.eliminar(partida.id)
                    presencia_sala.olvidar_sala(partida.id)
            else:
                ref = servicio_sala._a_datetime_utc(partida.actualizada_en or partida.creada_en)
                if ahora - ref > timedelta(hours=HORAS_SALA_INACTIVA):
                    from backend.servicios.calibracion import MIN_JUGADAS_PARTIDA_VALIDA
                    if len(partida.tablero.move_stack) < MIN_JUGADAS_PARTIDA_VALIDA:
                        repo.eliminar(partida.id)
                    else:
                        # Pierde quien tenía el turno (no respondió); gana el otro.
                        ganador = "negras" if partida.tablero.turn == chess.WHITE else "blancas"
                        servicio_sala._cerrar(partida, ganador, "abandonada", ahora)