"""Limpieza de salas `humano` inactivas (PLAN_MULTIJUGADOR.md §5.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.repositorios.repositorio_partida import RepositorioPartidas
from backend.servicios.sala import presencia_sala

MINUTOS_SALA_VACIA = 30
"""Una sala `esperando` sin segundo jugador se elimina si pasó este tiempo."""

MINUTOS_SALA_SIN_JUGADAS = 60
"""Una sala `en_curso` sin ninguna jugada se elimina si pasó este tiempo."""

HORAS_SALA_INACTIVA = 12
"""Una sala `en_curso` con jugadas pero inactiva pasa a `abandonada`."""


def limpiar_salas_inactivas(repositorio: RepositorioPartidas | None = None) -> None:
    """Barrido de salas `humano` inactivas.

    Reglas:
    - `esperando` > 30 min sin segundo jugador → eliminar.
    - `en_curso` sin jugadas > 60 min → eliminar.
    - `en_curso` con jugadas e inactiva > 12 h → `abandonada` (como partidas IA), si
      tiene al menos `MIN_JUGADAS_PARTIDA_VALIDA` jugadas; con menos se elimina.

    Contrato de una sala vencida por inactividad (`estado == "abandonada"`): la respuesta
    de la API trae `terminada: true`, `resultado: null` (nadie ganó) y
    `motivo_fin: "inactividad"`; el front la muestra como "partida cancelada por inactividad".
    Un abandono manual es distinto: `estado == "terminada"` con `resultado_por_tiempo`
    (hay ganador) y `motivo_fin: "abandono"`.

    Se llama al arrancar el backend y en `GET /sala/activa` y `GET /sala/{codigo}` (igual que
    `limpiar_partidas_inactivas` para partidas contra IA).
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
                    # Usar misma lógica que partidas IA: _cerrar_o_borrar
                    from backend.servicios.calibracion import MIN_JUGADAS_PARTIDA_VALIDA
                    if len(partida.tablero.move_stack) < MIN_JUGADAS_PARTIDA_VALIDA:
                        repo.eliminar(partida.id)
                    else:
                        partida.estado = "abandonada"
                        repo.guardar(partida)