"""Conexión a la base de datos (PostgreSQL) — sección 7 de PLAN_IMPLEMENTACION_COMPLETO.md.

`DATABASE_URL` se lee de una variable de entorno para no hardcodear ninguna
credencial en el repositorio (ver las reglas del proyecto sobre no commitear
secretos). Si no está seteada, el backend sigue funcionando igual que hasta
ahora — en memoria, vía `RepositorioPartidasEnMemoria` — porque no todas las
máquinas del equipo tienen Postgres corriendo (ver `backend/repositorios/repositorio_partida.py::crear_repositorio_partidas`).

Ejemplo de valor para `DATABASE_URL`:
`postgresql+psycopg2://usuario:password@localhost:5432/ajedrez`
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

if os.environ.get("PYTEST_RUNNING") != "1":
    load_dotenv()
    DATABASE_URL = os.environ.get("DATABASE_URL")
else:
    DATABASE_URL = None


class Base(DeclarativeBase):
    """Clase base declarativa de la que heredan las tablas en `backend/modelos/tablas_orm.py`."""


def obtener_engine() -> Engine:
    """Crea el engine de SQLAlchemy a partir de `DATABASE_URL` (PostgreSQL o SQLite).

    Para PostgreSQL fuerza la zona horaria de la sesión a UTC
    (`-c timezone=utc`): las columnas `TIMESTAMP` de este proyecto son "sin
    zona" (`server_default=func.now()` guarda la hora de pared de la sesión,
    no UTC explícito), así que sin esto la hora guardada depende de cómo
    esté configurado el Postgres de cada máquina — hoy "funciona de
    casualidad" en el servidor que ya está en GMT, pero no en una máquina
    con otro huso. Con la sesión fija en UTC, esa hora de pared siempre ES
    UTC, y `fecha_a_iso` de abajo puede asumirlo con seguridad."""
    db_url = os.environ.get("DATABASE_URL") or DATABASE_URL or "sqlite:///./ajedrez.db"
    if db_url.startswith("sqlite"):
        return create_engine(db_url, connect_args={"check_same_thread": False})
    # `pool_pre_ping`: antes de usar una conexión del pool comprueba que siga viva. Sin esto, tras reiniciar Postgres, dormir la máquina o un corte de red,
    # la primera consulta usa una conexión muerta y la ruta responde 500 (después se recupera sola).
    return create_engine(db_url, connect_args={"options": "-c timezone=utc"}, pool_pre_ping=True)


def fecha_a_iso(valor: datetime | None) -> str | None:
    """Convierte un `datetime` de una columna `TIMESTAMP` a ISO 8601 con
    offset UTC explícito (`+00:00`), o `None` si `valor` es `None`.

    Las columnas `TIMESTAMP` de este proyecto no llevan zona horaria
    (`Mapped[datetime]` sin más), así que SQLAlchemy siempre devuelve un
    `datetime` "naive" (sin `tzinfo`) — con `obtener_engine` fijando la
    sesión de Postgres a UTC, ese valor naive ES la hora UTC, así que
    marcarlo como tal acá es seguro. Sin esto, `valor.isoformat()` a secas
    produce una cadena sin sufijo de zona (ej. "2026-09-28T04:56:28"), y
    `new Date(...)` en el navegador la interpreta como hora LOCAL del
    cliente en vez de UTC — el bug real de "horas adelantadas" que reportó
    un usuario en Bolivia (UTC-4): la 00:50 UTC se mostraba como si fueran
    las 04:50 locales.

    Usar siempre esta función (nunca `valor.isoformat()` suelto) para
    convertir cualquier columna `TIMESTAMP`/`DateTime` de este proyecto a
    texto para una respuesta HTTP."""
    if valor is None:
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.isoformat()


def crear_tablas(engine: Engine) -> None:
    """Crea las tablas (sección 7 + `usuario`) si todavía no existen. Idempotente.

    Aplica columnas faltantes si la base ya existía y siembra los usuarios
    iniciales de prueba para desarrollo y demostración.
    """
    Base.metadata.create_all(engine)
    _agregar_columnas_faltantes(engine)
    _marcar_partidas_con_resultado_como_terminadas(engine)
    _limpiar_nivel_estimado_de_facilitadores(engine)
    if sembrado_de_usuarios_de_prueba_habilitado(engine):
        sembrar_usuarios_iniciales(engine)


# Servicios de base de datos gestionada donde nunca se deben crear los usuarios de prueba.
_HOSTS_DE_PRODUCCION = ("supabase.co", "supabase.com")


def sembrado_de_usuarios_de_prueba_habilitado(engine: Engine) -> bool:
    """Indica si al arrancar se crean los usuarios de prueba (`sembrar_usuarios_iniciales`).

    Esos usuarios tienen contraseñas fijas conocidas (`admin123`, `test123456`), útiles en
    desarrollo pero inaceptables en producción, sobre todo el facilitador. Por eso:

    - Si existe la variable de entorno `SEMBRAR_USUARIOS_PRUEBA`, manda ella: `true`/`1`/`si`
      los crea y cualquier otro valor no.
    - Si no existe, se crean salvo que la base esté en Supabase (`*.supabase.co` o
      `*.supabase.com`), donde nunca se crean por defecto. En SQLite y PostgreSQL local todo
      sigue igual que antes.
    """
    valor = os.environ.get("SEMBRAR_USUARIOS_PRUEBA", "").strip().lower()
    if valor:
        return valor in {"1", "true", "si", "sí", "yes"}
    host = (engine.url.host or "").lower()
    return not host.endswith(_HOSTS_DE_PRODUCCION)


_COLUMNAS_AGREGADAS: dict[str, dict[str, str]] = {
    "usuario": {
        "rol": "VARCHAR NOT NULL DEFAULT 'jugador'",
        "nivel_estimado": "INTEGER",
        "rango_estimado": "VARCHAR",
        "google_id": "VARCHAR",
        "avatar_url": "VARCHAR",
        "edad": "INTEGER",
        "descripcion": "VARCHAR",
        "tema": "VARCHAR NOT NULL DEFAULT 'dia'",
        "paleta": "VARCHAR NOT NULL DEFAULT 'clasico'",
        "preset_ensenanza": "VARCHAR",
        "institucion_id": "INTEGER",
        "correo_verificado": "BOOLEAN NOT NULL DEFAULT TRUE",
        "correo_verificado_en": "TIMESTAMP",
        "codigo_verificacion_hash": "VARCHAR",
        "codigo_verificacion_expira": "TIMESTAMP",
        "recuperacion_password_hash": "VARCHAR",
        "recuperacion_password_expira": "TIMESTAMP",
        "version_credenciales": "INTEGER NOT NULL DEFAULT 0",
        "totp_activo": "BOOLEAN NOT NULL DEFAULT FALSE",
        "totp_secreto": "VARCHAR",
        "totp_ultimo_paso": "INTEGER",
        "totp_codigos_respaldo": "VARCHAR",
        "cuenta_vinculada_id": "INTEGER",
        "codigo_amigo": "VARCHAR",
        "tutor_nombre": "VARCHAR",
        "tutor_telefono": "VARCHAR",
        "tutor_email": "VARCHAR",
        "autorizacion_tutor_en": "TIMESTAMP",
        "cuenta_gestionada": "BOOLEAN NOT NULL DEFAULT FALSE",
        "elo": "INTEGER NOT NULL DEFAULT 1000",
        "partidas_elo": "INTEGER NOT NULL DEFAULT 0",
    },
    "institucion": {
        "es_demostracion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "es_prueba": "BOOLEAN NOT NULL DEFAULT FALSE",
        "dominio_correo": "VARCHAR",
    },
    "codigo_invitacion": {
        "institucion_id": "INTEGER",
        "como_administrador": "BOOLEAN NOT NULL DEFAULT FALSE",
        "es_evaluacion": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "curso": {
        "cupo": "INTEGER NOT NULL DEFAULT 60",
        "preset_ensenanza": "VARCHAR",
        "permite_retos": "BOOLEAN NOT NULL DEFAULT TRUE",
        "autorizacion_familias": "BOOLEAN NOT NULL DEFAULT FALSE",
        "autorizacion_familias_en": "TIMESTAMP",
    },
    "partida": {
        "permite_simulacion_3d": "BOOLEAN NOT NULL DEFAULT FALSE",
        "permite_camara": "BOOLEAN NOT NULL DEFAULT FALSE",
        "fen_inicial": "VARCHAR",
        "es_demostracion": "BOOLEAN NOT NULL DEFAULT FALSE",
        "usa_brazo": "BOOLEAN NOT NULL DEFAULT FALSE",
        "puntuada": "BOOLEAN NOT NULL DEFAULT TRUE",
        "estado": "VARCHAR NOT NULL DEFAULT 'en_curso'",
        "iniciada_en": "TIMESTAMP",
        "actualizada_en": "TIMESTAMP",
        "control_tiempo_ms": "INTEGER NOT NULL DEFAULT 0",
        "tiempo_blancas_ms": "INTEGER",
        "tiempo_negras_ms": "INTEGER",
        "tiempos_jugadas_ms": "VARCHAR NOT NULL DEFAULT ''",
        # Sala (humano vs humano)
        "codigo_sala": "VARCHAR",
        "usuario_blancas_id": "INTEGER",
        "usuario_negras_id": "INTEGER",
    },
}


def _agregar_columnas_faltantes(engine: Engine) -> None:
    with engine.begin() as conexion:
        # El inspector se ata a `conexion` (no a `engine`) para que toda la introspección
        # quede dentro de esta misma transacción: si se ata al engine, cada consulta del
        # inspector abre su propia conexión nueva, y esa conexión se traba esperando el
        # lock que esta misma transacción ya tiene tomado sobre la tabla que acaba de alterar
        # (auto-deadlock de un solo proceso contra sí mismo).
        inspector = inspect(conexion)
        for tabla, columnas in _COLUMNAS_AGREGADAS.items():
            if not inspector.has_table(tabla):
                continue
            existentes = {c["name"] for c in inspector.get_columns(tabla)}
            for columna, definicion in columnas.items():
                if columna not in existentes:
                    conexion.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))
        # Índice único parcial para codigo_sala (solo donde no es null)
        if inspector.has_table("partida"):
            indices = {idx["name"] for idx in inspector.get_indexes("partida")}
            if "ix_partida_codigo_sala" not in indices:
                conexion.execute(
                    text(
                        "CREATE UNIQUE INDEX ix_partida_codigo_sala ON partida (codigo_sala) "
                        "WHERE codigo_sala IS NOT NULL"
                    )
                )


def _marcar_partidas_con_resultado_como_terminadas(engine: Engine) -> None:
    """Corrige el `estado` de las partidas que ya tenían `resultado` antes de
    que existiera la columna `estado` (HU sala de control, ciclo de vida).

    Idempotente: solo toca las que quedaron en `'en_curso'` a pesar de tener
    un resultado — correrlo de nuevo sobre una base ya corregida no cambia
    nada. Se ejecuta siempre, junto con `_agregar_columnas_faltantes`, no
    solo la primera vez que aparece la columna.

    No hace nada si la tabla `partida` todavía no tiene `resultado` o
    `estado` (bases sintéticas mínimas, como las de test) — en cualquier
    base real ambas columnas siempre están."""
    inspector = inspect(engine)
    if not inspector.has_table("partida"):
        return
    columnas_existentes = {columna["name"] for columna in inspector.get_columns("partida")}
    if not {"resultado", "estado"} <= columnas_existentes:
        return
    with engine.begin() as conexion:
        conexion.execute(
            text("UPDATE partida SET estado = 'terminada' WHERE resultado IS NOT NULL AND estado = 'en_curso'")
        )


def _limpiar_nivel_estimado_de_facilitadores(engine: Engine) -> None:
    """Borra `nivel_estimado`/`rango_estimado` de cualquier usuario con
    `rol = 'facilitador'` que los tenga puestos — un facilitador no tiene
    nivel de juego propio; esos datos quedaron de antes de que
    `PATCH /auth/nivel-estimado` (`ruta_auth.guardar_nivel_estimado`)
    empezara a rechazar la llamada para roles que no son `jugador`.

    Idempotente igual que `_marcar_partidas_con_resultado_como_terminadas`:
    solo toca las filas que todavía tengan alguno de los dos campos puesto,
    así que correrlo de nuevo sobre una base ya corregida no cambia nada."""
    inspector = inspect(engine)
    if not inspector.has_table("usuario"):
        return
    with engine.begin() as conexion:
        conexion.execute(
            text(
                "UPDATE usuario SET nivel_estimado = NULL, rango_estimado = NULL "
                "WHERE rol = 'facilitador' AND (nivel_estimado IS NOT NULL OR rango_estimado IS NOT NULL)"
            )
        )


def sembrar_usuarios_iniciales(engine: Engine) -> None:
    """Crea los usuarios iniciales (facilitador y jugador) si no existen todavía."""
    from backend.modelos.tablas_orm import UsuarioORM
    from backend.servicios.auth.servicio_auth import hash_password

    fabrica = sessionmaker(bind=engine)
    with fabrica() as session:
        # 1. Facilitador de prueba (clave: admin123)
        facilitador = session.execute(
            select(UsuarioORM).where(UsuarioORM.email == "facilitador@test.com")
        ).scalar_one_or_none()
        if not facilitador:
            session.add(
                UsuarioORM(
                    email="facilitador@test.com",
                    nombre="Facilitador Árbitro",
                    password_hash=hash_password("admin123"),
                    rol="facilitador",
                    activo=True,
                )
            )

        # 2. Jugador de prueba (clave: test123456)
        jugador = session.execute(
            select(UsuarioORM).where(UsuarioORM.email == "jugador@test.com")
        ).scalar_one_or_none()
        if not jugador:
            session.add(
                UsuarioORM(
                    email="jugador@test.com",
                    nombre="Jugador Aspirante",
                    password_hash=hash_password("test123456"),
                    rol="jugador",
                    activo=True,
                )
            )

        # 3. Jugador KAIROS (clave: test123456)
        jugador_kairos = session.execute(
            select(UsuarioORM).where(UsuarioORM.email == "jugador@kairos-chess.ai")
        ).scalar_one_or_none()
        if not jugador_kairos:
            session.add(
                UsuarioORM(
                    email="jugador@kairos-chess.ai",
                    nombre="Jugador Kairos Core",
                    password_hash=hash_password("test123456"),
                    rol="jugador",
                    activo=True,
                )
            )

        # 4. Facilitador Hebert Suarez Burgos
        facilitador_hebert = session.execute(
            select(UsuarioORM).where(UsuarioORM.email == "suarezburgoshebert@gmail.com")
        ).scalar_one_or_none()
        if not facilitador_hebert:
            session.add(
                UsuarioORM(
                    email="suarezburgoshebert@gmail.com",
                    nombre="Hebert Suarez Burgos",
                    password_hash=hash_password("admin123"),
                    rol="facilitador",
                    activo=True,
                )
            )
        elif facilitador_hebert.rol != "facilitador":
            facilitador_hebert.rol = "facilitador"

        session.commit()


def crear_fabrica_sesiones(engine: Engine) -> sessionmaker[Session]:
    """Fábrica de sesiones de SQLAlchemy para el engine dado."""
    return sessionmaker(bind=engine)
