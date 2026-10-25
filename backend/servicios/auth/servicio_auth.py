import fnmatch
import json
import logging
import os
import secrets
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import UsuarioORM
from backend.servicios.auth.servicio_invitaciones import consumir_codigo

# Configuración de hashing (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Configuración JWT
logger = logging.getLogger(__name__)
SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not SECRET_KEY:
    # Sin clave definida se usa una al azar de este arranque: nadie puede falsificar sesiones con un valor que se lee en el repo.
    # Las sesiones abiertas se pierden en cada reinicio, así que en el servidor hay que definir JWT_SECRET_KEY en el entorno.
    SECRET_KEY = secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET_KEY no está definida: se usa una clave temporal y las sesiones no sobreviven a un reinicio")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 12  # 12 horas para sesiones de laboratorio
REFRESH_TOKEN_EXPIRE_DAYS = 7


def hash_password(password: str) -> str:
    """Genera hash bcrypt de la contraseña."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verifica contraseña contra hash."""
    if not hashed_password:
        return False
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Genera JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Genera JWT refresh token (larga duración)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh", "jti": uuid.uuid4().hex})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decodifica y valida JWT. Lanza JWTError si es inválido o expirado."""
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def get_user_by_email(db: Session, email: str) -> Optional[UsuarioORM]:
    """Busca usuario por email (case-insensitive)."""
    return db.execute(select(UsuarioORM).where(UsuarioORM.email == email.lower().strip())).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: int) -> Optional[UsuarioORM]:
    """Busca usuario por ID."""
    return db.get(UsuarioORM, user_id)


def get_users_by_ids(db: Session, user_ids: Iterable[int]) -> dict[int, UsuarioORM]:
    """Busca varios usuarios de una sola consulta (`WHERE id IN (...)`).

    Pensado para listados (ej. `GET /partida`, Registro de Partidas) donde se
    necesita el nombre del dueño de cada fila — evita hacer una consulta N+1
    por fila llamando a `get_user_by_id` en un loop.
    """
    ids = {user_id for user_id in user_ids if user_id is not None}
    if not ids:
        return {}
    filas = db.execute(select(UsuarioORM).where(UsuarioORM.id.in_(ids))).scalars().all()
    return {usuario.id: usuario for usuario in filas}


def login_google_demo_permitido() -> bool:
    """Indica si se aceptan tokens de prueba `demo_<correo>` / `mock_<correo>` en el login con Google.

    Sirven en desarrollo y en los tests, donde no hay un ID Token real de Google. En un servidor
    publicado hay que apagarlos con `PERMITIR_LOGIN_GOOGLE_DEMO=false`: con ellos cualquiera podría
    entrar con el correo que quisiera, incluido el de un facilitador. Si la variable no existe se
    mantiene el comportamiento de siempre (permitidos).
    """
    valor = os.environ.get("PERMITIR_LOGIN_GOOGLE_DEMO", "true").strip().lower()
    return valor in {"1", "true", "si", "sí", "yes"}


def verificar_token_google(token: str) -> dict:
    """Verifica un ID Token de Google contra los servidores de Google OAuth2.

    Retorna un diccionario con: email, sub, name, picture, email_verified.
    Lanza ValueError si el token es inválido o expiró.
    """
    # Soporte para tokens de prueba / desarrollo local (apagado en producción, ver login_google_demo_permitido)
    if (token.startswith("demo_") or token.startswith("mock_")) and not login_google_demo_permitido():
        raise ValueError("Token de Google inválido")
    if token.startswith("demo_") or token.startswith("mock_"):
        email_demo = token.replace("demo_", "").replace("mock_", "").strip()
        if "@" not in email_demo:
            email_demo = "jugador.demo@gmail.com"
        return {
            "email": email_demo,
            "sub": f"google_sub_{abs(hash(email_demo))}",
            "name": email_demo.split("@")[0].replace(".", " ").title(),
            "picture": "https://lh3.googleusercontent.com/aida-public/AB6AXuCMO5vNCHUFCALRsMCB3d9zKKZBq2uymquENQzCns1KnL-MM6bgeZembHFCzONDVXmn2D7mlufw6mTJ0PoUsy-2n-FkcyT8gic2zvIUjSc6MyD2rmIEKY7GwNaYRA5yIPOIcbP9M3Z2GbLAvy-Nivp26y-zCUt66g5vzNcjA1q0uBRwTfO1Fyfe2N8NtuP6YMADjpk23a0B7p3IVeTZz3kfN0UI79lVyoVbT6ZhkBfFHKVxQU7djqCHq7vzqvrZ7zDGSMc",
            "email_verified": "true",
        }

    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={token}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AjedrezRobotico/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            datos = json.loads(response.read().decode("utf-8"))
            if "error" in datos or "error_description" in datos:
                raise ValueError(datos.get("error_description", "Token de Google inválido"))
            if not datos.get("email"):
                raise ValueError("El token de Google no contiene un correo electrónico")
            # `aud` es la audiencia del token: el client_id de la app para la que Google lo
            # emitió. Sin este chequeo, un token válido de Google pero emitido para OTRA
            # aplicación (no la nuestra) se aceptaría igual — cualquiera con una cuenta de
            # Google y un token de cualquier app ajena podría loguearse acá.
            client_id_esperado = os.environ.get("GOOGLE_CLIENT_ID")
            if client_id_esperado and datos.get("aud") != client_id_esperado:
                raise ValueError("El token de Google no fue emitido para esta aplicación")
            return datos
    except urllib.error.HTTPError as e:
        raise ValueError(f"Error al validar token de Google: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"No se pudo conectar a los servidores de Google: {e.reason}") from e


def autenticar_o_vincular_google(
    db: Session,
    google_info: dict,
    rol_seleccionado: str = "jugador",
    clave_facilitador: str | None = None,
) -> UsuarioORM:
    """Autentica con Google.

    Vincula cuenta existente si el email coincide o crea una nueva cuenta
    con política estricta de asignación de roles seguros.
    """
    email = google_info["email"].lower().strip()
    sub = google_info.get("sub")
    nombre = google_info.get("name") or email.split("@")[0].title()
    avatar = google_info.get("picture")

    # 1. Si el usuario ya está vinculado por su google_id
    if sub:
        user_por_sub = db.execute(select(UsuarioORM).where(UsuarioORM.google_id == sub)).scalar_one_or_none()
        if user_por_sub:
            if avatar and not user_por_sub.avatar_url:
                user_por_sub.avatar_url = avatar
                db.commit()
                db.refresh(user_por_sub)
            return user_por_sub

    # 2. Si el usuario ya existe por email (Account Linking automático)
    user_por_email = get_user_by_email(db, email)
    if user_por_email:
        if sub and not user_por_email.google_id:
            user_por_email.google_id = sub
        if avatar and not user_por_email.avatar_url:
            user_por_email.avatar_url = avatar
        db.commit()
        db.refresh(user_por_email)
        return user_por_email

    # 3. Usuario nuevo: el rol de facilitador exige correo autorizado o un código de invitación
    codigo = _exigir_acceso_de_facilitador(db, email, rol_seleccionado, clave_facilitador)
    rol_final = "facilitador" if rol_seleccionado == "facilitador" else "jugador"

    nuevo_usuario = UsuarioORM(
        email=email,
        nombre=nombre,
        password_hash=None,
        rol=rol_final,
        google_id=sub,
        avatar_url=avatar,
        activo=True,
    )
    db.add(nuevo_usuario)
    _confirmar_alta(db, nuevo_usuario, codigo)
    return nuevo_usuario


def correos_facilitadores_autorizados() -> list[str]:
    """Correos (o patrones, p. ej. `*@universidad.edu`) que pueden registrarse como facilitador sin código.

    Salen de `CORREOS_FACILITADORES` en el entorno, separados por coma. Sirve para crear el PRIMER facilitador
    de una instalación nueva; no hay ningún correo ni dominio autorizado "de fábrica".
    """
    return [e.strip().lower() for e in os.environ.get("CORREOS_FACILITADORES", "").split(",") if e.strip()]


def _correo_autorizado(email: str) -> bool:
    return any(fnmatch.fnmatchcase(email, patron) for patron in correos_facilitadores_autorizados())


def es_correo_de_principal(email: str) -> bool:
    """`True` si `email` coincide con algún correo o patrón de `CORREOS_FACILITADORES`: es un facilitador principal.

    Es la misma configuración que autoriza al primer facilitador de una instalación; el principal es quien administra
    a los demás facilitadores (por ejemplo, asigna cursos).
    """
    return _correo_autorizado((email or "").strip().lower())


def _exigir_acceso_de_facilitador(db: Session, email: str, rol: str, codigo: str | None):
    """Valida que `email` pueda registrarse con `rol`. Devuelve la fila del código consumido, o `None`.

    Registrarse como jugador es libre. Registrarse como facilitador exige estar en `CORREOS_FACILITADORES` o
    presentar un código de invitación válido (de un solo uso y con vencimiento, ver `servicio_invitaciones`).

    Raises:
        ValueError: si pide ser facilitador sin correo autorizado ni código válido.
    """
    if rol != "facilitador" or _correo_autorizado(email):
        return None
    return consumir_codigo(db, codigo)


def _confirmar_alta(db: Session, usuario: UsuarioORM, codigo) -> None:
    """Guarda al usuario nuevo y, si entró con un código de invitación, lo deja marcado como usado por él.

    Todo en una sola transacción: si algo falla, el código no se gasta."""
    try:
        db.flush()
        if codigo is not None:
            codigo.usado_por = usuario.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(usuario)


def create_user(
    db: Session,
    email: str,
    nombre: str,
    password: str,
    rol: str = "jugador",
    clave_facilitador: str | None = None,
) -> UsuarioORM:
    """Crea un nuevo usuario con contraseña hasheada.

    Para el rol de facilitador, `clave_facilitador` es el código de invitación que dio otro facilitador (o el
    correo debe estar en `CORREOS_FACILITADORES`). Ver `servicio_invitaciones`."""
    email = email.lower().strip()
    codigo = _exigir_acceso_de_facilitador(db, email, rol, clave_facilitador)

    user = UsuarioORM(
        email=email,
        nombre=nombre,
        password_hash=hash_password(password),
        rol=rol,
        activo=True,
    )
    db.add(user)
    _confirmar_alta(db, user, codigo)
    return user


def actualizar_nivel_estimado(db: Session, user: UsuarioORM, nivel: int, rango: str) -> UsuarioORM:
    """Guarda el nivel/rango vigente del jugador: el de "Mide tu nivel" (HU5/HU10)
    o el que calcula `servicio_calibracion.registrar_calibracion` tras cada partida.

    Pisa el valor anterior a propósito — acá vive solo el nivel vigente; el
    historial de calibraciones por partida está en `CalibracionORM`.
    """
    user.nivel_estimado = nivel
    user.rango_estimado = rango
    db.commit()
    db.refresh(user)
    return user


def actualizar_perfil(db: Session, user: UsuarioORM, campos: dict) -> UsuarioORM:
    """Actualiza el perfil del usuario con edición parcial.

    `campos` trae solo las claves que el cliente mandó (ver `exclude_unset`
    en la ruta) — las que no vinieron no se tocan, así una edición de un solo
    campo (ej. `edad`) no pisa el resto con `None`.
    """
    for campo, valor in campos.items():
        setattr(user, campo, valor)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> Optional[UsuarioORM]:
    """Verifica credenciales y retorna usuario si son válidas."""
    user = get_user_by_email(db, email)
    if not user or not user.activo:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_tokens(user: UsuarioORM) -> tuple[str, str]:
    """Crea access + refresh tokens para un usuario."""
    payload = {"sub": str(user.id), "email": user.email, "rol": user.rol}
    access = create_access_token(payload)
    refresh = create_refresh_token(payload)
    return access, refresh


def decode_and_validate_access_token(token: str) -> Optional[int]:
    """Decodifica access token y retorna user_id si es válido."""
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        return int(payload.get("sub", 0))
    except (JWTError, ValueError):
        return None


def decode_and_validate_refresh_token(token: str) -> Optional[int]:
    """Decodifica refresh token y retorna user_id si es válido."""
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            return None
        return int(payload.get("sub", 0))
    except (JWTError, ValueError):
        return None