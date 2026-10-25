"""Genera la documentación de la base de datos a partir de los modelos del código.

Fuente de verdad: las clases ORM de `backend/modelos/tablas_orm.py`. Este script
no se conecta a ninguna base; lee `Base.metadata` y escribe, junto a él:

- `base_completa.sql`: la base completa para desarrollo local (CREATE TABLE / INDEX / COMMENT ON).
- `base_supabase_produccion.sql`: la misma base lista para producción en Supabase (idempotente,
  con Row Level Security y sin usuarios de prueba).
- `DICCIONARIO_DE_DATOS.md`: tablas, columnas, claves, relaciones y diagramas
  (Mermaid) listos para pasar al documento de grado.

Uso, desde la raíz del repositorio:

    python docs/base_de_datos/generar_esquema.py

Si se agrega una tabla o columna al modelo sin describirla acá, el script se
detiene y avisa cuál falta: así el diccionario no puede quedar desactualizado.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from backend.database import Base  # noqa: E402
from backend.modelos import tablas_orm  # noqa: E402,F401  (registra las tablas en Base.metadata)

CARPETA = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Descripciones (una por tabla y por columna)
# ---------------------------------------------------------------------------
TABLAS: dict[str, tuple[str, str]] = {
    "usuario": (
        "Personas que usan el sistema, con su rol, su nivel de juego y su perfil.",
        "En uso",
    ),
    "partida": (
        "Cada partida jugada: quién la jugó, contra qué oponente, a qué nivel, la posición actual y "
        "las funciones opcionales que el facilitador habilitó.",
        "En uso",
    ),
    "jugada": (
        "Detalle de cada jugada de una partida: quién la decidió y cómo la evaluó Stockfish.",
        "En uso",
    ),
    "mensaje_tutor": (
        "Historial de la conversación de cada jugador con el tutor Turing (memoria multi-turno).",
        "En uso",
    ),
    "calibracion": (
        "Una fila por partida terminada que sirvió para medir el nivel del jugador. Con las últimas tres "
        "se calcula su nivel vigente.",
        "En uso",
    ),
    "exportacion_dataset": (
        "Una fila por cada vez que un facilitador descargó el dataset de partidas para reentrenar el "
        "modelo propio (HU4, todavía pendiente de construir).",
        "En uso",
    ),
    "codigo_invitacion": (
        "Códigos de un solo uso, con vencimiento, que un facilitador genera para que otra persona pueda registrarse "
        "como facilitador. Solo se guarda su hash.",
        "En uso",
    ),
    "institucion": (
        "Institución cliente de la plataforma (asociación, colegio u organizador de torneos). Agrupa a sus docentes y tiene "
        "un administrador, que es quien administra su suscripción.",
        "En uso",
    ),
    "curso": (
        "Grupo de estudiantes de un facilitador. Los jugadores entran con un código del curso, que sirve para "
        "muchos y el facilitador puede volver a ver y regenerar.",
        "En uso",
    ),
    "inscripcion": (
        "Un jugador dentro de un curso. Un jugador está en un solo curso a la vez.",
        "En uso",
    ),
    "medalla_personalizada": (
        "Medallas que crean los facilitadores (nombre, descripción, nivel, pieza, regla para ganarla y alcance). Las 13 "
        "medallas de fábrica no están acá: viven en el código.",
        "En uso",
    ),
    "medalla_ganada": (
        "Medallas (figuritas) que cada jugador ya ganó, con la fecha. Son permanentes. El catálogo de medallas vive en "
        "el código (`backend/servicios/medallas/catalogo.py`).",
        "En uso",
    ),
    "participante": (
        "Estudiantes de un curso, para sesiones del facilitador.",
        "Reservada: el modelo existe pero ningún flujo la usa todavía",
    ),
    "sesion": (
        "Sesión de clase que abre un facilitador, con su dificultad y tipo de oponente.",
        "Reservada: el modelo existe pero ningún flujo la usa todavía",
    ),
}

COLUMNAS: dict[tuple[str, str], str] = {
    # usuario
    ("usuario", "id"): "Identificador del usuario.",
    ("usuario", "email"): "Correo del usuario; es único y se usa para iniciar sesión.",
    ("usuario", "nombre"): "Nombre visible.",
    ("usuario", "password_hash"): "Hash de la contraseña (nunca se guarda en claro). Nulo si entra solo con Google.",
    ("usuario", "rol"): "`jugador` o `facilitador`.",
    ("usuario", "creado_en"): "Fecha y hora de registro.",
    ("usuario", "activo"): "Indica si la cuenta está habilitada.",
    ("usuario", "google_id"): "Identificador de Google cuando entra con su cuenta de Google.",
    ("usuario", "avatar_url"): "Ruta o URL de la foto de perfil.",
    ("usuario", "nivel_estimado"): (
        "Nivel de juego vigente, de 0 a 20 (la escala del Skill Level de Stockfish). Lo recalcula el sistema al "
        "terminar cada partida, o lo fija el jugador como punto de partida desde su perfil."
    ),
    ("usuario", "rango_estimado"): "`Principiante` (niveles 0-6), `Intermedio` (7-13) o `Avanzado` (14-20).",
    ("usuario", "edad"): "Edad, de uso libre en el perfil.",
    ("usuario", "descripcion"): "Biografía o trayectoria breve, de uso libre en el perfil.",
    ("usuario", "preset_ensenanza"): (
        "Tono con el que Turing habla: `infantil`, `estandar` o `adultos`. Lo elige el facilitador."
    ),
    # participante
    ("participante", "id"): "Identificador del participante.",
    ("participante", "nombre"): "Nombre del participante.",
    # sesion
    ("sesion", "id"): "Identificador de la sesión.",
    ("sesion", "facilitador"): "Nombre del facilitador que la abrió.",
    ("sesion", "fecha"): "Fecha y hora de la sesión.",
    ("sesion", "dificultad"): "Nivel de dificultad de la sesión, de 0 a 20.",
    ("sesion", "tipo_oponente"): "`motor`, `modelo` o `participante`.",
    # partida
    ("partida", "id"): "Identificador de la partida: un UUID en hexadecimal (texto), no un número.",
    ("partida", "usuario_id"): "Dueño de la partida. Nulo en partidas antiguas o de prueba sin dueño.",
    ("partida", "participante_id"): "Reservado para sesiones de clase. Hoy siempre nulo.",
    ("partida", "sesion_id"): "Reservado para sesiones de clase. Hoy siempre nulo.",
    ("partida", "fecha"): "Fecha y hora de creación.",
    ("partida", "resultado"): "`1-0`, `0-1` o `1/2-1/2`. Nulo mientras la partida sigue en curso o no se terminó.",
    ("partida", "tipo"): "`digital` (se juega en pantalla) o `fisica` (con el tablero real detectado por visión).",
    ("partida", "fen"): "Posición actual del tablero en notación FEN.",
    ("partida", "fen_inicial"): "Posición desde la que arrancó la partida (FEN). Nulo en partidas antiguas.",
    ("partida", "nivel"): "Nivel del oponente elegido al crear la partida, de 0 a 20. No cambia durante la partida.",
    ("partida", "tipo_oponente"): "`motor` (Stockfish) o `modelo` (Turing, el modelo propio).",
    ("partida", "jugadas_uci"): "Todas las jugadas en notación UCI separadas por espacio; permite reconstruir el tablero.",
    ("partida", "permite_simulacion_3d"): "El facilitador habilitó la simulación 3D para esta partida.",
    ("partida", "permite_camara"): "El facilitador habilitó la cámara del tablero físico para esta partida.",
    ("partida", "es_demostracion"): "Partida que el facilitador transmite en vivo a la clase. Solo una a la vez en todo el sistema.",
    ("partida", "usa_brazo"): "La respuesta del oponente también se ejecuta en el brazo robótico (o su simulador).",
    ("partida", "estado"): "`esperando`, `en_curso`, `terminada` o `abandonada`. Lo administra el ciclo de vida de la partida. `esperando` = sala creada, falta segundo jugador (solo `humano`).",
    ("partida", "iniciada_en"): "Momento de la primera jugada del jugador humano. Nulo si todavía no jugó ninguna.",
    ("partida", "actualizada_en"): "Momento de la última jugada aplicada (humano o estrategia). Nulo hasta la primera jugada.",
    ("partida", "control_tiempo_ms"): "Control de tiempo: milisegundos que tiene cada lado para toda la partida. 0 = sin reloj. Se elige al crear la partida.",
    ("partida", "tiempo_blancas_ms"): "Tiempo que le queda al jugador humano (blancas), en milisegundos. Nulo = todavía tiene el control completo. Permite reanudar la partida con el reloj donde se quedó.",
    ("partida", "tiempo_negras_ms"): "Tiempo que le queda al rival (negras), en milisegundos. Nulo = todavía tiene el control completo.",
    ("partida", "tiempos_jugadas_ms"): "Milisegundos que tardó cada jugada, separados por espacio y en el mismo orden que las jugadas; `-` si no se midió. Alimenta el tiempo por jugada del análisis y la retroalimentación.",
    # Sala (humano vs humano)
    ("partida", "codigo_sala"): (
        "Código corto de 6 caracteres para compartir la sala (alfabeto sin ambiguos). "
        "Único donde no es nulo (índice único parcial). Solo partidas `tipo_oponente = 'humano'`."
    ),
    ("partida", "usuario_blancas_id"): "Usuario que juega con blancas en la sala. Nulo si el asiento está libre. FK → usuario.id.",
    ("partida", "usuario_negras_id"): "Usuario que juega con negras en la sala. Nulo si el asiento está libre. FK → usuario.id.",
    # jugada
    ("jugada", "id"): "Identificador de la jugada.",
    ("jugada", "partida_id"): "Partida a la que pertenece.",
    ("jugada", "numero"): "Número de la jugada dentro de la partida (1, 2, 3...). Las impares son del jugador humano.",
    ("jugada", "fen_antes"): "Posición (FEN) justo antes de jugarla.",
    ("jugada", "movimiento"): "Jugada en notación UCI, por ejemplo `e2e4`.",
    ("jugada", "decidido_por"): "Quién la jugó: `jugador`, `motor` o `modelo`.",
    ("jugada", "tiempo_calculo_ms"): "Milisegundos que tardó en calcularse. Nulo si no se midió.",
    ("jugada", "explicacion"): "Explicación en lenguaje natural de la jugada. Nulo si no se generó.",
    ("jugada", "evaluacion_cp"): "Evaluación de Stockfish, en centipeones, del resultado de la jugada realmente jugada.",
    ("jugada", "mate_en"): "Si tras la jugada hay mate forzado, en cuántas jugadas. Nulo si no lo hay.",
    ("jugada", "evaluacion_mejor_cp"): "Evaluación, en centipeones, de la mejor jugada posible en esa posición.",
    ("jugada", "mate_en_mejor"): "Mate forzado disponible con la mejor jugada. Nulo si no lo había.",
    # mensaje_tutor
    ("mensaje_tutor", "id"): "Identificador del mensaje.",
    ("mensaje_tutor", "usuario_id"): "Jugador que conversa con el tutor.",
    ("mensaje_tutor", "rol"): "`user` (lo escribió el jugador) o `assistant` (respondió Turing).",
    ("mensaje_tutor", "contenido"): "Texto del mensaje.",
    ("mensaje_tutor", "creado_en"): "Fecha y hora del mensaje.",
    # calibracion
    ("calibracion", "id"): "Identificador de la calibración.",
    ("calibracion", "usuario_id"): "Jugador medido.",
    ("calibracion", "partida_id"): (
        "Partida que se usó para medir. No es clave foránea a propósito: una partida puede vivir solo en "
        "memoria y no tener fila en `partida`."
    ),
    ("calibracion", "precision_global"): (
        "Precisión del jugador en esa partida, de 0 a 100: compara solo sus jugadas con las de Stockfish."
    ),
    ("calibracion", "nivel_partida"): "Nivel (0-20) que saldría mirando solamente esa partida.",
    ("calibracion", "rango_partida"): "Rango que saldría mirando solamente esa partida.",
    ("calibracion", "total_jugadas"): "Cantidad de jugadas del jugador que se evaluaron (mínimo 5 para registrarla).",
    ("calibracion", "creado_en"): "Fecha y hora en que se registró.",
    # codigo_invitacion
    ("codigo_invitacion", "id"): "Identificador del código.",
    ("codigo_invitacion", "codigo_hash"): "Huella SHA-256 del código. El código en claro no se guarda: solo se ve al generarlo.",
    ("codigo_invitacion", "creado_por"): "Facilitador que lo generó.",
    ("codigo_invitacion", "para"): "A quién se lo dio (texto libre, opcional), solo para acordarse.",
    ("codigo_invitacion", "creado_en"): "Fecha y hora en que se generó.",
    ("codigo_invitacion", "expira_en"): "Fecha y hora a partir de la cual el código ya no sirve.",
    ("codigo_invitacion", "usado_en"): "Fecha y hora en que se usó. Nulo mientras no se haya usado: sirve una sola vez.",
    ("codigo_invitacion", "usado_por"): "Usuario que se registró con este código.",
    ("usuario", "institucion_id"): (
        "Institución a la que pertenece un facilitador (docente o administrador). FK → institucion.id. Nulo en los jugadores, "
        "cuya institución se deduce de su curso, y en los facilitadores de la plataforma."
    ),
    # institucion
    ("institucion", "id"): "Identificador de la institución.",
    ("institucion", "nombre"): "Nombre de la institución (por ejemplo, `Asociación Departamental de Ajedrez de Santa Cruz`). Es único.",
    ("institucion", "tipo"): "`academia` (academias y clubes), `colegio` u `organizador` (de torneos y exhibiciones).",
    ("institucion", "administrador_id"): "Usuario que administra la institución. FK → usuario.id. Nulo hasta que se designa uno.",
    ("institucion", "creado_en"): "Fecha y hora de creación.",
    # curso
    ("curso", "id"): "Identificador del curso.",
    ("curso", "nombre"): "Nombre del curso, que elige el facilitador (por ejemplo, `Ajedrez 1ro A`).",
    ("curso", "facilitador_id"): "Facilitador dueño del curso. FK → usuario.id.",
    ("curso", "codigo"): (
        "Código de 6 caracteres (alfabeto sin ambiguos) que el facilitador le da a la clase. Es único, sirve para "
        "muchos jugadores y se guarda en claro porque solo permite entrar al curso como jugador."
    ),
    ("curso", "acepta_ingresos"): "Si es falso, el código no deja entrar a nadie más; los ya inscritos siguen.",
    ("curso", "preset_ensenanza"): (
        "Tono con el que Turing les habla a los jugadores de este curso: `infantil`, `estandar` o `adultos`. "
        "Nulo = se usa el tono que eligió el facilitador en su perfil."
    ),
    ("curso", "cupo"): "Cantidad máxima de jugadores que pueden inscribirse, de 1 a 120 (60 por defecto). Lo define quien crea el curso.",
    ("curso", "creado_en"): "Fecha y hora de creación.",
    # inscripcion
    ("inscripcion", "id"): "Identificador de la inscripción.",
    ("inscripcion", "curso_id"): "Curso en el que está el jugador. FK → curso.id.",
    ("inscripcion", "usuario_id"): "Jugador inscrito. Único: está en un solo curso a la vez. FK → usuario.id.",
    ("inscripcion", "unido_en"): "Fecha y hora en que entró al curso.",
    # medalla_personalizada
    ("medalla_personalizada", "id"): "Identificador de la medalla.",
    ("medalla_personalizada", "codigo"): "Código único y estable (`propia_` y 8 caracteres): es la clave de las medallas ganadas y de su imagen.",
    ("medalla_personalizada", "nombre"): "Nombre corto de la medalla.",
    ("medalla_personalizada", "descripcion"): "Frase que dice cómo se gana.",
    ("medalla_personalizada", "nivel"): "`bronce`, `plata`, `oro` o `legendaria`: define el color y el brillo del escudo.",
    ("medalla_personalizada", "pieza"): "Silueta de pieza que se muestra mientras no tenga imagen propia.",
    ("medalla_personalizada", "regla"): "Cómo se gana: una regla automática (por ejemplo `victorias`) o `manual`.",
    ("medalla_personalizada", "meta"): "Cantidad que hay que alcanzar con la regla (1 en las manuales y en las de sí o no).",
    ("medalla_personalizada", "general"): "Verdadero: la ven todos los jugadores (solo el facilitador principal puede crearlas así). Falso: solo los de los cursos de su creador.",
    ("medalla_personalizada", "creada_por"): "Facilitador que la creó. FK → usuario.id.",
    ("medalla_personalizada", "creada_en"): "Fecha y hora de creación.",
    # medalla_ganada
    ("medalla_ganada", "id"): "Identificador de la medalla ganada.",
    ("medalla_ganada", "usuario_id"): "Jugador que la ganó. FK → usuario.id.",
    ("medalla_ganada", "codigo"): "Código de la medalla en el catálogo (por ejemplo `primer_peon`). Único por jugador.",
    ("medalla_ganada", "ganada_en"): "Fecha y hora en que la ganó.",
    ("medalla_ganada", "vista_en"): "Cuándo se le mostró el aviso de nueva medalla. Nulo = todavía no se le avisó.",
    # exportacion_dataset
    ("exportacion_dataset", "id"): "Identificador de la descarga.",
    ("exportacion_dataset", "usuario_id"): "Facilitador que descargó el dataset.",
    ("exportacion_dataset", "creado_en"): "Fecha y hora en que se generó la descarga.",
    ("exportacion_dataset", "corte_en"): (
        "Instante de corte de esta descarga: la fecha más reciente entre las partidas incluidas. Sirve para "
        "saber qué partidas son \"nuevas\" en la próxima descarga."
    ),
    ("exportacion_dataset", "cantidad_partidas"): "Cuántas partidas incluyó esta descarga.",
    ("exportacion_dataset", "cantidad_jugadas"): "Cuántas jugadas del jugador incluyó esta descarga.",
    ("exportacion_dataset", "formato"): "Formato del archivo entregado, por ejemplo `pgn+csv`.",
}

RELACIONES = [
    ("usuario", "partida", "1 a N", "Un usuario juega muchas partidas; una partida tiene un solo dueño (opcional)."),
    ("partida", "jugada", "1 a N", "Una partida tiene muchas jugadas; se borran con la partida."),
    ("usuario", "mensaje_tutor", "1 a N", "Un jugador tiene su propio historial de conversación con Turing."),
    ("usuario", "calibracion", "1 a N", "Un jugador acumula una calibración por cada partida que sirvió para medirlo."),
    ("usuario", "exportacion_dataset", "1 a N", "Un facilitador acumula una fila por cada vez que descargó el dataset de partidas."),
    ("usuario", "codigo_invitacion", "1 a N", "Un facilitador genera muchos códigos de invitación; cada uno lo usa, a lo sumo, una persona."),
    ("institucion", "usuario", "1 a N", "Una institución agrupa a sus facilitadores (docentes); cada facilitador pertenece a una sola institución."),
    ("usuario", "institucion", "1 a 1", "Un usuario es el administrador de, a lo sumo, una institución."),
    ("usuario", "curso", "1 a N", "Un facilitador crea varios cursos; cada curso tiene un solo facilitador."),
    ("curso", "inscripcion", "1 a N", "Un curso tiene muchos jugadores inscritos."),
    ("usuario", "medalla_personalizada", "1 a N", "Un facilitador crea muchas medallas propias."),
    ("usuario", "medalla_ganada", "1 a N", "Un jugador gana muchas medallas; cada una la gana una sola vez."),
    ("usuario", "inscripcion", "1 a 1", "Un jugador tiene, a lo sumo, una inscripción: está en un solo curso a la vez."),
    ("participante", "partida", "1 a N", "Opcional y reservada: una partida puede asignarse a un participante."),
    ("sesion", "partida", "1 a N", "Opcional y reservada: una partida puede pertenecer a una sesión de clase."),
    # Sala (humano vs humano)
    ("usuario", "partida", "1 a N", "Un usuario juega muchas salas como blancas; una sala tiene un blanco (opcional)."),
    ("usuario", "partida", "1 a N", "Un usuario juega muchas salas como negras; una sala tiene un negro (opcional)."),
]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _tipo_sql(columna) -> str:
    return columna.type.compile(dialect=postgresql.dialect())


def _valor_por_defecto(columna) -> str:
    sd = columna.server_default
    if sd is not None and hasattr(sd, "arg"):
        return str(getattr(sd.arg, "text", sd.arg))
    if columna.default is not None and getattr(columna.default, "arg", None) not in (None,):
        return str(columna.default.arg)
    return ""


def _clave(columna) -> str:
    partes = []
    if columna.primary_key:
        partes.append("PK")
    if columna.foreign_keys:
        destino = next(iter(columna.foreign_keys)).column
        partes.append(f"FK → {destino.table.name}.{destino.name}")
    if columna.unique:
        partes.append("único")
    return ", ".join(partes)


def _validar_descripciones() -> None:
    faltan = []
    for tabla in Base.metadata.sorted_tables:
        if tabla.name not in TABLAS:
            faltan.append(f"tabla {tabla.name}")
        for columna in tabla.columns:
            if (tabla.name, columna.name) not in COLUMNAS:
                faltan.append(f"columna {tabla.name}.{columna.name}")
    sobran = [f"{t}.{c}" for (t, c) in COLUMNAS if t not in Base.metadata.tables or c not in Base.metadata.tables[t].c]
    if faltan or sobran:
        raise SystemExit(
            "El diccionario de docs/base_de_datos/generar_esquema.py no coincide con el modelo.\n"
            f"  Sin describir: {faltan}\n  Descritas pero ya inexistentes: {sobran}"
        )


def _sql_literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------
def _sql_tablas(dialecto, *, si_no_existe: bool) -> list[str]:
    """DDL de todas las tablas e índices del modelo, en orden de dependencias."""
    salida: list[str] = []
    for tabla in Base.metadata.sorted_tables:
        salida.append(f"-- {tabla.name}: {TABLAS[tabla.name][0]}")
        salida.append(str(CreateTable(tabla, if_not_exists=si_no_existe).compile(dialect=dialecto)).strip() + ";")
        for indice in sorted(tabla.indexes, key=lambda i: i.name or ""):
            salida.append(str(CreateIndex(indice, if_not_exists=si_no_existe).compile(dialect=dialecto)).strip() + ";")
        salida.append("")
    return salida


def _sql_restricciones_pendientes() -> list[str]:
    """Claves foráneas que se agregan al final (`use_alter`): `CREATE TABLE` no las trae porque las dos tablas se apuntan entre sí.
    Se escriben de forma que se puedan volver a correr sin error."""
    salida: list[str] = []
    for tabla in Base.metadata.sorted_tables:
        for restriccion in sorted(tabla.foreign_key_constraints, key=lambda r: r.name or ""):
            if not restriccion.use_alter:
                continue
            elemento = restriccion.elements[0]
            salida.append(
                f"ALTER TABLE {tabla.name} DROP CONSTRAINT IF EXISTS {restriccion.name};\n"
                f"ALTER TABLE {tabla.name} ADD CONSTRAINT {restriccion.name} FOREIGN KEY ({restriccion.column_keys[0]}) "
                f"REFERENCES {elemento.column.table.name} ({elemento.column.name});"
            )
    return (["-- Claves foráneas entre tablas que se apuntan entre sí"] + salida + [""]) if salida else []


def _sql_comentarios() -> list[str]:
    salida = ["-- Comentarios de tablas y columnas (visibles en pgAdmin y en el editor de Supabase)"]
    for tabla in Base.metadata.sorted_tables:
        salida.append(f"COMMENT ON TABLE {tabla.name} IS {_sql_literal(TABLAS[tabla.name][0])};")
        for columna in tabla.columns:
            descripcion = COLUMNAS[(tabla.name, columna.name)].replace("`", "")
            salida.append(f"COMMENT ON COLUMN {tabla.name}.{columna.name} IS {_sql_literal(descripcion)};")
    return salida


def generar_sql() -> str:
    """Script de la base completa para desarrollo local (PostgreSQL)."""
    salida = [
        "-- =====================================================================",
        "-- BASE COMPLETA (desarrollo local) - Sistema Tutor Inteligente de Ajedrez",
        "-- PostgreSQL 16. Generado desde backend/modelos/tablas_orm.py con",
        "-- docs/base_de_datos/generar_esquema.py. No editar a mano: cambiar el modelo y",
        "-- volver a generar.",
        "--",
        "-- La aplicación crea estas tablas sola al arrancar (backend/database.py,",
        "-- crear_tablas) y siembra los usuarios de prueba de docs/usuarios_locales_dev.md.",
        "-- Este script sirve para crear la misma base por fuera de la aplicación:",
        "--",
        "--     CREATE DATABASE ajedrez;              -- una sola vez, conectado a 'postgres'",
        "--     psql -U <usuario> -d ajedrez -f docs/base_de_datos/base_completa.sql",
        "--",
        "-- Para producción en Supabase usar base_supabase_produccion.sql.",
        "-- =====================================================================",
        "",
    ]
    salida += _sql_tablas(postgresql.dialect(), si_no_existe=False)
    salida += _sql_restricciones_pendientes()
    salida += _sql_comentarios()
    return "\n".join(salida) + "\n"


# Índices que solo mejoran consultas por clave foránea; no cambian el comportamiento.
_INDICES_PRODUCCION = [
    ("ix_partida_usuario_id", "partida", "usuario_id"),
    ("ix_jugada_partida_id", "jugada", "partida_id"),
]


def generar_sql_supabase() -> str:
    """Script de la base para producción en Supabase (PostgreSQL gestionado)."""
    tablas = [t.name for t in Base.metadata.sorted_tables]
    lista_tablas = ", ".join(f"'{t}'" for t in tablas)
    salida = [
        "-- =====================================================================",
        "-- BASE DE PRODUCCIÓN PARA SUPABASE - Sistema Tutor Inteligente de Ajedrez",
        "-- Mismas tablas y columnas que base_completa.sql (generado desde",
        "-- backend/modelos/tablas_orm.py con docs/base_de_datos/generar_esquema.py).",
        "-- No editar a mano: cambiar el modelo y volver a generar.",
        "--",
        "-- Cómo usarlo: Supabase > SQL Editor > New query > pegar todo > Run.",
        "-- Es idempotente: se puede ejecutar más de una vez sin duplicar ni romper nada.",
        "--",
        "-- Diferencias con la base de desarrollo (ninguna cambia el comportamiento):",
        "--   1. CREATE ... IF NOT EXISTS y una sola transacción (todo o nada).",
        "--   2. Índices extra en las claves foráneas (partida.usuario_id, jugada.partida_id).",
        "--   3. Seguridad: Row Level Security activado en todas las tablas y sin acceso para",
        "--      los roles anon y authenticated. Supabase expone por defecto una API REST sobre",
        "--      el esquema public; sin esto, quien tenga la clave pública podría leer la tabla",
        "--      usuario (incluidos los hashes de contraseña). El backend no usa esa API: se",
        "--      conecta directamente como 'postgres', que no se ve afectado por RLS.",
        "--   4. NO se cargan usuarios de prueba. El primer facilitador se crea registrándose",
        "--      en la aplicación (ver docs/base_de_datos/DESPLIEGUE_SUPABASE.md).",
        "-- =====================================================================",
        "",
        "BEGIN;",
        "",
    ]
    salida += _sql_tablas(postgresql.dialect(), si_no_existe=True)
    salida += _sql_restricciones_pendientes()
    salida.append("-- Índices extra para consultas por clave foránea")
    for nombre, tabla, columna in _INDICES_PRODUCCION:
        salida.append(f"CREATE INDEX IF NOT EXISTS {nombre} ON {tabla} ({columna});")
    salida.append("")
    salida += _sql_comentarios()
    salida += [
        "",
        "-- Seguridad: Row Level Security sin políticas = nadie accede por la API de Supabase.",
        "DO $$",
        "DECLARE",
        "    tabla text;",
        "    rol text;",
        "BEGIN",
        f"    FOREACH tabla IN ARRAY ARRAY[{lista_tablas}] LOOP",
        "        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', current_schema(), tabla);",
        "    END LOOP;",
        "    FOREACH rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP",
        "        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rol) THEN",
        "            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', current_schema(), rol);",
        "            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', current_schema(), rol);",
        "        END IF;",
        "    END LOOP;",
        "END $$;",
        "",
        "COMMIT;",
        "",
        "-- Verificación: las 7 tablas deben aparecer con rowsecurity = true.",
        "SELECT tablename, rowsecurity FROM pg_tables",
        f"WHERE schemaname = current_schema() AND tablename IN ({lista_tablas})",
        "ORDER BY tablename;",
    ]
    return "\n".join(salida) + "\n"


def _tipo_mermaid(columna) -> str:
    return _tipo_sql(columna).split("(")[0].replace(" ", "_").replace("WITHOUT_TIME_ZONE", "").rstrip("_") or "TEXT"


def generar_er() -> str:
    lineas = ["```mermaid", "erDiagram"]
    for tabla_origen, tabla_destino, _card, _texto in RELACIONES:
        opcional = tabla_origen in ("participante", "sesion") or tabla_origen == "usuario" and tabla_destino == "partida"
        simbolo = "|o--o{" if opcional else "||--o{"
        lineas.append(f"    {tabla_origen} {simbolo} {tabla_destino} : tiene")
    for tabla in Base.metadata.sorted_tables:
        lineas.append(f"    {tabla.name} {{")
        for columna in tabla.columns:
            marca = " PK" if columna.primary_key else (" FK" if columna.foreign_keys else "")
            lineas.append(f"        {_tipo_mermaid(columna)} {columna.name}{marca}")
        lineas.append("    }")
    lineas.append("```")
    return "\n".join(lineas)


def generar_clases() -> str:
    lineas = ["```mermaid", "classDiagram"]
    for tabla in Base.metadata.sorted_tables:
        nombre = "".join(p.capitalize() for p in tabla.name.split("_"))
        lineas.append(f"    class {nombre} {{")
        for columna in tabla.columns:
            visibilidad = "-"
            lineas.append(f"        {visibilidad}{_tipo_mermaid(columna)} {columna.name}")
        lineas.append("    }")
    clase = lambda t: "".join(p.capitalize() for p in t.split("_"))  # noqa: E731
    for origen, destino, _c, _t in RELACIONES:
        lineas.append(f'    {clase(origen)} "1" --> "0..*" {clase(destino)}')
    lineas.append("```")
    return "\n".join(lineas)


def generar_diccionario() -> str:
    md = [
        "# Diccionario de datos",
        "",
        "Generado automáticamente desde `backend/modelos/tablas_orm.py` con "
        "`docs/base_de_datos/generar_esquema.py`. No editar a mano.",
        "",
        "- Motor: PostgreSQL 16. La aplicación crea las tablas sola al arrancar; el script equivalente está en "
        "[base_completa.sql](base_completa.sql); para producción en Supabase, "
        "[base_supabase_produccion.sql](base_supabase_produccion.sql).",
        f"- Tablas: {len(Base.metadata.sorted_tables)} "
        f"({sum(1 for t in TABLAS.values() if t[1] == 'En uso')} en uso, "
        f"{sum(1 for t in TABLAS.values() if t[1] != 'En uso')} reservadas para sesiones de clase).",
        "- Sin `DATABASE_URL` configurada, la aplicación guarda las partidas en memoria y no usa estas tablas "
        "para ellas.",
        "",
        "## Relaciones",
        "",
        "| Origen | Destino | Cardinalidad | Significado |",
        "| :-- | :-- | :-: | :-- |",
    ]
    for origen, destino, card, texto in RELACIONES:
        md.append(f"| `{origen}` | `{destino}` | {card} | {texto} |")
    for tabla in Base.metadata.sorted_tables:
        descripcion, estado = TABLAS[tabla.name]
        md += ["", f"## `{tabla.name}`", "", descripcion, "", f"**Estado:** {estado}", ""]
        md += ["| Columna | Tipo | Nulo | Clave | Por defecto | Descripción |", "| :-- | :-- | :-: | :-- | :-- | :-- |"]
        for columna in tabla.columns:
            nulo = "no" if (not columna.nullable or columna.primary_key) else "sí"
            md.append(
                f"| `{columna.name}` | {_tipo_sql(columna)} | {nulo} | {_clave(columna)} | "
                f"{_valor_por_defecto(columna)} | {COLUMNAS[(tabla.name, columna.name)]} |"
            )
        restricciones = [
            f"`{c.name}` único ({', '.join(col.name for col in c.columns)})"
            for c in tabla.constraints
            if c.__class__.__name__ == "UniqueConstraint" and len(c.columns) > 1
        ]
        if restricciones:
            md += ["", "**Restricciones:** " + "; ".join(restricciones) + "."]
    md += [
        "",
        "## Diagrama entidad-relación (Mermaid)",
        "",
        generar_er(),
        "",
        "## Diagrama de clases de persistencia (Mermaid)",
        "",
        "Cada tabla es una clase ORM de `backend/modelos/tablas_orm.py`. Este diagrama cubre solo los datos; "
        "el diagrama de clases del documento debe sumar además las clases de dominio y de servicios "
        "(por ejemplo `Partida`, `EstrategiaJugada` y sus variantes, los repositorios y la fábrica de estrategias).",
        "",
        generar_clases(),
        "",
    ]
    return "\n".join(md)


def main() -> None:
    _validar_descripciones()
    (CARPETA / "base_completa.sql").write_text(generar_sql(), encoding="utf-8")
    (CARPETA / "base_supabase_produccion.sql").write_text(generar_sql_supabase(), encoding="utf-8")
    (CARPETA / "DICCIONARIO_DE_DATOS.md").write_text(generar_diccionario(), encoding="utf-8")
    print(f"Generado en {CARPETA}:")
    print("  - base_completa.sql")
    print("  - base_supabase_produccion.sql")
    print("  - DICCIONARIO_DE_DATOS.md")


if __name__ == "__main__":
    main()
