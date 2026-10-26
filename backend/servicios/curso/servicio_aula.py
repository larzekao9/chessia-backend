"""Seguimiento a distancia de un curso: los avisos del docente y las tareas que el sistema mide solo.

- **Avisos.** Un mensaje corto del docente a sus alumnos (la consigna de la semana, el enlace de una clase en vivo, una felicitación). Es en un solo sentido: los
  alumnos lo leen y no responden, así no se abre un canal de mensajes con menores. Se ven los más nuevos.
- **Tareas.** Una meta con fecha límite que se mide con lo que los alumnos ya juegan, sin que nadie cargue nada: `partidas_ia` (partidas terminadas contra Turing o
  Stockfish), `partidas_amigos` (partidas terminadas contra compañeros o amigos) o `precision` (una partida medida con esa precisión o más). El docente ve quién cumplió y
  quién no; cada alumno ve su avance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import AvisoCursoORM, CalibracionORM, InscripcionORM, PartidaORM, TareaCursoORM, UsuarioORM

LARGO_DEL_AVISO = (3, 500)
AVISOS_GUARDADOS = 50
LARGO_DEL_TITULO = (3, 80)
DIAS_DE_LA_TAREA = (1, 60)
TIPOS_DE_TAREA = {
    "partidas_ia": (1, 50),
    "partidas_amigos": (1, 50),
    "precision": (10, 100),
}
"""Tipo de tarea -> (mínimo, máximo) de la meta."""


class ErrorAula(ValueError):
    """Un pedido de aula que no se puede cumplir (el mensaje es apto para mostrarle al docente). `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- avisos ---------------------------------------------------------------------------------------------------------------------------


def publicar_aviso(db: Session, curso_id: int, autor_id: int, texto: str, enlace: str | None = None) -> AvisoCursoORM:
    """Publica un aviso para el curso. Se guardan los últimos `AVISOS_GUARDADOS`.

    Raises:
        ErrorAula: si el texto no tiene entre 3 y 500 caracteres o el enlace no es una dirección `http(s)://`.
    """
    texto = " ".join((texto or "").split())
    if not LARGO_DEL_AVISO[0] <= len(texto) <= LARGO_DEL_AVISO[1]:
        raise ErrorAula(f"El aviso tiene que tener entre {LARGO_DEL_AVISO[0]} y {LARGO_DEL_AVISO[1]} caracteres")
    enlace = (enlace or "").strip() or None
    if enlace is not None and (not enlace.lower().startswith(("http://", "https://")) or len(enlace) > 300 or " " in enlace):
        raise ErrorAula("El enlace tiene que empezar con http:// o https:// (por ejemplo el de la clase en vivo)")
    aviso = AvisoCursoORM(curso_id=curso_id, autor_id=autor_id, texto=texto, enlace=enlace)
    db.add(aviso)
    db.commit()
    viejos = list(db.scalars(select(AvisoCursoORM).where(AvisoCursoORM.curso_id == curso_id).order_by(AvisoCursoORM.id.desc()).offset(AVISOS_GUARDADOS)))
    for viejo in viejos:
        db.delete(viejo)
    if viejos:
        db.commit()
    db.refresh(aviso)
    return aviso


def avisos_del_curso(db: Session, curso_id: int, limite: int = 10) -> list[dict]:
    """Los últimos avisos del curso, el más nuevo primero, con el nombre del docente que los escribió."""
    filas = db.scalars(select(AvisoCursoORM).where(AvisoCursoORM.curso_id == curso_id).order_by(AvisoCursoORM.id.desc()).limit(limite))
    resultado = []
    for aviso in filas:
        autor = db.get(UsuarioORM, aviso.autor_id)
        resultado.append({"id": aviso.id, "texto": aviso.texto, "enlace": aviso.enlace, "creado_en": aviso.creado_en, "autor": autor.nombre if autor else None})
    return resultado


def borrar_aviso(db: Session, curso_id: int, aviso_id: int) -> bool:
    """Borra un aviso del curso. `False` si no existe en él."""
    aviso = db.get(AvisoCursoORM, aviso_id)
    if aviso is None or aviso.curso_id != curso_id:
        return False
    db.delete(aviso)
    db.commit()
    return True


# --- tareas ---------------------------------------------------------------------------------------------------------------------------


def crear_tarea(db: Session, curso_id: int, autor_id: int, titulo: str, tipo: str, meta: int, dias: int) -> TareaCursoORM:
    """Pone una tarea al curso: cuenta lo que se juegue desde ahora hasta dentro de `dias` días.

    Raises:
        ErrorAula: si el título, el tipo, la meta o los días no sirven.
    """
    titulo = " ".join((titulo or "").split())
    if not LARGO_DEL_TITULO[0] <= len(titulo) <= LARGO_DEL_TITULO[1]:
        raise ErrorAula(f"El título tiene que tener entre {LARGO_DEL_TITULO[0]} y {LARGO_DEL_TITULO[1]} caracteres")
    if tipo not in TIPOS_DE_TAREA:
        raise ErrorAula(f"Elegí un tipo de tarea: {', '.join(TIPOS_DE_TAREA)}")
    minimo, maximo = TIPOS_DE_TAREA[tipo]
    if not minimo <= meta <= maximo:
        raise ErrorAula(f"Para este tipo de tarea la meta tiene que estar entre {minimo} y {maximo}")
    if not DIAS_DE_LA_TAREA[0] <= dias <= DIAS_DE_LA_TAREA[1]:
        raise ErrorAula(f"La tarea dura entre {DIAS_DE_LA_TAREA[0]} y {DIAS_DE_LA_TAREA[1]} días")
    ahora = _ahora()
    tarea = TareaCursoORM(curso_id=curso_id, autor_id=autor_id, titulo=titulo, tipo=tipo, meta=meta, desde=ahora, hasta=ahora + timedelta(days=dias))
    db.add(tarea)
    db.commit()
    db.refresh(tarea)
    return tarea


def borrar_tarea(db: Session, curso_id: int, tarea_id: int) -> bool:
    tarea = db.get(TareaCursoORM, tarea_id)
    if tarea is None or tarea.curso_id != curso_id:
        return False
    db.delete(tarea)
    db.commit()
    return True


def progreso(db: Session, tarea: TareaCursoORM, ids: list[int]) -> dict[int, dict]:
    """`usuario_id -> {"valor": ..., "cumplida": ...}` de la tarea, medido con lo que cada uno jugó entre `desde` y `hasta`.

    `valor` es cuántas partidas terminó (`partidas_ia`, `partidas_amigos`) o la mejor precisión de una partida medida (`precision`).
    """
    valores = {i: 0 for i in ids}
    if not ids:
        return {}
    ventana = (tarea.desde, tarea.hasta)
    if tarea.tipo == "partidas_ia":
        filas = db.execute(
            select(PartidaORM.usuario_id).where(
                PartidaORM.usuario_id.in_(ids),
                PartidaORM.tipo_oponente.in_(("motor", "modelo")),
                PartidaORM.resultado.is_not(None),
                PartidaORM.es_demostracion.is_(False),
                PartidaORM.fecha >= ventana[0],
                PartidaORM.fecha <= ventana[1],
            )
        ).all()
        for (usuario_id,) in filas:
            valores[usuario_id] += 1
    elif tarea.tipo == "partidas_amigos":
        filas = db.execute(
            select(PartidaORM.usuario_blancas_id, PartidaORM.usuario_negras_id).where(
                PartidaORM.tipo_oponente == "humano",
                PartidaORM.resultado.is_not(None),
                PartidaORM.fecha >= ventana[0],
                PartidaORM.fecha <= ventana[1],
                or_(PartidaORM.usuario_blancas_id.in_(ids), PartidaORM.usuario_negras_id.in_(ids)),
            )
        ).all()
        for blancas, negras in filas:
            for usuario_id in (blancas, negras):
                if usuario_id in valores:
                    valores[usuario_id] += 1
    else:  # precision
        for usuario_id, mejor in db.execute(
            select(CalibracionORM.usuario_id, CalibracionORM.precision_global).where(
                CalibracionORM.usuario_id.in_(ids), CalibracionORM.creado_en >= ventana[0], CalibracionORM.creado_en <= ventana[1]
            )
        ).all():
            valores[usuario_id] = max(valores[usuario_id], int(mejor))
    return {i: {"valor": v, "cumplida": v >= tarea.meta} for i, v in valores.items()}


def _datos_de_tarea(tarea: TareaCursoORM) -> dict:
    ahora = _ahora()
    return {
        "id": tarea.id,
        "titulo": tarea.titulo,
        "tipo": tarea.tipo,
        "meta": tarea.meta,
        "desde": tarea.desde,
        "hasta": tarea.hasta,
        "vencida": ahora > tarea.hasta,
        "dias_restantes": max(0, (tarea.hasta - ahora).days + (1 if ahora <= tarea.hasta else 0)),
    }


def tareas_del_curso(db: Session, curso_id: int, limite: int = 10) -> list[TareaCursoORM]:
    return list(db.scalars(select(TareaCursoORM).where(TareaCursoORM.curso_id == curso_id).order_by(TareaCursoORM.id.desc()).limit(limite)))


def tareas_para_el_alumno(db: Session, curso_id: int, usuario_id: int) -> list[dict]:
    """Las tareas del curso con el avance de este alumno."""
    resultado = []
    for tarea in tareas_del_curso(db, curso_id):
        avance = progreso(db, tarea, [usuario_id]).get(usuario_id, {"valor": 0, "cumplida": False})
        resultado.append({**_datos_de_tarea(tarea), **avance})
    return resultado


def tareas_con_resultados(db: Session, curso_id: int) -> list[dict]:
    """Las tareas del curso con el avance de cada alumno inscrito (para el docente): quién cumplió y quién no."""
    alumnos = list(
        db.scalars(
            select(UsuarioORM)
            .join(InscripcionORM, InscripcionORM.usuario_id == UsuarioORM.id)
            .where(InscripcionORM.curso_id == curso_id, UsuarioORM.activo.is_(True))
            .order_by(UsuarioORM.nombre)
        )
    )
    ids = [a.id for a in alumnos]
    salida = []
    for tarea in tareas_del_curso(db, curso_id):
        avance = progreso(db, tarea, ids)
        filas = [{"id": a.id, "nombre": a.nombre, **avance[a.id]} for a in alumnos]
        salida.append({**_datos_de_tarea(tarea), "alumnos": filas, "cumplieron": sum(1 for f in filas if f["cumplida"]), "total": len(filas)})
    return salida
