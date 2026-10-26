"""Instituciones clientes de la plataforma: quiénes son, quién las administra y qué docentes tienen.

Una institución (una asociación, un colegio, un organizador de torneos) es la que va a pagar la suscripción. Agrupa a sus
docentes (`usuario.institucion_id`) y tiene un administrador (`institucion.administrador_id`). Los alumnos no guardan
institución: se deduce de la cadena alumno → curso → docente → institución, así hay una sola fuente de la verdad.

Los perfiles de la plataforma son cuatro:

- **Plataforma**: los facilitadores principales (su correo está en `CORREOS_FACILITADORES`). No pertenecen a ninguna institución.
- **Administrador de institución**: el usuario de `institucion.administrador_id`.
- **Docente**: un facilitador con `institucion_id`.
- **Jugador (alumno)**.

Un usuario pertenece a una sola institución a la vez (asignarlo a otra lo mueve) y administra, a lo sumo, una.
"""
from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, aliased

from backend.modelos.tablas_orm import TIPOS_INSTITUCION, CodigoInvitacionORM, CursoORM, InscripcionORM, InstitucionORM, UsuarioORM
from backend.servicios.curso.servicio_cursos import es_principal

LARGO_NOMBRE = (3, 80)


class ErrorInstitucion(ValueError):
    """Un pedido sobre instituciones que no se puede cumplir (el mensaje es apto para mostrarle al usuario)."""


class NombreEnUso(ErrorInstitucion):
    """Ya hay otra institución con ese nombre (se responde 409)."""


def _nombre_valido(nombre: str) -> str:
    nombre = " ".join((nombre or "").split())
    if not LARGO_NOMBRE[0] <= len(nombre) <= LARGO_NOMBRE[1]:
        raise ErrorInstitucion(f"El nombre tiene que tener entre {LARGO_NOMBRE[0]} y {LARGO_NOMBRE[1]} caracteres")
    return nombre


def _tipo_valido(tipo: str) -> str:
    if tipo not in TIPOS_INSTITUCION:
        raise ErrorInstitucion(f"Tipo inválido: usá uno de {', '.join(TIPOS_INSTITUCION)}")
    return tipo


def _nombre_libre(db: Session, nombre: str, excepto_id: int | None = None) -> None:
    consulta = select(InstitucionORM.id).where(func.lower(InstitucionORM.nombre) == nombre.lower())
    if excepto_id is not None:
        consulta = consulta.where(InstitucionORM.id != excepto_id)
    if db.scalar(consulta) is not None:
        raise NombreEnUso("Ya hay una institución con ese nombre")


def obtener(db: Session, institucion_id: int) -> InstitucionORM | None:
    return db.get(InstitucionORM, institucion_id)


def listar(db: Session) -> list[InstitucionORM]:
    return list(db.scalars(select(InstitucionORM).order_by(func.lower(InstitucionORM.nombre))))


def _facilitador_activo(db: Session, usuario_id: int) -> UsuarioORM:
    usuario = db.get(UsuarioORM, usuario_id)
    if usuario is None:
        raise ErrorInstitucion("No existe ese usuario")
    if usuario.rol != "facilitador" or not usuario.activo:
        raise ErrorInstitucion("Solo un facilitador activo puede ser docente o administrador de una institución")
    return usuario


def es_administrador(institucion: InstitucionORM | None, usuario_id: int) -> bool:
    return institucion is not None and institucion.administrador_id == usuario_id


def institucion_de(db: Session, usuario: UsuarioORM) -> InstitucionORM | None:
    """La institución a la que pertenece un facilitador (o `None`)."""
    return db.get(InstitucionORM, usuario.institucion_id) if usuario.institucion_id is not None else None


def administra_alguna(db: Session, usuario_id: int, incluir_prueba: bool = True) -> bool:
    """`True` si el usuario es el administrador de alguna institución. Con `incluir_prueba=False` no cuentan las instituciones de prueba."""
    consulta = select(InstitucionORM.id).where(InstitucionORM.administrador_id == usuario_id)
    if not incluir_prueba:
        consulta = consulta.where(InstitucionORM.es_prueba.is_(False))
    return db.scalar(consulta.limit(1)) is not None


def crear(db: Session, nombre: str, tipo: str, administrador_id: int | None = None) -> InstitucionORM:
    """Crea una institución. Con `administrador_id`, ese facilitador queda como su administrador (y como su primer docente)."""
    nombre, tipo = _nombre_valido(nombre), _tipo_valido(tipo)
    _nombre_libre(db, nombre)
    if administrador_id is not None:
        _facilitador_activo(db, administrador_id)
    institucion = InstitucionORM(nombre=nombre, tipo=tipo)
    db.add(institucion)
    db.flush()
    if administrador_id is not None:
        designar_administrador(db, institucion, administrador_id)
    db.commit()
    db.refresh(institucion)
    return institucion


def editar(db: Session, institucion: InstitucionORM, nombre: str | None = None, tipo: str | None = None) -> InstitucionORM:
    if nombre is not None:
        nombre = _nombre_valido(nombre)
        _nombre_libre(db, nombre, excepto_id=institucion.id)
        institucion.nombre = nombre
    if tipo is not None:
        institucion.tipo = _tipo_valido(tipo)
    db.commit()
    db.refresh(institucion)
    return institucion


def fijar_dominio_correo(db: Session, institucion: InstitucionORM, dominio: str | None) -> InstitucionORM:
    """Define (o quita, con vacío) el dominio del correo institucional: solo entran a los cursos de sus docentes los jugadores con un correo de ese dominio.

    Raises:
        ErrorInstitucion: si no parece un dominio (por ejemplo `colegio.edu.bo`).
    """
    texto = (dominio or "").strip().lower().lstrip("@")
    if texto and not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", texto):
        raise ErrorInstitucion("El dominio tiene que ser como colegio.edu.bo (sin @ ni espacios)")
    institucion.dominio_correo = texto or None
    db.commit()
    db.refresh(institucion)
    return institucion


def convertir_en_cliente(db: Session, institucion: InstitucionORM, nombre: str | None = None, tipo: str | None = None) -> InstitucionORM:
    """La institución de prueba pasa a ser cliente: deja de ser de prueba (y, con ello, sus códigos pendientes dejan de crear un jugador
    de prueba) y, si se indica, cambia de nombre y de tipo. Todo lo que armó la persona que la evaluaba se conserva."""
    if not institucion.es_prueba:
        raise ErrorInstitucion("Esa institución ya es cliente")
    editar(db, institucion, nombre, tipo)
    institucion.es_prueba = False
    db.execute(update(CodigoInvitacionORM).where(CodigoInvitacionORM.institucion_id == institucion.id).values(es_evaluacion=False))
    db.commit()
    db.refresh(institucion)
    return institucion


def asignar_docente(db: Session, institucion: InstitucionORM, usuario_id: int) -> UsuarioORM:
    """Pasa a un facilitador a esta institución. Si era el administrador de otra, esa queda sin administrador."""
    usuario = _facilitador_activo(db, usuario_id)
    if usuario.institucion_id is not None and usuario.institucion_id != institucion.id:
        db.execute(
            update(InstitucionORM)
            .where(InstitucionORM.id == usuario.institucion_id, InstitucionORM.administrador_id == usuario.id)
            .values(administrador_id=None)
        )
    usuario.institucion_id = institucion.id
    db.commit()
    db.refresh(usuario)
    return usuario


def quitar_docente(db: Session, institucion: InstitucionORM, usuario_id: int) -> None:
    """Saca a un docente de la institución (si era su administrador, la institución queda sin administrador)."""
    usuario = db.get(UsuarioORM, usuario_id)
    if usuario is None or usuario.institucion_id != institucion.id:
        raise ErrorInstitucion("Ese usuario no pertenece a esta institución")
    usuario.institucion_id = None
    if institucion.administrador_id == usuario.id:
        institucion.administrador_id = None
    db.commit()


def designar_administrador(db: Session, institucion: InstitucionORM, usuario_id: int | None) -> InstitucionORM:
    """Define quién administra la institución (se lo agrega a ella si todavía no era parte). `None` la deja sin administrador."""
    if usuario_id is None:
        institucion.administrador_id = None
        db.commit()
        return institucion
    asignar_docente(db, institucion, usuario_id)
    institucion.administrador_id = usuario_id
    db.commit()
    db.refresh(institucion)
    return institucion


def docentes_de(db: Session, institucion: InstitucionORM) -> list[UsuarioORM]:
    return list(
        db.scalars(
            select(UsuarioORM)
            .where(UsuarioORM.institucion_id == institucion.id, UsuarioORM.rol == "facilitador")
            .order_by(func.lower(UsuarioORM.nombre))
        )
    )


def contar_cursos(db: Session, institucion_id: int) -> int:
    """Cursos de los docentes de la institución."""
    return db.scalar(
        select(func.count())
        .select_from(CursoORM)
        .join(UsuarioORM, UsuarioORM.id == CursoORM.facilitador_id)
        .where(UsuarioORM.institucion_id == institucion_id)
    ) or 0


def contar_alumnos(db: Session, institucion_id: int) -> int:
    """Alumnos inscriptos en los cursos de los docentes de la institución (la base del tope de cada plan)."""
    return db.scalar(
        select(func.count())
        .select_from(InscripcionORM)
        .join(CursoORM, CursoORM.id == InscripcionORM.curso_id)
        .join(UsuarioORM, UsuarioORM.id == CursoORM.facilitador_id)
        .where(UsuarioORM.institucion_id == institucion_id)
    ) or 0


def retirar_administrador(db: Session, usuario_id: int) -> None:
    """Deja sin administrador a las instituciones que administraba `usuario_id` (al borrar su cuenta). No hace commit."""
    db.execute(update(InstitucionORM).where(InstitucionORM.administrador_id == usuario_id).values(administrador_id=None))


def ids_alumnos_visibles(db: Session, usuario: UsuarioORM) -> set[int] | None:
    """Jugadores que un facilitador puede ver: `None` si son todos (la plataforma); si no, los inscriptos en sus cursos y,
    si además administra una institución, en los cursos de todos los docentes de esa institución."""
    if es_principal(usuario):
        return None
    docentes = {usuario.id}
    institucion = institucion_de(db, usuario)
    if es_administrador(institucion, usuario.id):
        docentes.update(d.id for d in docentes_de(db, institucion))
    inscriptos = db.scalars(
        select(InscripcionORM.usuario_id)
        .join(CursoORM, CursoORM.id == InscripcionORM.curso_id)
        .where(CursoORM.facilitador_id.in_(docentes))
    )
    return set(inscriptos)


def alumnos_de(db: Session, institucion: InstitucionORM) -> list[dict]:
    """Los jugadores inscriptos en los cursos de los docentes de la institución, con su curso y su docente."""
    docente = aliased(UsuarioORM)
    filas = db.execute(
        select(UsuarioORM, CursoORM.nombre, docente.nombre)
        .join(InscripcionORM, InscripcionORM.usuario_id == UsuarioORM.id)
        .join(CursoORM, CursoORM.id == InscripcionORM.curso_id)
        .join(docente, docente.id == CursoORM.facilitador_id)
        .where(docente.institucion_id == institucion.id)
        .order_by(func.lower(docente.nombre), func.lower(CursoORM.nombre), func.lower(UsuarioORM.nombre))
    )
    return [
        {
            "id": alumno.id,
            "nombre": alumno.nombre,
            "email": alumno.email,
            "rango_estimado": alumno.rango_estimado,
            "activo": bool(alumno.activo),
            "curso": curso,
            "docente": nombre_docente,
        }
        for alumno, curso, nombre_docente in filas
    ]


def puede_supervisar(db: Session, facilitador: UsuarioORM, dueno_id: int | None) -> bool:
    """`True` si `facilitador` puede mirar y tocar la partida de `dueno_id`: la plataforma, siempre; un docente o administrador,
    la suya y las de los alumnos de sus cursos (o, si administra una institución, de los de todos sus docentes)."""
    if dueno_id is not None and dueno_id == facilitador.id:
        return True
    visibles = ids_alumnos_visibles(db, facilitador)
    return visibles is None or (dueno_id is not None and dueno_id in visibles)


def ve_demostracion(db: Session, visor: UsuarioORM, dueno_id: int | None) -> bool:
    """`True` si `visor` puede ver la demostración en vivo que transmite `dueno_id`.

    La del dueño, siempre; la de la plataforma, todos; la de un docente, sus alumnos (y quienes administran su institución o
    trabajan en ella). Sin esto, un alumno de una institución vería las transmisiones de otra."""
    dueno = db.get(UsuarioORM, dueno_id) if dueno_id is not None else None
    if dueno is None:
        return False
    if visor.id == dueno.id or es_principal(visor):
        return True
    visibles = ids_alumnos_visibles(db, dueno)
    if visibles is None or visor.id in visibles:
        return True
    return visor.rol == "facilitador" and dueno.institucion_id is not None and visor.institucion_id == dueno.institucion_id
