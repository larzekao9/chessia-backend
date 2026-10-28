"""Pruebas de la migración automática de columnas (`crear_tablas`)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.pool import StaticPool

from backend.database import crear_fabrica_sesiones, crear_tablas, fecha_a_iso
from backend.modelos import tablas_orm  # noqa: F401  (registra las tablas en Base.metadata)
from backend.modelos.tablas_orm import UsuarioORM


def test_fecha_a_iso_de_datetime_naive_agrega_offset_utc() -> None:
    # Las columnas TIMESTAMP de este proyecto no llevan zona horaria: SQLAlchemy
    # siempre devuelve un `datetime` naive. `obtener_engine` fija la sesión de
    # Postgres a UTC, así que ese naive ES la hora UTC — hay que marcarlo así
    # antes de convertirlo a texto, o el frontend lo malinterpreta como hora
    # local (bug real: 4 horas de diferencia para un usuario en Bolivia).
    naive = datetime(2026, 9, 28, 4, 56, 28, 637925)

    resultado = fecha_a_iso(naive)

    assert resultado is not None
    assert resultado.endswith("+00:00")
    assert datetime.fromisoformat(resultado) == naive.replace(tzinfo=timezone.utc)


def test_fecha_a_iso_no_pisa_un_offset_que_ya_viene_puesto() -> None:
    con_offset = datetime(2026, 9, 28, 4, 56, 28, tzinfo=timezone(timedelta(hours=-4)))

    resultado = fecha_a_iso(con_offset)

    assert resultado == con_offset.isoformat()


def test_fecha_a_iso_de_none_es_none() -> None:
    assert fecha_a_iso(None) is None


def test_crear_tablas_limpia_nivel_estimado_de_facilitadores() -> None:
    # Bug real: un facilitador no tiene nivel de juego propio, pero antes de
    # que `PATCH /auth/nivel-estimado` empezara a rechazar el rol, algunas
    # cuentas de facilitador quedaron con `nivel_estimado`/`rango_estimado`
    # puestos (ver `ruta_auth.guardar_nivel_estimado`). `crear_tablas` corrige
    # eso solo, de forma idempotente, sin tocar a los jugadores.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    crear_tablas(engine)
    fabrica = crear_fabrica_sesiones(engine)
    with fabrica() as sesion:
        sesion.add(UsuarioORM(
            email="facilitador-viejo@x.com", nombre="Profe", password_hash="x", rol="facilitador",
            nivel_estimado=5, rango_estimado="Principiante",
        ))
        sesion.add(UsuarioORM(
            email="jugador-viejo@x.com", nombre="Jugador", password_hash="x", rol="jugador",
            nivel_estimado=12, rango_estimado="Intermedio",
        ))
        sesion.commit()

    crear_tablas(engine)  # segunda pasada: acá corre la limpieza sobre las filas de arriba

    with fabrica() as sesion:
        facilitador = sesion.scalar(select(UsuarioORM).where(UsuarioORM.email == "facilitador-viejo@x.com"))
        jugador = sesion.scalar(select(UsuarioORM).where(UsuarioORM.email == "jugador-viejo@x.com"))

    assert facilitador.nivel_estimado is None
    assert facilitador.rango_estimado is None
    assert jugador.nivel_estimado == 12
    assert jugador.rango_estimado == "Intermedio"


def test_crear_tablas_agrega_usa_brazo_a_base_vieja_de_partidas() -> None:
    # Una base creada antes de `partida.usa_brazo` (HU9) tiene que seguir sirviendo: sin la
    # columna, crear o listar cualquier partida con el ORM falla con un error 500.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conexion:
        conexion.execute(text("CREATE TABLE partida (id VARCHAR PRIMARY KEY)"))
        conexion.execute(text("INSERT INTO partida (id) VALUES ('vieja')"))

    crear_tablas(engine)

    columnas = {c["name"] for c in inspect(engine).get_columns("partida")}
    assert {"usa_brazo", "es_demostracion", "permite_camara", "permite_simulacion_3d"} <= columnas
    with engine.connect() as conexion:
        usa_brazo = conexion.execute(text("SELECT usa_brazo FROM partida WHERE id = 'vieja'")).scalar()
    assert not usa_brazo  # las partidas anteriores quedan sin brazo (FALSE)


def _cargar_generador_de_esquema():
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[1] / "docs" / "base_de_datos" / "generar_esquema.py"
    especificacion = importlib.util.spec_from_file_location("generar_esquema", ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_base_anterior_recibe_el_tema_dia_sin_perder_perfiles() -> None:
    motor = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    with motor.begin() as conexion:
        conexion.execute(text("CREATE TABLE usuario (id INTEGER PRIMARY KEY, email VARCHAR, nombre VARCHAR, password_hash VARCHAR, rol VARCHAR, creado_en DATETIME, activo BOOLEAN)"))
        conexion.execute(text("INSERT INTO usuario (id, email, nombre, password_hash, rol, activo) VALUES (1, 'anterior@example.com', 'Alumno', 'hash', 'jugador', 1)"))
    crear_tablas(motor)
    crear_tablas(motor)  # El segundo arranque no vuelve a agregar ni sobrescribir la preferencia.
    with motor.connect() as conexion:
        fila = conexion.execute(text("SELECT nombre, tema FROM usuario WHERE id = 1")).one()
    assert fila.nombre == "Alumno" and fila.tema == "dia"


def test_diccionario_de_datos_describe_todas_las_tablas_y_columnas() -> None:
    # Si se agrega una tabla o columna al modelo sin describirla en
    # docs/base_de_datos/generar_esquema.py, el script sale con error y esta prueba falla:
    # así el diccionario de datos del documento no queda desactualizado.
    generador = _cargar_generador_de_esquema()

    generador._validar_descripciones()

    sql = generador.generar_sql()
    for tabla in ("usuario", "partida", "jugada", "mensaje_tutor", "calibracion"):
        assert f"CREATE TABLE {tabla} " in sql
    assert "COMMENT ON COLUMN partida.usa_brazo" in sql


def test_no_siembra_usuarios_de_prueba_en_supabase_salvo_que_se_pida(monkeypatch) -> None:
    from sqlalchemy.engine import make_url

    from backend.database import sembrado_de_usuarios_de_prueba_habilitado

    class _Motor:  # solo necesita la URL; no se conecta a nada
        def __init__(self, url: str) -> None:
            self.url = make_url(url)

    supabase = _Motor("postgresql+psycopg2://postgres.abc:clave@aws-0-us-east-1.pooler.supabase.com:5432/postgres")
    directa = _Motor("postgresql+psycopg2://postgres:clave@db.abc.supabase.co:5432/postgres")
    local = _Motor("postgresql+psycopg2://postgres:clave@localhost:5432/ajedrez")
    sqlite = _Motor("sqlite:///:memory:")

    monkeypatch.delenv("SEMBRAR_USUARIOS_PRUEBA", raising=False)
    assert not sembrado_de_usuarios_de_prueba_habilitado(supabase)
    assert not sembrado_de_usuarios_de_prueba_habilitado(directa)
    assert sembrado_de_usuarios_de_prueba_habilitado(local)  # desarrollo: sin cambios
    assert sembrado_de_usuarios_de_prueba_habilitado(sqlite)

    monkeypatch.setenv("SEMBRAR_USUARIOS_PRUEBA", "true")  # la variable explícita siempre manda
    assert sembrado_de_usuarios_de_prueba_habilitado(supabase)
    monkeypatch.setenv("SEMBRAR_USUARIOS_PRUEBA", "false")
    assert not sembrado_de_usuarios_de_prueba_habilitado(local)


def test_el_sql_de_produccion_no_trae_usuarios_ni_claves_de_prueba() -> None:
    generador = _cargar_generador_de_esquema()

    sql = generador.generar_sql_supabase()

    assert "IF NOT EXISTS" in sql and "ENABLE ROW LEVEL SECURITY" in sql
    assert "INSERT INTO" not in sql
    assert "admin123" not in sql and "test123456" not in sql


def test_los_sql_y_el_diccionario_estan_al_dia_con_el_modelo() -> None:
    # Regla del proyecto: cada cambio en backend/modelos/tablas_orm.py obliga a regenerar la base
    # completa, la de producción (Supabase) y el diccionario de datos. Esta prueba falla si se
    # cambió el modelo y no se volvió a generar. Se compara sin espacios para no depender de
    # detalles de formato entre versiones de SQLAlchemy ni de saltos de línea del sistema.
    generador = _cargar_generador_de_esquema()

    def normalizado(texto: str) -> str:
        return " ".join(texto.split())

    esperados = {
        "base_completa.sql": generador.generar_sql(),
        "base_supabase_produccion.sql": generador.generar_sql_supabase(),
        "DICCIONARIO_DE_DATOS.md": generador.generar_diccionario(),
    }
    for nombre, esperado in esperados.items():
        actual = (generador.CARPETA / nombre).read_text(encoding="utf-8")
        assert normalizado(actual) == normalizado(esperado), (
            f"docs/base_de_datos/{nombre} está desactualizado respecto al modelo. "
            "Ejecuta: python docs/base_de_datos/generar_esquema.py"
        )
