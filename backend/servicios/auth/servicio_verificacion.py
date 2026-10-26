"""Verificación del correo con un código de 6 dígitos.

Comprueba que quien se registra controla el correo que escribió (sin esto, cualquiera crea una cuenta con el correo de otra persona).
Solo está activa si el servidor tiene configurado el envío de correo (`SMTP_HOST` y `SMTP_REMITENTE`); sin eso, el sistema funciona
igual que antes y las cuentas nuevas quedan verificadas. Las cuentas de Google ya vienen verificadas por Google.

El código vence a los `MINUTOS_DE_VIGENCIA` minutos y solo se guarda su huella. Los intentos fallidos y los reenvíos se limitan.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.auth.servicio_invitaciones import intentos_bloqueados, olvidar_intentos, registrar_intento_fallido

logger = logging.getLogger(__name__)

MINUTOS_DE_VIGENCIA = 15
SEGUNDOS_ENTRE_ENVIOS = 60

_ultimo_envio: dict[int, float] = {}
_candado = threading.Lock()


class ErrorVerificacion(ValueError):
    """La verificación no se puede completar. `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def verificacion_activa() -> bool:
    """`True` si el servidor puede mandar correos (`SMTP_HOST` y `SMTP_REMITENTE` definidos)."""
    return bool(os.environ.get("SMTP_HOST", "").strip() and os.environ.get("SMTP_REMITENTE", "").strip())


def _huella(usuario_id: int, codigo: str) -> str:
    return hashlib.sha256(f"{usuario_id}:{codigo}".encode()).hexdigest()


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enviar_correo(destino: str, nombre: str, codigo: str, *, recuperacion: bool = False) -> None:
    """Manda el código por SMTP (con STARTTLS si el puerto no es el 465). Lanza si el servidor de correo falla."""
    host = os.environ["SMTP_HOST"].strip()
    puerto = int(os.environ.get("SMTP_PORT", "587"))
    remitente = os.environ["SMTP_REMITENTE"].strip()
    mensaje = EmailMessage()
    mensaje["Subject"] = "Recuperá tu contraseña de ChessIA" if recuperacion else "Tu código de verificación de ChessIA"
    mensaje["From"] = remitente
    mensaje["To"] = destino
    proposito = "recuperar tu contraseña" if recuperacion else "verificar el correo"
    mensaje.set_content(
        f"Hola {nombre},\n\nTu código para {proposito} es: {codigo}\n\n"
        f"Vence en {MINUTOS_DE_VIGENCIA} minutos. Si no lo pediste, ignorá este mensaje. No compartas este código.\n"
    )
    clase = smtplib.SMTP_SSL if puerto == 465 else smtplib.SMTP
    with clase(host, puerto, timeout=15) as servidor:
        if puerto != 465:
            servidor.starttls()
        usuario, clave = os.environ.get("SMTP_USUARIO", ""), os.environ.get("SMTP_CLAVE", "")
        if usuario:
            servidor.login(usuario, clave)
        servidor.send_message(mensaje)


def _enviar_en_segundo_plano(destino: str, nombre: str, codigo: str, *, recuperacion: bool = False) -> None:
    def tarea() -> None:
        try:
            if recuperacion:
                enviar_correo(destino, nombre, codigo, recuperacion=True)
            else:
                enviar_correo(destino, nombre, codigo)
        except Exception:  # noqa: BLE001 — un fallo del correo no debe romper el registro; se puede reenviar
            logger.exception("No se pudo enviar el código de verificación")

    threading.Thread(target=tarea, daemon=True).start()


def iniciar_verificacion(db: Session, usuario: UsuarioORM) -> None:
    """Deja la cuenta sin verificar, genera un código nuevo y lo manda al correo. No hace nada si el envío no está configurado."""
    if usuario.cuenta_gestionada or not verificacion_activa():
        return
    codigo = f"{secrets.randbelow(10**6):06d}"
    usuario.correo_verificado = False
    usuario.correo_verificado_en = None
    usuario.codigo_verificacion_hash = _huella(usuario.id, codigo)
    usuario.codigo_verificacion_expira = _ahora() + timedelta(minutes=MINUTOS_DE_VIGENCIA)
    db.commit()
    with _candado:
        _ultimo_envio[usuario.id] = time.monotonic()
    _enviar_en_segundo_plano(usuario.email, usuario.nombre, codigo)


def reenviar(db: Session, usuario: UsuarioORM) -> None:
    """Manda un código nuevo (el anterior deja de servir). 429 si se pidió hace menos de un minuto."""
    if usuario.correo_verificado and usuario.correo_verificado_en is not None:
        raise ErrorVerificacion("Tu correo ya está verificado")
    if not verificacion_activa():
        raise ErrorVerificacion("El envío de correos no está disponible en este servidor", 503)
    with _candado:
        pasaron = time.monotonic() - _ultimo_envio.get(usuario.id, -SEGUNDOS_ENTRE_ENVIOS)
    if pasaron < SEGUNDOS_ENTRE_ENVIOS:
        raise ErrorVerificacion(f"Esperá {int(SEGUNDOS_ENTRE_ENVIOS - pasaron) + 1} segundos antes de pedir otro código", 429)
    iniciar_verificacion(db, usuario)


def confirmar(db: Session, usuario: UsuarioORM, codigo: str) -> UsuarioORM:
    """Verifica el correo con el código recibido. 400 si es incorrecto o venció, 429 si falló demasiadas veces."""
    if usuario.correo_verificado:
        return usuario
    clave = f"verificacion:{usuario.id}"
    if intentos_bloqueados(clave):
        raise ErrorVerificacion("Demasiados intentos fallidos. Esperá unos minutos y volvé a probar.", 429)
    codigo = "".join(c for c in codigo if c.isdigit())
    vigente = usuario.codigo_verificacion_expira is not None and usuario.codigo_verificacion_expira > _ahora()
    correcto = vigente and usuario.codigo_verificacion_hash is not None and secrets.compare_digest(
        usuario.codigo_verificacion_hash, _huella(usuario.id, codigo)
    )
    if not correcto:
        registrar_intento_fallido(clave)
        raise ErrorVerificacion("El código no es correcto o ya venció. Pedí uno nuevo.")
    usuario.correo_verificado = True
    usuario.correo_verificado_en = _ahora()
    usuario.codigo_verificacion_hash = None
    usuario.codigo_verificacion_expira = None
    db.commit()
    olvidar_intentos(clave)
    return usuario
