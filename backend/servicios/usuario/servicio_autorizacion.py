"""Menores de edad y autorización: quién puede jugar con otras personas y cómo se contacta a la familia de un alumno.

Los jugadores son, en su mayoría, menores, y sus familias no van a registrarlos uno por uno: lo normal es que un colegio o una academia ya tenga la autorización
de las familias. Por eso hay tres formas de quedar autorizado, y alcanza con una:

- **Mayor de edad** (18 o más): no hace falta nada más.
- **Su curso está autorizado**: el docente (o la institución) declara en el curso que tiene la autorización de las familias (`curso.autorizacion_familias`). Los alumnos
  del curso, y los que el docente crea desde el curso, quedan cubiertos sin cargar nada.
- **Su familia lo autorizó**: el jugador (o su docente) carga el nombre y el teléfono de la madre, el padre o el tutor y declara que autorizó el uso
  (`autorizacion_tutor_en`). Es el camino de quien juega por su cuenta, fuera de una institución.

Un menor **sin autorización** (o sin edad cargada) puede usar todo lo que es individual (jugar contra Turing o Stockfish, aprender, ver su progreso), pero no lo que lo pone en
contacto con otras personas: retos, amigos, búsqueda de rival y salas entre jugadores. Se le explica qué le falta y cómo completarlo.

El contacto del tutor lo ven solo el propio jugador, su docente, el administrador de su institución y la plataforma, para avisarle si pasa algo. No hay forma de buscar a
una persona por su teléfono.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import CursoORM, InscripcionORM, UsuarioORM
from sqlalchemy import select

MAYORIA_DE_EDAD = 18
EDAD_MINIMA = 3
EDAD_MAXIMA = 120


class ErrorAutorizacion(ValueError):
    """Un dato de autorización que no sirve (el mensaje es apto para mostrarle al usuario). `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def normalizar_telefono(texto: str | None) -> str | None:
    """Deja solo el signo `+` inicial (si lo hay) y los dígitos. `None` si es vacío; error si no parece un teléfono (de 7 a 15 dígitos).

    Raises:
        ErrorAutorizacion: si hay texto pero no es un teléfono válido.
    """
    if texto is None or not texto.strip():
        return None
    limpio = re.sub(r"[^\d+]", "", texto.strip())
    digitos = limpio.lstrip("+").replace("+", "")
    if not 7 <= len(digitos) <= 15:
        raise ErrorAutorizacion("El teléfono tiene que tener entre 7 y 15 números (por ejemplo 71234567 o +59171234567).")
    return ("+" if limpio.startswith("+") else "") + digitos


def normalizar_nombre_tutor(texto: str | None) -> str | None:
    """El nombre del tutor sin espacios de más; `None` si es vacío; error si es demasiado corto."""
    if texto is None or not texto.strip():
        return None
    nombre = " ".join(texto.split())
    if len(nombre) < 3:
        raise ErrorAutorizacion("Escribí el nombre completo de la madre, el padre o el tutor.")
    return nombre[:100]


def validar_edad(edad: int | None) -> int | None:
    """La edad, o error si no es un número razonable."""
    if edad is None:
        return None
    if not EDAD_MINIMA <= edad <= EDAD_MAXIMA:
        raise ErrorAutorizacion(f"La edad tiene que estar entre {EDAD_MINIMA} y {EDAD_MAXIMA} años.")
    return edad


def guardar_datos_del_tutor(
    usuario: UsuarioORM, nombre: str | None, telefono: str | None, autoriza: bool | None, *, campos_presentes: set[str] | None = None, email: str | None = None
) -> None:
    """Carga en `usuario` el contacto de su tutor. `autoriza=True` exige nombre y teléfono y deja anotado cuándo se declaró; `autoriza=False` retira la declaración.
    Sin hacer commit.

    Raises:
        ErrorAutorizacion: si el nombre o el teléfono no sirven, o si se declara la autorización sin ellos.
    """
    nombre_limpio = normalizar_nombre_tutor(nombre)
    telefono_limpio = normalizar_telefono(telefono)
    if nombre_limpio is not None or (campos_presentes is not None and "tutor_nombre" in campos_presentes):
        usuario.tutor_nombre = nombre_limpio
    if telefono_limpio is not None or (campos_presentes is not None and "tutor_telefono" in campos_presentes):
        usuario.tutor_telefono = telefono_limpio
    if email is not None or (campos_presentes is not None and "tutor_email" in campos_presentes):
        usuario.tutor_email = (str(email).strip().lower() or None) if email is not None else None
    if autoriza is True:
        if not usuario.tutor_nombre or not usuario.tutor_telefono:
            raise ErrorAutorizacion("Para declarar la autorización hacen falta el nombre y el teléfono de la madre, el padre o el tutor.")
        usuario.autorizacion_tutor_en = datetime.now(timezone.utc).replace(tzinfo=None)
    elif autoriza is False:
        usuario.autorizacion_tutor_en = None
    if not usuario.tutor_nombre or not usuario.tutor_telefono:
        usuario.autorizacion_tutor_en = None


def autorizado_por_si_mismo(usuario: UsuarioORM) -> bool:
    """`True` si es mayor de edad o su familia lo autorizó (sin mirar su curso)."""
    if usuario.edad is not None and usuario.edad >= MAYORIA_DE_EDAD:
        return True
    return bool(usuario.tutor_nombre and usuario.tutor_telefono and usuario.autorizacion_tutor_en is not None)


def curso_del_jugador(db: Session, usuario_id: int) -> CursoORM | None:
    return db.scalar(select(CursoORM).join(InscripcionORM, InscripcionORM.curso_id == CursoORM.id).where(InscripcionORM.usuario_id == usuario_id))


def estado_de_autorizacion(db: Session, usuario: UsuarioORM) -> dict:
    """Cómo está de autorización un jugador y qué le falta.

    `estado`: `no_aplica` (no es jugador), `adulto`, `institucional` (su curso está autorizado), `familia` (su tutor lo autorizó), `falta_edad` o `falta_tutor`.
    `puede_jugar_con_otros` es verdadero en los cuatro primeros. `falta` lista lo que hay que cargar (`edad`, `tutor`).
    """
    base = {"es_menor": None if usuario.edad is None else usuario.edad < MAYORIA_DE_EDAD, "tutor_nombre": usuario.tutor_nombre, "tutor_telefono": usuario.tutor_telefono, "tutor_email": usuario.tutor_email}
    if usuario.rol != "jugador":
        return {**base, "estado": "no_aplica", "puede_jugar_con_otros": True, "falta": [], "mensaje": None}
    curso = curso_del_jugador(db, usuario.id)
    if curso is not None and curso.autorizacion_familias:
        return {**base, "estado": "institucional", "puede_jugar_con_otros": True, "falta": [], "mensaje": None}
    if usuario.edad is not None and usuario.edad >= MAYORIA_DE_EDAD:
        return {**base, "estado": "adulto", "puede_jugar_con_otros": True, "falta": [], "mensaje": None}
    if usuario.tutor_nombre and usuario.tutor_telefono and usuario.autorizacion_tutor_en is not None:
        return {**base, "estado": "familia", "puede_jugar_con_otros": True, "falta": [], "mensaje": None}
    if usuario.edad is None:
        return {
            **base,
            "estado": "falta_edad",
            "puede_jugar_con_otros": False,
            "falta": ["edad"],
            "mensaje": "Para jugar con otras personas necesitamos tu edad. Si sos menor, también los datos de tu madre, tu padre o tu tutor (o que tu docente declare que tiene la autorización de las familias).",
        }
    return {
        **base,
        "estado": "falta_tutor",
        "puede_jugar_con_otros": False,
        "falta": ["tutor"],
        "mensaje": "Sos menor de edad: para jugar con otras personas hace falta la autorización de tu madre, tu padre o tu tutor. Cargá sus datos, o pedile a tu docente que declare que tiene la autorización de las familias de tu curso.",
    }
