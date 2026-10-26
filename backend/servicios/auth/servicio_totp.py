"""Verificación en dos pasos con una app de códigos (TOTP, RFC 6238): Google Authenticator, Authy, Microsoft Authenticator, etc.

Después de la contraseña, la persona escribe el código de 6 dígitos que muestra la app, que cambia cada 30 segundos. Así, quien se
roba una contraseña no alcanza para entrar. Es para los facilitadores (docentes, administradores de institución y la plataforma).

- El secreto de cada cuenta se guarda **cifrado** (Fernet, con una clave derivada de `JWT_SECRET_KEY`): sin esa clave fija en el
  servidor no se puede activar, porque con una clave que cambia en cada arranque se perderían todos los secretos.
- Un mismo código no sirve dos veces (se recuerda el último paso de tiempo aceptado).
- Para quien pierde el celular hay 10 códigos de respaldo de un solo uso; solo se guardan sus huellas.
- Si el servidor define `DOBLE_FACTOR_OBLIGATORIO=1`, la plataforma y los administradores de institución no pueden usar sus funciones
  sin tenerlo activado (ver `ruta_auth.get_current_principal`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.auth import servicio_auth

PASO_SEGUNDOS = 30
DIGITOS = 6
DESFASE_PERMITIDO = 1  # se aceptan el paso anterior y el siguiente, por si el reloj del celular se atrasa o adelanta unos segundos
CANTIDAD_CODIGOS_RESPALDO = 10
EMISOR = "ChessIA"
MINUTOS_DEL_TOKEN_DE_PASO_2 = 5


class ErrorDobleFactor(ValueError):
    """La operación de doble factor no se puede cumplir. `estado` es el código HTTP que corresponde."""

    def __init__(self, mensaje: str, estado: int = 400) -> None:
        super().__init__(mensaje)
        self.estado = estado


def _ahora() -> float:
    """El reloj, separado para que los tests puedan adelantarlo."""
    return time.time()


# --- el código de 6 dígitos --------------------------------------------------------------------------------------------------


def generar_secreto() -> str:
    """Un secreto nuevo de 160 bits, en base32 (como lo piden las apps de códigos)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def codigo_en(secreto: str, paso: int) -> str:
    """El código de 6 dígitos que corresponde a un paso de tiempo (HOTP, RFC 4226)."""
    clave = base64.b32decode(secreto + "=" * (-len(secreto) % 8), casefold=True)
    resumen = hmac.new(clave, struct.pack(">Q", paso), hashlib.sha1).digest()
    desde = resumen[-1] & 0x0F
    numero = struct.unpack(">I", resumen[desde : desde + 4])[0] & 0x7FFFFFFF
    return str(numero % 10**DIGITOS).zfill(DIGITOS)


def paso_actual() -> int:
    return int(_ahora() // PASO_SEGUNDOS)


def verificar_codigo(secreto: str, codigo: str, ultimo_paso: int | None = None) -> int | None:
    """Devuelve el paso de tiempo al que corresponde `codigo` (para recordarlo), o `None` si no vale.

    No vale si no coincide con ninguno de los pasos cercanos o si ese paso (o uno anterior) ya se usó: un código sirve una sola vez."""
    codigo = "".join(c for c in codigo if c.isdigit())
    if len(codigo) != DIGITOS:
        return None
    actual = paso_actual()
    for paso in range(actual - DESFASE_PERMITIDO, actual + DESFASE_PERMITIDO + 1):
        if hmac.compare_digest(codigo_en(secreto, paso), codigo) and (ultimo_paso is None or paso > ultimo_paso):
            return paso
    return None


def uri_de_aprovisionamiento(secreto: str, email: str) -> str:
    """El enlace `otpauth://` que lee la app al escanear el QR (o al tocarlo en el celular)."""
    etiqueta = quote(f"{EMISOR}:{email}")
    parametros = urlencode({"secret": secreto, "issuer": EMISOR, "algorithm": "SHA1", "digits": DIGITOS, "period": PASO_SEGUNDOS})
    return f"otpauth://totp/{etiqueta}?{parametros}"


# --- guardar el secreto cifrado ----------------------------------------------------------------------------------------------


def clave_estable() -> bool:
    """`True` si el servidor tiene `JWT_SECRET_KEY` fija (necesaria para poder descifrar los secretos después de un reinicio)."""
    return bool(os.environ.get("JWT_SECRET_KEY", "").strip())


def _fernet() -> Fernet:
    clave = base64.urlsafe_b64encode(hashlib.sha256(b"doble-factor:" + servicio_auth.SECRET_KEY.encode()).digest())
    return Fernet(clave)


def cifrar(secreto: str) -> str:
    return _fernet().encrypt(secreto.encode()).decode()


def descifrar(valor: str | None) -> str | None:
    """El secreto en claro, o `None` si no hay o no se puede leer (por ejemplo, si cambió la clave del servidor)."""
    if not valor:
        return None
    try:
        return _fernet().decrypt(valor.encode()).decode()
    except InvalidToken:
        return None


# --- códigos de respaldo -----------------------------------------------------------------------------------------------------


def _huella_respaldo(codigo: str) -> str:
    limpio = "".join(c for c in codigo.lower() if c.isalnum())
    return hashlib.sha256(f"respaldo:{limpio}".encode()).hexdigest()


def generar_codigos_respaldo() -> tuple[list[str], str]:
    """Los códigos en claro (para mostrarlos una sola vez) y el JSON con sus huellas (lo que se guarda)."""
    codigos = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(CANTIDAD_CODIGOS_RESPALDO)]
    return codigos, json.dumps([_huella_respaldo(c) for c in codigos])


def _consumir_respaldo(usuario: UsuarioORM, codigo: str) -> bool:
    huellas = json.loads(usuario.totp_codigos_respaldo or "[]")
    huella = _huella_respaldo(codigo)
    for guardada in huellas:
        if hmac.compare_digest(guardada, huella):
            huellas.remove(guardada)
            usuario.totp_codigos_respaldo = json.dumps(huellas)
            return True
    return False


# --- operaciones sobre una cuenta --------------------------------------------------------------------------------------------


def preparar(db: Session, usuario: UsuarioORM) -> dict:
    """Genera un secreto nuevo (todavía sin activar) y devuelve lo que muestra la pantalla: el secreto escrito y el enlace del QR.

    Si ya estaba activo, hay que desactivarlo antes: así no se pisa el secreto de una cuenta protegida."""
    if usuario.totp_activo:
        raise ErrorDobleFactor("La verificación en dos pasos ya está activada")
    if not clave_estable():
        raise ErrorDobleFactor("El servidor necesita una JWT_SECRET_KEY fija para guardar el secreto de forma segura", 503)
    secreto = generar_secreto()
    usuario.totp_secreto = cifrar(secreto)
    usuario.totp_ultimo_paso = None
    db.commit()
    return {"secreto": secreto, "uri": uri_de_aprovisionamiento(secreto, usuario.email)}


def activar(db: Session, usuario: UsuarioORM, codigo: str) -> list[str]:
    """Activa el doble factor si `codigo` es el que muestra la app. Devuelve los códigos de respaldo (se ven una sola vez)."""
    if usuario.totp_activo:
        raise ErrorDobleFactor("La verificación en dos pasos ya está activada")
    secreto = descifrar(usuario.totp_secreto)
    if secreto is None:
        raise ErrorDobleFactor("Primero generá el código QR")
    if verificar_codigo(secreto, codigo) is None:
        raise ErrorDobleFactor("El código no es correcto. Revisá que la hora del celular sea la automática.")
    codigos, huellas = generar_codigos_respaldo()
    usuario.totp_activo = True
    usuario.totp_codigos_respaldo = huellas
    db.commit()
    return codigos


def comprobar_segundo_paso(db: Session, usuario: UsuarioORM, codigo: str) -> bool:
    """`True` si `codigo` es el de la app (sin repetir uno ya usado) o uno de respaldo, que además se gasta."""
    secreto = descifrar(usuario.totp_secreto)
    if secreto is not None:
        paso = verificar_codigo(secreto, codigo, usuario.totp_ultimo_paso)
        if paso is not None:
            usuario.totp_ultimo_paso = paso
            db.commit()
            return True
    if _consumir_respaldo(usuario, codigo):
        db.commit()
        return True
    db.rollback()
    return False


def borrar_doble_factor(db: Session, usuario: UsuarioORM) -> None:
    """Deja la cuenta sin doble factor y sin secreto ni códigos de respaldo."""
    usuario.totp_activo = False
    usuario.totp_secreto = None
    usuario.totp_ultimo_paso = None
    usuario.totp_codigos_respaldo = None
    db.commit()


def obligatorio_en_el_servidor() -> bool:
    """`True` si el servidor exige el doble factor a la plataforma y a los administradores de institución (`DOBLE_FACTOR_OBLIGATORIO`)."""
    return os.environ.get("DOBLE_FACTOR_OBLIGATORIO", "").strip().lower() in ("1", "true", "si", "sí")


# --- el paso intermedio del inicio de sesión ---------------------------------------------------------------------------------


def crear_token_de_paso_2(usuario_id: int, version_credenciales: int = 0) -> str:
    """Una constancia de que la contraseña ya estaba bien, válida unos minutos y solo para completar el segundo paso."""
    vence = datetime.now(timezone.utc) + timedelta(minutes=MINUTOS_DEL_TOKEN_DE_PASO_2)
    return jwt.encode({"sub": str(usuario_id), "type": "2fa", "exp": vence, "cv": version_credenciales}, servicio_auth.SECRET_KEY, algorithm=servicio_auth.ALGORITHM)


def usuario_del_token_de_paso_2(token: str) -> int | None:
    try:
        datos = servicio_auth.decode_token(token)
        return int(datos["sub"]) if datos.get("type") == "2fa" else None
    except (JWTError, ValueError, KeyError):
        return None
