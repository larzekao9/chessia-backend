"""Editar y eliminar usuarios desde Gestión de Usuarios (solo facilitadores).

Dos reglas de seguridad valen para las dos operaciones: un facilitador no puede quitarse a sí mismo el
acceso (eliminarse, desactivarse o dejar de ser facilitador) y nunca puede quedar el sistema sin al menos un
facilitador activo. Eliminar es definitivo: se borran la cuenta y todo lo que depende de ella.
"""
from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import (
    CalibracionORM,
    CodigoInvitacionORM,
    ExportacionDatasetORM,
    MensajeTutorORM,
    UsuarioORM,
)
from backend.servicios.auth import actualizar_perfil, get_user_by_email
from backend.servicios.curso.servicio_cursos import retirar_usuario_de_cursos
from backend.servicios.institucion.servicio_instituciones import retirar_administrador
from backend.servicios.medallas.servicio_creacion import retirar_usuario_de_medallas
from backend.servicios.usuario.servicio_avatar import eliminar_avatar

RANGOS_VALIDOS = ("Principiante", "Intermedio", "Avanzado")


class ErrorGestionUsuarios(ValueError):
    """La operación sobre el usuario no está permitida. `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def _facilitadores_activos(db: Session) -> int:
    return db.scalar(
        select(func.count()).select_from(UsuarioORM).where(UsuarioORM.rol == "facilitador", UsuarioORM.activo.is_(True))
    ) or 0


def _es_facilitador_activo(usuario: UsuarioORM) -> bool:
    return usuario.rol == "facilitador" and usuario.activo


def editar_usuario(db: Session, objetivo: UsuarioORM, solicitante_id: int, campos: dict) -> UsuarioORM:
    """Edita los datos de `objetivo` (edición parcial: solo se tocan las claves que vienen en `campos`).

    Campos: `nombre`, `email`, `rol`, `activo`, `edad`, `descripcion`, `nivel_estimado` y `rango_estimado`.

    Raises:
        ErrorGestionUsuarios: si el email ya lo usa otra cuenta (409), si el facilitador intenta quitarse a sí
            mismo el acceso o si el cambio dejaría al sistema sin facilitadores activos (400).
    """
    cambios = dict(campos)

    if "email" in cambios:
        email = cambios["email"].lower().strip()
        existente = get_user_by_email(db, email)
        if existente is not None and existente.id != objetivo.id:
            raise ErrorGestionUsuarios("Ya existe otra cuenta con ese correo", 409)
        cambios["email"] = email

    rango = cambios.get("rango_estimado")
    if rango is not None and rango not in RANGOS_VALIDOS:
        raise ErrorGestionUsuarios(f"Rango inválido: {rango!r} (usá {', '.join(RANGOS_VALIDOS)})")

    nuevo_rol = cambios.get("rol", objetivo.rol)
    nuevo_activo = cambios.get("activo", objetivo.activo)
    pierde_acceso_de_facilitador = _es_facilitador_activo(objetivo) and not (nuevo_rol == "facilitador" and nuevo_activo)
    if pierde_acceso_de_facilitador:
        if objetivo.id == solicitante_id:
            raise ErrorGestionUsuarios("No podés quitarte a vos mismo el acceso de facilitador ni desactivar tu cuenta")
        if _facilitadores_activos(db) <= 1:
            raise ErrorGestionUsuarios("Tiene que quedar al menos un facilitador activo")

    return actualizar_perfil(db, objetivo, cambios)


def validar_eliminacion(db: Session, objetivo: UsuarioORM, solicitante_id: int) -> None:
    """Comprueba que `objetivo` se puede eliminar, sin tocar nada.

    Raises:
        ErrorGestionUsuarios: si es el propio solicitante o el último facilitador activo.
    """
    if objetivo.id == solicitante_id:
        raise ErrorGestionUsuarios("No podés eliminar tu propia cuenta")
    if _es_facilitador_activo(objetivo) and _facilitadores_activos(db) <= 1:
        raise ErrorGestionUsuarios("No se puede eliminar al único facilitador activo")


def eliminar_usuario(db: Session, objetivo: UsuarioORM, solicitante_id: int) -> dict:
    """Elimina definitivamente a `objetivo` y lo que depende de su cuenta.

    Borra sus calibraciones, sus mensajes con el tutor, su registro de descargas del dataset, su foto y la
    cuenta. Las partidas las borra quien llama (ver `servicio_partida.eliminar_partidas_de_usuario`) ANTES de
    esta función, porque viven detrás de su propio repositorio. Devuelve cuántas filas se borraron de cada cosa.

    Raises:
        ErrorGestionUsuarios: si es el propio solicitante o el último facilitador activo.
    """
    validar_eliminacion(db, objetivo, solicitante_id)

    borradas = {
        "calibraciones": db.execute(delete(CalibracionORM).where(CalibracionORM.usuario_id == objetivo.id)).rowcount,
        "mensajes_tutor": db.execute(delete(MensajeTutorORM).where(MensajeTutorORM.usuario_id == objetivo.id)).rowcount,
        "exportaciones": db.execute(
            delete(ExportacionDatasetORM).where(ExportacionDatasetORM.usuario_id == objetivo.id)
        ).rowcount,
    }
    # Los códigos de invitación que generó se borran; los que usó quedan (sin nombre) como registro.
    db.execute(delete(CodigoInvitacionORM).where(CodigoInvitacionORM.creado_por == objetivo.id))
    db.execute(update(CodigoInvitacionORM).where(CodigoInvitacionORM.usado_por == objetivo.id).values(usado_por=None))
    # Si es jugador sale de su curso; si es facilitador se borran sus cursos (sus estudiantes quedan sin curso).
    retirar_usuario_de_cursos(db, objetivo.id)
    retirar_administrador(db, objetivo.id)  # si administraba una institución, esta queda sin administrador
    retirar_usuario_de_medallas(db, objetivo.id)  # sus medallas ganadas y, si es facilitador, las que creó
    eliminar_avatar(objetivo.id)
    db.delete(objetivo)
    db.commit()
    return borradas
