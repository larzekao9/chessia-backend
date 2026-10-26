"""Lo que un docente ve de las partidas entre sus alumnos: qué salas hay abiertas ahora, con quién y con qué reloj.

Un docente ve las salas en las que juega alguno de los alumnos de sus cursos; el administrador de una institución, las de todos los de su
institución; la plataforma, todas (es la misma regla de `servicio_instituciones.ids_alumnos_visibles`). Con el código de la sala puede mirarla
en vivo, en solo lectura, como espectador. Los docentes ven el nombre completo de sus alumnos, como en el resto de sus pantallas.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.institucion.servicio_instituciones import ids_alumnos_visibles
from backend.servicios.sala import servicio_sala


def _jugador(db: Session, usuario_id: int | None) -> dict | None:
    usuario = db.get(UsuarioORM, usuario_id) if usuario_id is not None else None
    if usuario is None:
        return None
    return {"id": usuario.id, "nombre": usuario.nombre, "nivel": usuario.nivel_estimado, "rango": usuario.rango_estimado}


def partidas_en_vivo(db: Session, facilitador: UsuarioORM) -> list[dict]:
    """Las salas abiertas (esperando rival o en curso) en las que juega algún alumno que `facilitador` puede ver, las que tienen reloj primero."""
    visibles = ids_alumnos_visibles(db, facilitador)
    filas = []
    for partida in servicio_sala._repositorio.listar_salas_abiertas():
        if partida.tipo_oponente != "humano" or partida.estado not in ("esperando", "en_curso") or partida.terminada:
            continue
        asientos = (partida.usuario_blancas_id, partida.usuario_negras_id)
        if visibles is not None and not any(a in visibles for a in asientos if a is not None):
            continue
        blancas, negras = servicio_sala.restantes_ms(partida) if partida.control_tiempo_ms else (None, None)
        filas.append(
            {
                "codigo": partida.codigo_sala,
                "estado": partida.estado,
                "control_tiempo_ms": partida.control_tiempo_ms,
                "jugadas": len(partida.tablero.move_stack),
                "turno": "blancas" if partida.tablero.turn else "negras",
                "tiempo_blancas_ms": blancas,
                "tiempo_negras_ms": negras,
                "blancas": _jugador(db, partida.usuario_blancas_id),
                "negras": _jugador(db, partida.usuario_negras_id),
                "creada_en": partida.creada_en,
            }
        )
    filas.sort(key=lambda f: (f["estado"] != "en_curso", f["control_tiempo_ms"] == 0, f["creada_en"]))
    return filas
