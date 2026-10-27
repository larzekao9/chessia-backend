"""Las 4 tablas mínimas de la base de datos (PLAN_IMPLEMENTACION_COMPLETO.md, sección 7).

Son la capa de "Modelos de datos" (representan las tablas), separada a
propósito de `partida.py` — la entidad de dominio (`Partida`, un dataclass
con un `chess.Board` adentro) que ya usan `servicio_partida.py` y las rutas.
Ninguno de los dos reemplaza al otro: `RepositorioPartidasPostgres`
(`backend/repositorios/repositorio_partida.py`) es el único lugar que
traduce entre ambos, tal como pide el patrón Repository (sección 4.3) — el
resto del backend sigue sin saber que SQLAlchemy existe.

Dos diferencias deliberadas respecto al SQL literal de la sección 7,
documentadas acá para no perder el motivo:

1. `partida.id` es TEXT (el uuid hex que ya genera `Partida.id`), no
   INTEGER autoincremental — así el id no cambia entre la versión en
   memoria y la versión en Postgres.
2. `partida` suma las columnas `fen`, `nivel`, `tipo_oponente` y
   `jugadas_uci`, que hoy viven directo en el dataclass `Partida` porque
   todavía no existe ningún flujo de sesión/participante (HU10/HU11 son las
   que lo van a crear). Los campos `participante_id` y `sesion_id` quedan
   nulleables mientras tanto. `jugadas_uci` guarda la lista de jugadas (UCI,
   separadas por espacio) para poder reconstruir el `chess.Board` completo
   al leer — la tabla `jugada` de abajo es para el detalle por jugada que
   necesita RF34/HU4 (quién decidió, tiempo de cálculo, explicación), no
   para esto.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


ROLES_USUARIO = ("jugador", "facilitador")
"""Roles posibles de un usuario. `jugador` es el único que puede usar la app
móvil; `facilitador` está pensado para la Sala de Control web."""


class UsuarioORM(Base):
    """Tabla de usuarios para autenticación (email + password hash + rol)."""
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(nullable=False)
    password_hash: Mapped[str | None] = mapped_column(nullable=True)
    rol: Mapped[str] = mapped_column(nullable=False, default="jugador", server_default="jugador")
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    activo: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Autenticación federada (Google OAuth) y perfil
    google_id: Mapped[str | None] = mapped_column(nullable=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(nullable=True)
    # Nivel/rango vigente del jugador: lo recalcula `servicio_calibracion` al terminar
    # cada partida (promedio de las últimas calibraciones, historial en `CalibracionORM`)
    # y también lo pisa "Mide tu nivel" (HU5/HU10) como punto de partida manual.
    nivel_estimado: Mapped[int | None] = mapped_column(nullable=True)
    rango_estimado: Mapped[str | None] = mapped_column(nullable=True)  # 'Principiante' | 'Intermedio' | 'Avanzado'
    # Perfil editable por el propio usuario (PATCH /auth/me) — genérico para
    # cualquier rol, no específico del diagnóstico de nivel del jugador de
    # arriba. `edad` y `descripcion` son de uso libre: para un facilitador
    # suele cubrir experiencia/trayectoria, para un jugador una bio corta.
    edad: Mapped[int | None] = mapped_column(nullable=True)
    descripcion: Mapped[str | None] = mapped_column(nullable=True)
    # Tono con el que Turing le habla a los estudiantes de este facilitador
    # ('infantil' | 'estandar' | 'adultos') — ver docstring de
    # `ActualizarPerfilRequest.preset_ensenanza`: les llega a los jugadores
    # inscritos en sus cursos (`CursoORM` / `InscripcionORM`).
    preset_ensenanza: Mapped[str | None] = mapped_column(nullable=True)
    # Institución a la que pertenece un facilitador (docente o administrador). Nulo en los jugadores (su institución se deduce
    # de su curso) y en los facilitadores de la plataforma, que no necesitan ninguna.
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("institucion.id"), nullable=True, index=True)
    institucion: Mapped["InstitucionORM | None"] = relationship(foreign_keys=[institucion_id], lazy="select")
    # Verificación del correo. Las cuentas anteriores y las de Google quedan verificadas; una cuenta nueva con contraseña queda sin
    # verificar solo si el servidor tiene el envío de correo configurado, y se verifica con un código de 6 dígitos que vence a los
    # 15 minutos (solo se guarda su huella).
    correo_verificado: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    correo_verificado_en: Mapped[datetime | None] = mapped_column(nullable=True)
    codigo_verificacion_hash: Mapped[str | None] = mapped_column(nullable=True)
    codigo_verificacion_expira: Mapped[datetime | None] = mapped_column(nullable=True)
    recuperacion_password_hash: Mapped[str | None] = mapped_column(nullable=True)
    recuperacion_password_expira: Mapped[datetime | None] = mapped_column(nullable=True)
    version_credenciales: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    # Verificación en dos pasos (TOTP) de los facilitadores. El secreto se guarda cifrado; los códigos de respaldo, como huellas.
    totp_activo: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    totp_secreto: Mapped[str | None] = mapped_column(nullable=True)
    totp_ultimo_paso: Mapped[int | None] = mapped_column(nullable=True)
    totp_codigos_respaldo: Mapped[str | None] = mapped_column(nullable=True)
    # Modo evaluación: un facilitador que prueba el sistema como director tiene además una cuenta de jugador de prueba, y cada una apunta a
    # la otra. Sirve para pasar de una vista a la otra sin volver a iniciar sesión. Se borra al convertir la institución en cliente.
    cuenta_vinculada_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    # Código para que otro jugador le mande una solicitud de amistad sin saber su correo (se comparte por WhatsApp). Se genera la primera
    # vez que se pide; sirve solo para mandar la solicitud: no muestra nada de quien lo tiene.
    codigo_amigo: Mapped[str | None] = mapped_column(nullable=True, index=True)
    # Contacto de la madre, el padre o el tutor de un jugador menor de edad, para que su docente pueda avisarle si pasa algo. Solo lo ven el propio
    # jugador, su docente, el administrador de su institución y la plataforma. `autorizacion_tutor_en` es cuándo se declaró que el tutor autorizó el uso.
    tutor_nombre: Mapped[str | None] = mapped_column(nullable=True)
    tutor_telefono: Mapped[str | None] = mapped_column(nullable=True)
    tutor_email: Mapped[str | None] = mapped_column(nullable=True)
    autorizacion_tutor_en: Mapped[datetime | None] = mapped_column(nullable=True)
    # `True` en las cuentas que creó un docente para un alumno sin correo (entra con un usuario y una clave que el docente le entrega).
    cuenta_gestionada: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    # Elo entre jugadores (ver `servicios/social/elo.py`): empieza en 1000 y se mueve con las partidas contra otras personas.
    elo: Mapped[int] = mapped_column(default=1000, server_default="1000", nullable=False)
    partidas_elo: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)

    partidas: Mapped[list["PartidaORM"]] = relationship(
        back_populates="usuario",
        cascade="all, delete-orphan",
        foreign_keys="PartidaORM.usuario_id",
    )


class ParticipanteORM(Base):
    __tablename__ = "participante"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(nullable=False)


class SesionORM(Base):
    __tablename__ = "sesion"

    id: Mapped[int] = mapped_column(primary_key=True)
    facilitador: Mapped[str | None] = mapped_column(nullable=True)
    fecha: Mapped[datetime] = mapped_column(server_default=func.now())
    dificultad: Mapped[int | None] = mapped_column(nullable=True)  # 0-20, nivel de Stockfish
    tipo_oponente: Mapped[str | None] = mapped_column(nullable=True)  # 'motor' | 'modelo' | 'participante'


class PartidaORM(Base):
    __tablename__ = "partida"

    id: Mapped[str] = mapped_column(primary_key=True)  # uuid hex, ver docstring del módulo
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    participante_id: Mapped[int | None] = mapped_column(ForeignKey("participante.id"), nullable=True)
    sesion_id: Mapped[int | None] = mapped_column(ForeignKey("sesion.id"), nullable=True)
    fecha: Mapped[datetime] = mapped_column(server_default=func.now())
    resultado: Mapped[str | None] = mapped_column(nullable=True)  # None mientras está 'en_curso'
    tipo: Mapped[str] = mapped_column(nullable=False)  # 'fisica' | 'digital'
    fen: Mapped[str] = mapped_column(nullable=False)
    # Posición desde la que arrancó la partida (ver docstring de `Partida.fen_inicial`
    # en `modelos/partida.py`) — nullable porque las filas de antes de esta columna
    # no la tienen; `_fila_a_partida` cae a la posición inicial estándar en ese caso.
    fen_inicial: Mapped[str | None] = mapped_column(nullable=True)
    nivel: Mapped[int] = mapped_column(nullable=False)
    tipo_oponente: Mapped[str] = mapped_column(nullable=False, default="motor")
    jugadas_uci: Mapped[str] = mapped_column(nullable=False, default="")
    # Funciones educativas opcionales por partida (ver `modelos/partida.py`) —
    # el facilitador las prende vía `PATCH /partida/{id}/permisos`.
    permite_simulacion_3d: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    permite_camara: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Partida que el facilitador transmite en vivo a la clase (ver `modelos/partida.py`,
    # `Partida.es_demostracion`) — solo una fila en `True` a la vez en todo el sistema.
    es_demostracion: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Si la jugada de respuesta de la estrategia activa también se ejecuta en el
    # brazo (ver `modelos/partida.py`, `Partida.usa_brazo`, HU9).
    usa_brazo: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Si una partida entre personas mueve el Elo (puntuada) o es amistosa (ver `servicios/social/elo.py`); solo importa en las de `tipo_oponente = 'humano'`.
    puntuada: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # Ciclo de vida de la partida (ver `modelos/partida.py`, `Partida.estado` y
    # `ESTADOS_PARTIDA`): 'en_curso' | 'terminada' | 'abandonada'.
    estado: Mapped[str] = mapped_column(nullable=False, default="en_curso", server_default="en_curso")
    # Momento de la primera jugada del jugador humano. Nulo si la partida se
    # creó pero nadie movió todavía (ver `Partida.iniciada_en`).
    iniciada_en: Mapped[datetime | None] = mapped_column(nullable=True)
    # Momento de la última jugada aplicada (humano o estrategia). Nulo hasta
    # la primera jugada (ver `Partida.actualizada_en`).
    actualizada_en: Mapped[datetime | None] = mapped_column(nullable=True)
    # Reloj de la partida (ver `modelos/partida.py`, `Partida.control_tiempo_ms`): tiempo por lado en
    # milisegundos (0 = sin reloj) y lo que le queda a cada uno. Nulo en los restantes = control completo.
    control_tiempo_ms: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    tiempo_blancas_ms: Mapped[int | None] = mapped_column(nullable=True)
    tiempo_negras_ms: Mapped[int | None] = mapped_column(nullable=True)
    # Milisegundos que tardó cada jugada, separados por espacio y en el mismo orden que `jugadas_uci`;
    # "-" cuando no se midió (ver `Partida.tiempos_jugadas_ms`).
    tiempos_jugadas_ms: Mapped[str] = mapped_column(nullable=False, default="", server_default="")
    # Sala (humano vs humano)
    # Índice único parcial creado en `database._agregar_columnas_faltantes`
    codigo_sala: Mapped[str | None] = mapped_column(nullable=True)
    usuario_blancas_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)
    usuario_negras_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True)

    jugadas: Mapped[list["JugadaORM"]] = relationship(back_populates="partida", cascade="all, delete-orphan")
    usuario: Mapped["UsuarioORM"] = relationship(
        back_populates="partidas",
        foreign_keys="PartidaORM.usuario_id",
    )


class JugadaORM(Base):
    """Todavía sin poblar desde `servicio_partida.py` (RF34, HU4) — la tabla ya
    existe para cuando llegue esa HU, no hace falta crearla de nuevo."""

    __tablename__ = "jugada"

    id: Mapped[int] = mapped_column(primary_key=True)
    partida_id: Mapped[str] = mapped_column(ForeignKey("partida.id"), nullable=False)
    numero: Mapped[int] = mapped_column(nullable=False)
    fen_antes: Mapped[str] = mapped_column(nullable=False)
    movimiento: Mapped[str] = mapped_column(nullable=False)  # notación UCI, ej. "e2e4"
    decidido_por: Mapped[str | None] = mapped_column(nullable=True)  # 'motor' | 'modelo' | 'jugador'
    tiempo_calculo_ms: Mapped[int | None] = mapped_column(nullable=True)
    explicacion: Mapped[str | None] = mapped_column(nullable=True)
    # Evaluación de Stockfish de esta jugada — se pobla una sola vez, cuando
    # `GET /partida/{id}/analisis-completo` (HU5) recalcula la partida entera;
    # no se calcula acá ni en `/usuario/estadisticas` para no repetir llamadas
    # a Stockfish (ver `servicio_partida.analisis_completo` y
    # `RepositorioPartidas.actualizar_evaluacion_jugada`). Misma convención de
    # signos que `JugadaAnalisisResponse` (`backend/esquemas/partida_esquema.py`):
    # `evaluacion_cp`/`mate_en` son el resultado de la jugada REALMENTE jugada;
    # `evaluacion_mejor_cp`/`mate_en_mejor` son lo que valía la mejor jugada en
    # la posición "antes", ambos en la perspectiva de quien jugó.
    evaluacion_cp: Mapped[int | None] = mapped_column(nullable=True)
    mate_en: Mapped[int | None] = mapped_column(nullable=True)
    evaluacion_mejor_cp: Mapped[int | None] = mapped_column(nullable=True)
    mate_en_mejor: Mapped[int | None] = mapped_column(nullable=True)

    partida: Mapped[PartidaORM] = relationship(back_populates="jugadas")


class MensajeTutorORM(Base):
    """Historial de turnos de la conversación con el tutor "Turing"
    (`backend/servicios/tutor/servicio_tutor.py`). Tabla nueva — no hace falta
    tocar `_COLUMNAS_AGREGADAS` en `backend/database.py`, `Base.metadata.create_all`
    ya la crea sola.

    La memoria de la conversación vive acá, nunca en la API de Groq: cada
    request al tutor reconstruye la lista `messages` completa (system prompt +
    historial de esta tabla + mensaje nuevo) desde cero."""

    __tablename__ = "mensaje_tutor"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    rol: Mapped[str] = mapped_column(nullable=False)  # 'user' | 'assistant'
    contenido: Mapped[str] = mapped_column(nullable=False)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class CalibracionORM(Base):
    """Una fila por partida terminada que ya calibró el nivel de su dueño
    (`backend/servicios/calibracion/servicio_calibracion.py`). Tabla nueva —
    `Base.metadata.create_all` la crea sola, no hace falta tocar
    `_COLUMNAS_AGREGADAS` en `backend/database.py`.

    `precision_global` es la precisión de las jugadas del propio jugador en esa
    partida (cada una comparada contra Stockfish), no la de ambos bandos.
    `nivel_partida`/`rango_partida` son el nivel que saldría mirando SOLO esa
    partida; el nivel vigente del jugador (`UsuarioORM.nivel_estimado`) sale del
    promedio de las últimas calibraciones, no de esta columna.

    `partida_id` no es clave foránea: las partidas pueden vivir solo en memoria
    (`RepositorioPartidasEnMemoria`) y no tener fila en `partida`. La restricción
    única `(usuario_id, partida_id)` hace idempotente registrar dos veces la
    misma partida.
    """

    __tablename__ = "calibracion"
    __table_args__ = (
        UniqueConstraint("usuario_id", "partida_id", name="uq_calibracion_usuario_partida"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    partida_id: Mapped[str] = mapped_column(nullable=False)
    precision_global: Mapped[float] = mapped_column(nullable=False)
    nivel_partida: Mapped[int] = mapped_column(nullable=False)
    rango_partida: Mapped[str] = mapped_column(nullable=False)
    total_jugadas: Mapped[int] = mapped_column(nullable=False)  # jugadas evaluadas del jugador
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class ExportacionDatasetORM(Base):
    """Una fila por cada descarga del dataset de partidas para reentrenar el
    modelo propio (HU4, todavía sin construir — ver
    `backend/servicios/entrenamiento/servicio_dataset.py`). Tabla nueva —
    `Base.metadata.create_all` la crea sola, no hace falta tocar
    `_COLUMNAS_AGREGADAS` en `backend/database.py`.

    `corte_en` es el instante de corte de esa descarga (el más reciente entre
    `actualizada_en`/`fecha` de las partidas incluidas) — sirve para que la
    próxima descarga con `solo_nuevas=true` sepa desde dónde son "nuevas"
    las partidas (ver `servicio_dataset.estado_entrenamiento`)."""

    __tablename__ = "exportacion_dataset"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    corte_en: Mapped[datetime] = mapped_column(nullable=False)
    cantidad_partidas: Mapped[int] = mapped_column(nullable=False)
    cantidad_jugadas: Mapped[int] = mapped_column(nullable=False)
    formato: Mapped[str] = mapped_column(nullable=False)


class CodigoInvitacionORM(Base):
    """Código de invitación para registrarse como facilitador. Lo genera un facilitador, sirve una sola vez
    y vence a los pocos minutos. Solo se guarda su hash (SHA-256): el código en claro se muestra una única
    vez, al generarlo. Tabla nueva: `Base.metadata.create_all` la crea sola, no hace falta tocar
    `_COLUMNAS_AGREGADAS` en `backend/database.py`."""

    __tablename__ = "codigo_invitacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo_hash: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    creado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True, index=True)
    para: Mapped[str | None] = mapped_column(nullable=True)  # a quién se lo dio, solo para acordarse
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    expira_en: Mapped[datetime] = mapped_column(nullable=False)
    usado_en: Mapped[datetime | None] = mapped_column(nullable=True)
    usado_por: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True, index=True)
    # Si el código es para una institución, quien se registra con él queda dentro de ella (como docente o, si `como_administrador`, como
    # su administrador). Sin institución, solo da permiso para registrarse como facilitador.
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("institucion.id"), nullable=True, index=True)
    como_administrador: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    # `true` si es una invitación para evaluar el sistema: además de quedar como administrador, a quien se registre se le crea un jugador de prueba.
    es_evaluacion: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)


class AuditoriaORM(Base):
    """Registro de lo que hacen los administradores sobre cuentas, instituciones y códigos de invitación (quién, qué y a quién).

    `actor_id` y `objetivo_id` NO son claves foráneas a propósito: el registro tiene que sobrevivir a la baja de la cuenta de la
    que habla. `detalle` es un texto corto sin datos personales (nada de correos ni nombres). Se conserva 1 año. Tabla nueva:
    `Base.metadata.create_all` la crea sola."""

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    actor_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    accion: Mapped[str] = mapped_column(nullable=False, index=True)
    objetivo_tipo: Mapped[str | None] = mapped_column(nullable=True)
    objetivo_id: Mapped[int | None] = mapped_column(nullable=True)
    detalle: Mapped[str | None] = mapped_column(nullable=True)


TIPOS_INSTITUCION = ("academia", "colegio", "organizador")
"""Tipos de institución cliente (los segmentos del CANVAS): academias y clubes, colegios y organizadores de torneos."""


class InstitucionORM(Base):
    """Institución cliente de la plataforma (una asociación, un colegio, un organizador de torneos). Agrupa a sus docentes
    (`usuario.institucion_id`) y tiene un administrador, que es quien después administra su suscripción. Tabla nueva:
    `Base.metadata.create_all` la crea sola."""

    __tablename__ = "institucion"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(unique=True, nullable=False)
    tipo: Mapped[str] = mapped_column(nullable=False, default="academia", server_default="academia")
    # `use_alter`: usuario e institución se apuntan entre sí; esta restricción se agrega al final, cuando existen las dos tablas.
    administrador_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", use_alter=True, name="fk_institucion_administrador"), nullable=True
    )
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    # `true` en las instituciones de ejemplo que carga la plataforma para mostrar el panel de ingresos: se pueden quitar todas juntas.
    es_demostracion: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    # `true` mientras es una institución de prueba, creada para que una institución evalúe el sistema antes de contratarlo.
    es_prueba: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    # Dominio del correo institucional (`colegio.edu.bo`). Si está definido, solo entran a los cursos de sus docentes los jugadores con un correo de ese dominio.
    dominio_correo: Mapped[str | None] = mapped_column(nullable=True)


PLANES_SUSCRIPCION = ("plan_60", "plan_120")
"""Planes: hasta 60 alumnos (Bs 350 al mes) y hasta 120 alumnos (Bs 500 al mes). Docentes y cursos ilimitados."""

CICLOS_SUSCRIPCION = ("mensual", "trimestral", "anual")
ESTADOS_PAGO = ("pendiente", "confirmado", "rechazado")


class SuscripcionORM(Base):
    """La suscripción de una institución: su plan, hasta cuándo dura la prueba gratis y hasta cuándo está pagada. El estado (prueba,
    activa o vencida) no se guarda: se calcula con estas dos fechas. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "suscripcion"

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int] = mapped_column(ForeignKey("institucion.id"), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(nullable=False, default="plan_60", server_default="plan_60")
    ciclo: Mapped[str] = mapped_column(nullable=False, default="mensual", server_default="mensual")
    prueba_hasta: Mapped[datetime] = mapped_column(nullable=False)
    vigente_hasta: Mapped[datetime | None] = mapped_column(nullable=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class PagoORM(Base):
    """Un pago que una institución avisa haber hecho (transferencia o QR). Queda `pendiente` hasta que la plataforma lo confirma, y recién
    ahí se extiende la suscripción. `resuelto_por` no es clave foránea a propósito: el registro sobrevive a la cuenta de quien lo
    confirmó. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "pago"

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int] = mapped_column(ForeignKey("institucion.id"), nullable=False, index=True)
    plan: Mapped[str] = mapped_column(nullable=False)
    ciclo: Mapped[str] = mapped_column(nullable=False)
    monto_bs: Mapped[int] = mapped_column(nullable=False)
    estado: Mapped[str] = mapped_column(nullable=False, default="pendiente", server_default="pendiente", index=True)
    referencia: Mapped[str | None] = mapped_column(nullable=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    resuelto_en: Mapped[datetime | None] = mapped_column(nullable=True)
    resuelto_por: Mapped[int | None] = mapped_column(nullable=True)
    periodo_hasta: Mapped[datetime | None] = mapped_column(nullable=True)
    nota: Mapped[str | None] = mapped_column(nullable=True)


class CursoORM(Base):
    """Curso (o grupo) de un facilitador: reúne a sus estudiantes. Los jugadores entran con `codigo`, que sirve
    para muchos (a diferencia de los códigos de facilitador, que son de un solo uso) y que el facilitador puede
    volver a ver y regenerar. Tabla nueva: `Base.metadata.create_all` la crea sola."""

    __tablename__ = "curso"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(nullable=False)
    facilitador_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    codigo: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    acepta_ingresos: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    cupo: Mapped[int] = mapped_column(default=60, server_default="60", nullable=False)
    # Tono de Turing para los jugadores de este curso ('infantil' | 'estandar' | 'adultos'); nulo = el del facilitador.
    preset_ensenanza: Mapped[str | None] = mapped_column(nullable=True)
    # «Modo clase»: el docente puede pausar los retos, la búsqueda de rival y las salas entre alumnos de este curso mientras explica.
    permite_retos: Mapped[bool] = mapped_column(default=True, server_default="true", nullable=False)
    # El docente (o la institución) declara que tiene la autorización de las familias de los alumnos de este curso para que usen ChessIA. Con eso, los alumnos menores
    # no necesitan cargar los datos de un tutor uno por uno para jugar con sus compañeros. `autorizacion_familias_en` es cuándo se declaró.
    autorizacion_familias: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    autorizacion_familias_en: Mapped[datetime | None] = mapped_column(nullable=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class InscripcionORM(Base):
    """Un jugador dentro de un curso. `usuario_id` es único: un jugador está en un solo curso a la vez (entrar a
    otro lo mueve). Tabla nueva: `Base.metadata.create_all` la crea sola."""

    __tablename__ = "inscripcion"

    id: Mapped[int] = mapped_column(primary_key=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("curso.id"), nullable=False, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, unique=True)
    unido_en: Mapped[datetime] = mapped_column(server_default=func.now())


class MedallaGanadaORM(Base):
    """Una medalla que un jugador ya ganó. Es permanente: queda aunque después se borre la partida o cambie la regla.
    `vista_en` nulo = todavía no se le mostró el aviso de "nueva medalla". Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "medalla_ganada"
    __table_args__ = (UniqueConstraint("usuario_id", "codigo", name="uq_medalla_ganada_usuario_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    codigo: Mapped[str] = mapped_column(nullable=False)
    ganada_en: Mapped[datetime] = mapped_column(server_default=func.now())
    vista_en: Mapped[datetime | None] = mapped_column(nullable=True)


class MedallaPersonalizadaORM(Base):
    """Medalla creada por un facilitador. `general` (solo el principal) la ven todos los jugadores; si no, solo los jugadores
    de los cursos de quien la creó. `regla` es una de las del catálogo (`REGLAS`) o `manual`. La imagen es un archivo en
    `backend/media/medallas/<codigo>.<ext>`, como las de las medallas de fábrica. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "medalla_personalizada"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(nullable=False)
    descripcion: Mapped[str] = mapped_column(nullable=False)
    nivel: Mapped[str] = mapped_column(nullable=False)
    pieza: Mapped[str] = mapped_column(nullable=False, default="peon", server_default="peon")
    regla: Mapped[str] = mapped_column(nullable=False)
    meta: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    general: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    creada_por: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    creada_en: Mapped[datetime] = mapped_column(server_default=func.now())


class AmistadORM(Base):
    """Una amistad entre dos jugadores. `solicitante_id` la pidió (por correo, por código de amigo o desde su curso) y `destinatario_id`
    la acepta (`estado = "aceptada"`) o la rechaza (la fila se borra, así quien pidió no se entera). Los compañeros de curso no necesitan
    amistad para jugar entre sí: esto es para los que no comparten curso. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "amistad"
    __table_args__ = (UniqueConstraint("solicitante_id", "destinatario_id", name="uq_amistad_par"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    solicitante_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    destinatario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    estado: Mapped[str] = mapped_column(nullable=False, default="pendiente", server_default="pendiente")
    creada_en: Mapped[datetime] = mapped_column(server_default=func.now())
    respondida_en: Mapped[datetime | None] = mapped_column(nullable=True)


class EloMovimientoORM(Base):
    """Cuánto se movió el Elo de un jugador en una partida contra otra persona: una fila por jugador y por sala (la sala se anota una sola vez, aunque la consulten los dos a la
    vez). Sirve para mostrarle cómo viene (su historial) y para que el Elo no se cuente dos veces. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "elo_movimiento"
    __table_args__ = (UniqueConstraint("usuario_id", "sala_codigo", name="uq_elo_movimiento_sala"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    rival_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    sala_codigo: Mapped[str] = mapped_column(nullable=False, index=True)
    color: Mapped[str] = mapped_column(nullable=False)  # 'blancas' | 'negras'
    resultado: Mapped[float] = mapped_column(nullable=False)  # 1 ganó, 0.5 tablas, 0 perdió (para este jugador)
    elo_antes: Mapped[int] = mapped_column(nullable=False)
    elo_despues: Mapped[int] = mapped_column(nullable=False)
    elo_rival: Mapped[int] = mapped_column(nullable=False)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class DenunciaORM(Base):
    """Una denuncia de un jugador contra otro (por su conducta, por hacer trampa o por abandonar partidas). La revisa el docente del denunciado, el administrador de su
    institución o la plataforma, que la marca como revisada. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "denuncia"

    id: Mapped[int] = mapped_column(primary_key=True)
    denunciante_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    denunciado_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    motivo: Mapped[str] = mapped_column(nullable=False)  # 'conducta' | 'trampa' | 'abandono' | 'otro'
    detalle: Mapped[str | None] = mapped_column(nullable=True)
    codigo_sala: Mapped[str | None] = mapped_column(nullable=True)
    estado: Mapped[str] = mapped_column(nullable=False, default="nueva", server_default="nueva")  # 'nueva' | 'revisada'
    creada_en: Mapped[datetime] = mapped_column(server_default=func.now())
    resuelta_en: Mapped[datetime | None] = mapped_column(nullable=True)
    resuelta_por: Mapped[int | None] = mapped_column(nullable=True)  # sin clave foránea: el registro sobrevive a la cuenta de quien lo resolvió


class TorneoORM(Base):
    """Un torneo relámpago de un curso (sistema suizo): los alumnos se inscriben, el docente arma cada ronda y el sistema empareja por puntos. Las partidas son salas
    con el reloj elegido. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "torneo"

    id: Mapped[int] = mapped_column(primary_key=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("curso.id"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(nullable=False)
    control_tiempo_ms: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    rondas: Mapped[int] = mapped_column(nullable=False, default=3, server_default="3")
    ronda_actual: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    estado: Mapped[str] = mapped_column(nullable=False, default="inscripcion", server_default="inscripcion")  # 'inscripcion' | 'en_juego' | 'terminado'
    creado_por: Mapped[int] = mapped_column(nullable=False)  # sin clave foránea: el torneo sobrevive a la cuenta de quien lo armó
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class TorneoJugadorORM(Base):
    """Un jugador inscrito en un torneo, con sus puntos (1 por victoria, 0,5 por tablas, 1 por descanso). Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "torneo_jugador"
    __table_args__ = (UniqueConstraint("torneo_id", "usuario_id", name="uq_torneo_jugador"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    torneo_id: Mapped[int] = mapped_column(ForeignKey("torneo.id"), nullable=False, index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    puntos: Mapped[float] = mapped_column(nullable=False, default=0.0, server_default="0")
    descansos: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")  # rondas en que le tocó descansar (número impar de jugadores)
    inscrito_en: Mapped[datetime] = mapped_column(server_default=func.now())


class TorneoPartidaORM(Base):
    """Una partida de una ronda de un torneo: quién juega con blancas y con negras (`negras_id` nulo = descanso del jugador de blancas), el código de su sala y cómo
    terminó. Tabla nueva: `create_all` la crea sola."""

    __tablename__ = "torneo_partida"

    id: Mapped[int] = mapped_column(primary_key=True)
    torneo_id: Mapped[int] = mapped_column(ForeignKey("torneo.id"), nullable=False, index=True)
    ronda: Mapped[int] = mapped_column(nullable=False)
    blancas_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"), nullable=False, index=True)
    negras_id: Mapped[int | None] = mapped_column(ForeignKey("usuario.id"), nullable=True, index=True)
    codigo_sala: Mapped[str | None] = mapped_column(nullable=True)
    resultado: Mapped[str | None] = mapped_column(nullable=True)  # '1-0' | '0-1' | '1/2-1/2' | '0-0' (nadie se presentó); nulo mientras se juega


class AvisoCursoORM(Base):
    """Un aviso de un docente a su curso (una consigna, el enlace de una clase en vivo, una felicitación). Es en un solo sentido: los alumnos lo leen, no responden. Tabla
    nueva: `create_all` la crea sola."""

    __tablename__ = "aviso_curso"

    id: Mapped[int] = mapped_column(primary_key=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("curso.id"), nullable=False, index=True)
    autor_id: Mapped[int] = mapped_column(nullable=False)  # sin clave foránea: el aviso sobrevive a la cuenta de quien lo escribió
    texto: Mapped[str] = mapped_column(nullable=False)
    enlace: Mapped[str | None] = mapped_column(nullable=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())


class TareaCursoORM(Base):
    """Una tarea de un docente para su curso, con una meta que el sistema mide solo con lo que los alumnos juegan (cuántas partidas, con quién, qué precisión). Tabla nueva:
    `create_all` la crea sola."""

    __tablename__ = "tarea_curso"

    id: Mapped[int] = mapped_column(primary_key=True)
    curso_id: Mapped[int] = mapped_column(ForeignKey("curso.id"), nullable=False, index=True)
    autor_id: Mapped[int] = mapped_column(nullable=False)  # sin clave foránea: la tarea sobrevive a la cuenta de quien la puso
    titulo: Mapped[str] = mapped_column(nullable=False)
    tipo: Mapped[str] = mapped_column(nullable=False)  # 'partidas_ia' | 'partidas_amigos' | 'precision'
    meta: Mapped[int] = mapped_column(nullable=False)
    desde: Mapped[datetime] = mapped_column(nullable=False)
    hasta: Mapped[datetime] = mapped_column(nullable=False)
    creada_en: Mapped[datetime] = mapped_column(server_default=func.now())
