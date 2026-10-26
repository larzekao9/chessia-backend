"""Recuperación de cuentas personales con un código de un solo uso enviado al correo."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.auth import servicio_auth, servicio_verificacion as correo
from backend.servicios.auth.servicio_invitaciones import intentos_bloqueados, registrar_intento_fallido

MENSAJE_SOLICITUD = "Si hay una cuenta personal con ese correo, te enviamos un código. Si entrás con Google, usá Continuar con Google. Si tu docente creó tu usuario, pedile una clave nueva."
_pedidos: dict[str, list[float]] = {}
_candado = threading.Lock()


def _huella(usuario: UsuarioORM, codigo: str) -> str:
    contenido = f"recuperacion:{usuario.id}:{usuario.version_credenciales}:{usuario.password_hash}:{codigo}"
    return hmac.new(servicio_auth.SECRET_KEY.encode(), contenido.encode(), hashlib.sha256).hexdigest()


def _limitar_pedidos(claves: list[str]) -> None:
    ahora = time.monotonic()
    with _candado:
        for clave in list(_pedidos):
            vigentes = [instante for instante in _pedidos[clave] if ahora - instante < 3600]
            if vigentes:
                _pedidos[clave] = vigentes
            else:
                _pedidos.pop(clave, None)
        if any(len(_pedidos.get(clave, [])) >= (20 if clave.startswith("ip:") else 3) for clave in claves):
            raise correo.ErrorVerificacion("Esperá un rato antes de pedir otro código de recuperación.", 429)
        for clave in claves:
            _pedidos.setdefault(clave, []).append(ahora)


def solicitar(db: Session, email: str, cliente: str) -> None:
    if not correo.verificacion_activa():
        raise correo.ErrorVerificacion("El servidor todavía no tiene configurado el envío de correos. Si tu docente creó tu usuario, pedile una clave nueva. Si vinculaste Google, entrá con Google.", 503)
    email = email.strip().lower()
    _limitar_pedidos([f"ip:{cliente}", f"email:{email}"])
    usuario = servicio_auth.get_user_by_email(db, email)
    if usuario is None or not usuario.activo or usuario.cuenta_gestionada or not usuario.password_hash:
        return
    codigo = f"{secrets.randbelow(10**6):06d}"
    usuario.recuperacion_password_hash = _huella(usuario, codigo)
    usuario.recuperacion_password_expira = correo._ahora() + timedelta(minutes=15)
    db.commit()
    correo._enviar_en_segundo_plano(usuario.email, usuario.nombre, codigo, recuperacion=True)


def confirmar(db: Session, email: str, codigo: str, password: str, cliente: str) -> None:
    email = email.strip().lower()
    claves = [f"recuperar:ip:{cliente}", f"recuperar:email:{email}"]
    if any(intentos_bloqueados(clave) for clave in claves):
        raise correo.ErrorVerificacion("Demasiados intentos fallidos. Esperá unos minutos y volvé a probar.", 429)
    usuario = servicio_auth.get_user_by_email(db, email)
    vigente = (usuario is not None and usuario.activo and not usuario.cuenta_gestionada and usuario.password_hash
               and usuario.recuperacion_password_expira is not None and usuario.recuperacion_password_expira > correo._ahora())
    if not (vigente and usuario.recuperacion_password_hash and secrets.compare_digest(usuario.recuperacion_password_hash, _huella(usuario, codigo))):
        for clave in claves:
            registrar_intento_fallido(clave)
        raise correo.ErrorVerificacion("El código no es correcto o venció. Pedí uno nuevo.")
    usuario.password_hash = servicio_auth.hash_password(password)
    usuario.version_credenciales += 1
    usuario.recuperacion_password_hash = None
    usuario.recuperacion_password_expira = None
    usuario.correo_verificado = True
    usuario.correo_verificado_en = correo._ahora()
    usuario.codigo_verificacion_hash = None
    usuario.codigo_verificacion_expira = None
    db.commit()
