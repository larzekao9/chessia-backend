"""Alta de instituciones con suscripción, invitaciones por institución y modo evaluación (SQLite en memoria)."""
from datetime import datetime

import pytest

from backend.modelos.tablas_orm import UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.rutas.test_ruta_instituciones import perfiles  # noqa: F401  (fixture compartida)
from backend.servicios.suscripcion import servicio_suscripciones as suscripciones

AHORA = datetime(2026, 10, 25, 12, 0, 0)


@pytest.fixture
def entorno(perfiles, monkeypatch):
    monkeypatch.setattr(suscripciones, "_ahora", lambda: AHORA)
    return perfiles


def _crear(cliente, personas, **cuerpo):
    return cliente.post("/instituciones", json={"nombre": "Colegio Nuevo", "tipo": "colegio", **cuerpo}, headers=personas["plataforma"]["headers"])


def _registrar_con_codigo(cliente, email, codigo, nombre="Persona Nueva"):
    respuesta = cliente.post("/auth/registro", json={"email": email, "nombre": nombre, "password": "secreto1", "rol": "facilitador", "clave_facilitador": codigo})
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    return {"id": cuerpo["usuario"]["id"], "usuario": cuerpo["usuario"], "headers": {"Authorization": f"Bearer {cuerpo['tokens']['access_token']}"}}


def _yo(cliente, persona):
    return cliente.get("/auth/me", headers=persona["headers"]).json()


# --- alta con plan, prueba y pago ----------------------------------------------------------------------------------------------


def test_se_define_el_plan_y_los_dias_de_prueba_al_crear_la_institucion(entorno) -> None:
    cliente, personas, _ = entorno

    creada = _crear(cliente, personas, plan="plan_120", dias_de_prueba=15).json()

    assert (creada["suscripcion"]["plan"], creada["suscripcion"]["estado"], creada["suscripcion"]["dias_restantes"]) == ("plan_120", "prueba", 15)
    assert creada["codigo_administrador"] is None


def test_una_institucion_que_ya_pago_arranca_activa(entorno) -> None:
    cliente, personas, _ = entorno

    creada = _crear(cliente, personas, plan="plan_60", pago_inicial_ciclo="anual").json()

    assert creada["suscripcion"]["estado"] == "activa" and creada["suscripcion"]["vigente_hasta"].startswith("2027-10-25")
    pagos = cliente.get("/suscripciones/pagos", headers=personas["plataforma"]["headers"]).json()
    assert [(p["monto_bs"], p["estado"]) for p in pagos] == [(3500, "confirmado")]


def test_no_se_mezclan_un_administrador_existente_y_una_invitacion(entorno) -> None:
    cliente, personas, _ = entorno

    mezcla = _crear(cliente, personas, administrador_id=personas["docente"]["id"], invitar_administrador=True)
    prueba_con_pago = _crear(cliente, personas, es_prueba=True, pago_inicial_ciclo="mensual")
    prueba_con_admin = _crear(cliente, personas, es_prueba=True, administrador_id=personas["docente"]["id"])

    assert (mezcla.status_code, prueba_con_pago.status_code, prueba_con_admin.status_code) == (400, 400, 400)


def test_la_plataforma_invita_al_director_y_quien_se_registra_queda_como_administrador(entorno) -> None:
    cliente, personas, _ = entorno
    creada = _crear(cliente, personas, invitar_administrador=True).json()
    assert creada["codigo_administrador"] and creada["codigo_expira_en"]

    director = _registrar_con_codigo(cliente, "director.nuevo@colegio.com", creada["codigo_administrador"])
    yo = _yo(cliente, director)

    assert yo["es_admin_institucion"] is True and yo["institucion"]["id"] == creada["id"] and yo["tiene_vista_alternativa"] is False
    # la misma institución ya tiene administrador: no se invita a otro
    segundo = cliente.post(
        "/auth/codigos-facilitador", json={"institucion_id": creada["id"], "como_administrador": True}, headers=personas["plataforma"]["headers"]
    )
    assert segundo.status_code == 400


# --- el director invita a sus docentes ------------------------------------------------------------------------------------------


@pytest.fixture
def con_director(entorno):
    cliente, personas, fabrica = entorno
    institucion = cliente.post(
        "/instituciones", json={"nombre": "Academia Uno", "tipo": "academia", "administrador_id": personas["director"]["id"]}, headers=personas["plataforma"]["headers"]
    ).json()
    return cliente, personas, institucion


def test_el_director_invita_a_un_docente_y_queda_en_su_institucion(con_director) -> None:
    cliente, personas, institucion = con_director
    director = personas["director"]["headers"]

    generado = cliente.post("/auth/codigos-facilitador", json={"minutos": 4320, "para": "Prof. Gómez"}, headers=director)
    assert generado.status_code == 201 and generado.json()["institucion_id"] == institucion["id"] and generado.json()["como_administrador"] is False

    docente = _registrar_con_codigo(cliente, "gomez@colegio.com", generado.json()["codigo"], "Prof. Gómez")
    yo = _yo(cliente, docente)
    assert yo["institucion"]["id"] == institucion["id"] and yo["es_admin_institucion"] is False
    docentes = cliente.get("/instituciones/mia/docentes", headers=director).json()
    assert {d["nombre"] for d in docentes} >= {"Prof. Gómez"}


def test_el_director_solo_invita_docentes_de_su_propia_institucion(con_director) -> None:
    cliente, personas, institucion = con_director
    director = personas["director"]["headers"]
    otra = cliente.post("/instituciones", json={"nombre": "Otra Academia", "tipo": "academia"}, headers=personas["plataforma"]["headers"]).json()

    ajena = cliente.post("/auth/codigos-facilitador", json={"institucion_id": otra["id"]}, headers=director)
    admin = cliente.post("/auth/codigos-facilitador", json={"como_administrador": True}, headers=director)
    docente_comun = cliente.post("/auth/codigos-facilitador", json={}, headers=personas["docente"]["headers"])
    larga = cliente.post("/auth/codigos-facilitador", json={"minutos": 4320}, headers=personas["plataforma"]["headers"])

    assert (ajena.status_code, admin.status_code, docente_comun.status_code) == (403, 403, 403)
    assert larga.status_code == 400  # sin institución un código dura hasta 24 h


def test_cada_director_ve_y_anula_solo_los_codigos_de_su_institucion(con_director) -> None:
    cliente, personas, institucion = con_director
    director, plataforma = personas["director"]["headers"], personas["plataforma"]["headers"]
    propio = cliente.post("/auth/codigos-facilitador", json={"para": "Mío"}, headers=director).json()
    de_plataforma = cliente.post("/auth/codigos-facilitador", json={"para": "Sin institución"}, headers=plataforma).json()

    vistos = {c["para"] for c in cliente.get("/auth/codigos-facilitador", headers=director).json()}
    todos = {c["para"] for c in cliente.get("/auth/codigos-facilitador", headers=plataforma).json()}

    assert vistos == {"Mío"} and todos >= {"Mío", "Sin institución"}
    assert cliente.delete(f"/auth/codigos-facilitador/{de_plataforma['id']}", headers=director).status_code == 404
    assert cliente.delete(f"/auth/codigos-facilitador/{propio['id']}", headers=director).status_code == 204


# --- modo evaluación -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def evaluacion(entorno):
    """Una institución de prueba, su director ya registrado con el código y el cliente HTTP."""
    cliente, personas, fabrica = entorno
    creada = _crear(cliente, personas, nombre="Prueba Colegio Sol", es_prueba=True, dias_de_prueba=30, minutos_del_codigo=4320).json()
    director = _registrar_con_codigo(cliente, "director@colegio-sol.com", creada["codigo_administrador"], "Dora Directora")
    return cliente, personas, fabrica, creada, director


def test_el_enlace_muestra_a_que_se_invita_sin_gastar_el_codigo(entorno) -> None:
    cliente, personas, _ = entorno
    creada = _crear(cliente, personas, nombre="Prueba Colegio Sol", es_prueba=True).json()

    info = cliente.post("/auth/codigos-facilitador/verificar", json={"codigo": creada["codigo_administrador"]}).json()
    otra_vez = cliente.post("/auth/codigos-facilitador/verificar", json={"codigo": creada["codigo_administrador"]}).json()

    assert info["valido"] and info["institucion_nombre"] == "Prueba Colegio Sol" and info["como_administrador"] and info["es_evaluacion"]
    assert otra_vez["valido"]  # verificar no lo gasta


def test_quien_evalua_queda_de_director_de_una_institucion_de_prueba_con_un_jugador_vinculado(evaluacion) -> None:
    cliente, personas, fabrica, creada, director = evaluacion

    yo = _yo(cliente, director)

    assert yo["es_admin_institucion"] and yo["institucion"]["es_prueba"] is True and yo["tiene_vista_alternativa"] is True
    assert creada["suscripcion"]["estado"] == "prueba" and creada["suscripcion"]["dias_restantes"] == 30
    with fabrica() as db:
        facilitador = db.get(UsuarioORM, director["id"])
        jugador = db.get(UsuarioORM, facilitador.cuenta_vinculada_id)
        assert jugador.rol == "jugador" and jugador.password_hash is None and jugador.cuenta_vinculada_id == facilitador.id
        assert jugador.email.endswith("@chessia.invalid")


def test_el_boton_pasa_de_director_a_jugador_de_prueba_y_vuelve(evaluacion) -> None:
    cliente, personas, _, _, director = evaluacion

    como_jugador = cliente.post("/auth/cambiar-vista", headers=director["headers"]).json()
    cabeceras_jugador = {"Authorization": f"Bearer {como_jugador['tokens']['access_token']}"}
    assert como_jugador["usuario"]["rol"] == "jugador" and como_jugador["usuario"]["tiene_vista_alternativa"] is True

    de_vuelta = cliente.post("/auth/cambiar-vista", headers=cabeceras_jugador).json()
    assert de_vuelta["usuario"]["id"] == director["id"] and de_vuelta["usuario"]["rol"] == "facilitador"


def test_las_cuentas_sin_vista_alternativa_no_pueden_cambiar(entorno) -> None:
    cliente, personas, _ = entorno

    assert cliente.post("/auth/cambiar-vista", headers=personas["docente"]["headers"]).status_code == 400
    assert cliente.post("/auth/cambiar-vista").status_code == 401


def test_quien_evalua_prueba_el_lado_del_alumno_con_su_jugador(evaluacion) -> None:
    cliente, personas, _, creada, director = evaluacion
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez de prueba"}, headers=director["headers"]).json()
    como_jugador = cliente.post("/auth/cambiar-vista", headers=director["headers"]).json()
    cabeceras = {"Authorization": f"Bearer {como_jugador['tokens']['access_token']}"}

    unido = cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=cabeceras)

    assert unido.status_code == 200
    mia = cliente.get("/instituciones/mia", headers=director["headers"]).json()
    assert mia["alumnos"] == 1


def test_el_doble_factor_obligatorio_no_alcanza_a_quien_evalua_pero_si_al_cliente(evaluacion, monkeypatch) -> None:
    cliente, personas, _, creada, director = evaluacion
    monkeypatch.setenv("DOBLE_FACTOR_OBLIGATORIO", "1")

    assert _yo(cliente, director)["doble_factor_obligatorio"] is False
    assert cliente.get("/instituciones/mia", headers=director["headers"]).status_code == 200

    monkeypatch.delenv("DOBLE_FACTOR_OBLIGATORIO")  # la plataforma tampoco tiene doble factor activado en este test: convierte sin la exigencia
    assert cliente.post(f"/instituciones/{creada['id']}/convertir", json={}, headers=personas["plataforma"]["headers"]).status_code == 200
    monkeypatch.setenv("DOBLE_FACTOR_OBLIGATORIO", "1")

    assert _yo(cliente, director)["doble_factor_obligatorio"] is True


def test_convertir_en_cliente_quita_el_jugador_de_prueba_y_conserva_lo_armado(evaluacion) -> None:
    cliente, personas, fabrica, creada, director = evaluacion
    cliente.post("/cursos", json={"nombre": "Mi primer curso"}, headers=director["headers"])
    plataforma = personas["plataforma"]["headers"]

    convertida = cliente.post(
        f"/instituciones/{creada['id']}/convertir",
        json={"nombre": "Colegio Sol", "tipo": "colegio", "plan": "plan_120", "pago_inicial_ciclo": "trimestral"},
        headers=plataforma,
    )

    assert convertida.status_code == 200 and convertida.json()["nombre"] == "Colegio Sol" and convertida.json()["es_prueba"] is False
    yo = _yo(cliente, director)
    assert yo["tiene_vista_alternativa"] is False and yo["institucion"]["es_prueba"] is False and yo["es_admin_institucion"] is True
    assert cliente.post("/auth/cambiar-vista", headers=director["headers"]).status_code == 400  # el botón ya no existe
    assert cliente.get("/cursos", headers=director["headers"]).json()[0]["nombre"] == "Mi primer curso"  # lo que armó se conserva
    mia = cliente.get("/suscripciones/mia", headers=director["headers"]).json()
    assert (mia["estado"], mia["plan"], mia["ciclo"]) == ("activa", "plan_120", "trimestral")
    with fabrica() as db:
        assert db.query(UsuarioORM).filter(UsuarioORM.email.like("%@chessia.invalid")).count() == 0
    assert cliente.post(f"/instituciones/{creada['id']}/convertir", json={}, headers=plataforma).status_code == 400  # ya es cliente
    assert cliente.post(f"/instituciones/{creada['id']}/convertir", json={}, headers=director["headers"]).status_code == 403


def test_si_se_borra_la_cuenta_de_quien_evalua_tambien_se_borra_su_jugador_de_prueba(evaluacion) -> None:
    cliente, personas, fabrica, _, director = evaluacion

    baja = cliente.post("/auth/me/eliminar", json={"password": "secreto1"}, headers=director["headers"])

    assert baja.status_code == 200
    with fabrica() as db:
        assert db.query(UsuarioORM).filter(UsuarioORM.email.like("%@chessia.invalid")).count() == 0


# --- pago recibido por la plataforma -------------------------------------------------------------------------------------------


def test_la_plataforma_registra_un_pago_recibido_en_persona(entorno) -> None:
    cliente, personas, _ = entorno
    plataforma = personas["plataforma"]["headers"]
    creada = _crear(cliente, personas, plan="plan_60").json()

    registrado = cliente.post(f"/suscripciones/{creada['id']}/pago-registrado", json={"plan": "plan_60", "ciclo": "trimestral", "referencia": "Efectivo en la visita"}, headers=plataforma)

    assert registrado.status_code == 201 and (registrado.json()["estado"], registrado.json()["monto_bs"]) == ("confirmado", 945)
    lista = cliente.get("/suscripciones", headers=plataforma).json()
    assert next(s for s in lista if s["institucion_id"] == creada["id"])["estado"] == "activa"
    assert cliente.post(f"/suscripciones/{creada['id']}/pago-registrado", json={"plan": "plan_60", "ciclo": "mensual"}, headers=personas["docente"]["headers"]).status_code == 403
