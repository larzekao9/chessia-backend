"""Cuentas de alumnos creadas por el docente, y claves restablecidas.

Muchos alumnos (sobre todo los más chicos) no tienen un correo propio, y sus familias no van a registrarlos uno por uno. El docente carga la lista de su curso y el sistema le
crea a cada alumno una cuenta con un **usuario** corto y una **clave temporal**, ya inscripta en el curso y que cuenta contra el cupo de la institución. El alumno entra
escribiendo ese usuario (la pantalla de ingreso lo completa con `@alumnos.example.com`, un dominio reservado que nadie puede recibir: no hay correo que se pueda
usar para recuperar la cuenta). Con la clave temporal entra y puede cambiarla desde su perfil.

El mismo mecanismo resuelve el olvido de la clave: no hay recuperación por correo, así que el docente la **restablece** y le pasa una clave nueva al alumno.
"""
from __future__ import annotations

import re
import secrets
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import CursoORM, InscripcionORM, UsuarioORM
from backend.servicios.auth.servicio_auth import hash_password
from backend.servicios.curso.servicio_cursos import ErrorCurso, _inscritos
from backend.servicios.usuario import servicio_autorizacion as autorizacion

DOMINIO_DE_ALUMNOS = "alumnos.example.com"
"""Dominio reservado (RFC 2606): las cuentas creadas por un docente usan `usuario@alumnos.example.com`, que no recibe correo de nadie."""

MAXIMO_POR_VEZ = 60
_LETRAS_DE_CLAVE = "abcdefghjkmnpqrstuvwxyz"
_DIGITOS_DE_CLAVE = "23456789"


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def correo_de_usuario(usuario: str) -> str:
    """`ana.perez4k` -> `ana.perez4k@alumnos.example.com`."""
    return f"{usuario}@{DOMINIO_DE_ALUMNOS}"


def usuario_de_correo(correo: str) -> str | None:
    """`ana.perez4k@alumnos.example.com` -> `ana.perez4k`; `None` si no es una cuenta creada por un docente."""
    sufijo = "@" + DOMINIO_DE_ALUMNOS
    return correo[: -len(sufijo)] if correo.lower().endswith(sufijo) else None


def _generar_usuario(db: Session, nombre: str) -> str:
    """Un usuario corto y fácil de escribir: nombre y apellido (o inicial) más dos caracteres al azar, sin repetir con otra cuenta."""
    partes = re.findall(r"[a-z]+", _sin_acentos(nombre.lower()))
    base = ".".join(partes[:1] + ([partes[-1][:8]] if len(partes) > 1 else [])) or "alumno"
    while True:
        usuario = f"{base[:20]}{secrets.choice(_DIGITOS_DE_CLAVE)}{secrets.choice(_LETRAS_DE_CLAVE)}"
        if db.scalar(select(UsuarioORM.id).where(UsuarioORM.email == correo_de_usuario(usuario))) is None:
            return usuario


def generar_clave() -> str:
    """Una clave temporal de 8 caracteres fácil de dictar (sin letras que se confunden): cinco letras y tres números, mezclados."""
    caracteres = [secrets.choice(_LETRAS_DE_CLAVE) for _ in range(5)] + [secrets.choice(_DIGITOS_DE_CLAVE) for _ in range(3)]
    secrets.SystemRandom().shuffle(caracteres)
    return "".join(caracteres)


def crear_alumnos(db: Session, curso: CursoORM, alumnos: list[dict]) -> list[dict]:
    """Crea una cuenta por cada alumno de la lista (`{"nombre", "edad"?, "tutor_nombre"?, "tutor_telefono"?}`) y las inscribe en el curso.

    Todo o nada: si algo no sirve (un nombre, un teléfono, el cupo del curso o el tope de la institución), no se crea ninguna. Devuelve, por alumno, su `id`,
    su `nombre`, el `usuario` con el que entra y la `password` temporal (esta es la única vez que se ve).

    Raises:
        ErrorCurso: con el motivo si no se pudo (lista vacía o demasiado larga, nombre inválido, datos de la familia inválidos, curso o plan completo).
    """
    from backend.servicios.suscripcion.servicio_suscripciones import ErrorSuscripcion, verificar_cupo_de_institucion

    if not alumnos:
        raise ErrorCurso("Escribí al menos un alumno")
    if len(alumnos) > MAXIMO_POR_VEZ:
        raise ErrorCurso(f"Se pueden crear hasta {MAXIMO_POR_VEZ} cuentas por vez")
    if _inscritos(db, curso.id) + len(alumnos) > curso.cupo:
        raise ErrorCurso(f"El curso tiene cupo para {max(0, curso.cupo - _inscritos(db, curso.id))} alumnos más; querés crear {len(alumnos)}. Subí el cupo del curso.")
    creadas: list[dict] = []
    try:
        for dato in alumnos:
            nombre = " ".join((dato.get("nombre") or "").split())
            if len(nombre) < 2 or len(nombre) > 100:
                raise ErrorCurso(f"El nombre «{nombre or '(vacío)'}» no sirve: tiene que tener entre 2 y 100 caracteres")
            usuario_texto = _generar_usuario(db, nombre)
            clave = generar_clave()
            alumno = UsuarioORM(
                email=correo_de_usuario(usuario_texto),
                nombre=nombre,
                password_hash=hash_password(clave),
                rol="jugador",
                activo=True,
                cuenta_gestionada=True,
            )
            try:
                alumno.edad = autorizacion.validar_edad(dato.get("edad"))
                autorizacion.guardar_datos_del_tutor(alumno, dato.get("tutor_nombre"), dato.get("tutor_telefono"), None, email=dato.get("tutor_email"))
            except autorizacion.ErrorAutorizacion as error:
                raise ErrorCurso(f"{nombre}: {error}") from error
            db.add(alumno)
            db.flush()
            try:
                verificar_cupo_de_institucion(db, curso, alumno)
            except ErrorSuscripcion as error:
                raise ErrorCurso(str(error)) from error
            db.add(InscripcionORM(curso_id=curso.id, usuario_id=alumno.id))
            db.flush()
            creadas.append({"id": alumno.id, "nombre": nombre, "usuario": usuario_texto, "password": clave})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return creadas


def restablecer_clave(db: Session, alumno: UsuarioORM) -> str:
    """Le pone al alumno una clave temporal nueva y la devuelve (se ve una sola vez). La anterior deja de servir."""
    if not alumno.cuenta_gestionada:
        raise ErrorCurso("Solo podés restablecer claves de cuentas creadas por el docente. Las cuentas personales recuperan su contraseña desde el ingreso.")
    clave = generar_clave()
    alumno.password_hash = hash_password(clave)
    alumno.version_credenciales += 1
    alumno.recuperacion_password_hash = None
    alumno.recuperacion_password_expira = None
    db.commit()
    return clave
