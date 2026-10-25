"""Perfiles de acceso (plataforma, administrador de institución, docente, jugador) e instituciones (SQLite en memoria)."""
import pytest

from backend.modelos.tablas_orm import InstitucionORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)


@pytest.fixture
def perfiles(contexto, monkeypatch):
    """Una persona de cada perfil. Los facilitadores se registran con `*@test.com` autorizado y después solo `plataforma@test.com`
    queda como principal, así los demás facilitadores pasan a ser docentes comunes."""
    cliente, registrar, fabrica = contexto
    personas = {
        "plataforma": registrar("plataforma@test.com", "facilitador"),
        "docente": registrar("docente@test.com", "facilitador"),
        "director": registrar("director@test.com", "facilitador"),
        "alumno": registrar("alumno@test.com"),
    }
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")
    return cliente, personas, fabrica


def _crear_institucion(cliente, personas, nombre="Asociación de Ajedrez", **extra) -> dict:
    respuesta = cliente.post(
        "/instituciones", json={"nombre": nombre, "tipo": "academia", **extra}, headers=personas["plataforma"]["headers"]
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _yo(cliente, persona) -> dict:
    return cliente.get("/auth/me", headers=persona["headers"]).json()


RUTAS_SOLO_PLATAFORMA = [
    ("post", "/auth/codigos-facilitador"),
    ("get", "/auth/codigos-facilitador"),
    ("get", "/brazo/estado"),
    ("get", "/brazo/control/estado"),
    ("get", "/entrenamiento/estado"),
    ("get", "/vision/camaras/estado"),
    ("get", "/instituciones"),
]


@pytest.mark.parametrize("metodo,ruta", RUTAS_SOLO_PLATAFORMA)
def test_lo_que_toca_el_hardware_o_a_todos_los_usuarios_es_solo_de_la_plataforma(perfiles, metodo, ruta) -> None:
    cliente, personas, _ = perfiles
    llamar = getattr(cliente, metodo)
    cuerpo = {"json": {}} if metodo == "post" else {}

    assert llamar(ruta, headers=personas["plataforma"]["headers"], **cuerpo).status_code != 403
    for quien in ("docente", "director", "alumno"):
        assert llamar(ruta, headers=personas[quien]["headers"], **cuerpo).status_code == 403, (ruta, quien)
    assert llamar(ruta, **cuerpo).status_code == 401


def test_cada_perfil_ve_solo_a_sus_alumnos(perfiles) -> None:
    cliente, personas, _ = perfiles
    plataforma = personas["plataforma"]["headers"]
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=plataforma)
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()
    cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=personas["alumno"]["headers"])

    def ids(quien):
        return {u["id"] for u in cliente.get("/auth/usuarios", headers=personas[quien]["headers"]).json()}

    todos = ids("plataforma")
    assert {p["id"] for p in personas.values()} <= todos
    assert ids("docente") == {personas["alumno"]["id"]}  # su curso
    assert ids("director") == {personas["alumno"]["id"]}  # los cursos de sus docentes
    assert cliente.get("/auth/usuarios", headers=personas["alumno"]["headers"]).status_code == 403

    alumno_id = personas["alumno"]["id"]
    assert cliente.get(f"/auth/usuarios/{alumno_id}/historial-partidas", headers=personas["docente"]["headers"]).status_code == 200
    ajeno = cliente.get(f"/auth/usuarios/{personas['plataforma']['id']}/historial-partidas", headers=personas["docente"]["headers"])
    assert ajeno.status_code == 403


def test_solo_la_plataforma_puede_prender_el_brazo_de_una_partida(perfiles) -> None:
    cliente, personas, _ = perfiles

    asi = cliente.patch("/partida/x/permisos", json={"usa_brazo": True}, headers=personas["docente"]["headers"])
    assert asi.status_code == 403
    # un permiso educativo común sigue siendo del docente (llega a buscar la partida: 404 porque no existe)
    comun = cliente.patch("/partida/x/permisos", json={"permite_camara": True}, headers=personas["docente"]["headers"])
    assert comun.status_code == 404


def test_la_plataforma_arma_una_institucion_con_su_administrador_y_sus_docentes(perfiles) -> None:
    cliente, personas, _ = perfiles
    plataforma = personas["plataforma"]["headers"]

    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    assert institucion["administrador_id"] == personas["director"]["id"] and institucion["cantidad_docentes"] == 1

    agregada = cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=plataforma).json()
    assert agregada["cantidad_docentes"] == 2
    assert {d["nombre"] for d in agregada["docentes"]} == {"Persona Prueba"}
    assert [d["es_administrador"] for d in agregada["docentes"]].count(True) == 1

    assert cliente.get("/instituciones", headers=plataforma).json()[0]["id"] == institucion["id"]
    # un jugador no puede ser docente ni administrador
    assert cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['alumno']['id']}", headers=plataforma).status_code == 400


def test_el_menu_de_cada_perfil_sale_de_lo_que_devuelve_auth_me(perfiles) -> None:
    cliente, personas, _ = perfiles
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])

    plataforma, director, docente, alumno = (_yo(cliente, personas[q]) for q in ("plataforma", "director", "docente", "alumno"))

    assert plataforma["es_principal"] is True and plataforma["institucion"] is None
    assert director["es_principal"] is False and director["es_admin_institucion"] is True
    assert director["institucion"]["nombre"] == "Asociación de Ajedrez"
    assert docente["es_principal"] is False and docente["es_admin_institucion"] is False and docente["institucion"]["id"] == institucion["id"]
    assert alumno["es_principal"] is False and alumno["es_admin_institucion"] is False and alumno["institucion"] is None


def test_el_docente_ve_su_institucion_y_solo_el_administrador_ve_la_lista_de_docentes(perfiles) -> None:
    cliente, personas, _ = perfiles
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])

    del_docente = cliente.get("/instituciones/mia", headers=personas["docente"]["headers"]).json()
    assert del_docente["nombre"] == "Asociación de Ajedrez" and del_docente["docentes"] is None and del_docente["es_administrador"] is False
    assert cliente.get("/instituciones/mia", headers=personas["director"]["headers"]).json()["es_administrador"] is True

    assert len(cliente.get("/instituciones/mia/docentes", headers=personas["director"]["headers"]).json()) == 2
    assert cliente.get("/instituciones/mia/docentes", headers=personas["docente"]["headers"]).status_code == 403
    assert cliente.get("/instituciones/mia", headers=personas["alumno"]["headers"]).status_code == 403
    assert cliente.get("/instituciones/mia", headers=personas["plataforma"]["headers"]).status_code == 404  # la plataforma no tiene institución


def test_los_cursos_y_alumnos_de_la_institucion_salen_de_sus_docentes(perfiles) -> None:
    cliente, personas, _ = perfiles
    institucion = _crear_institucion(cliente, personas)
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])

    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()
    cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=personas["alumno"]["headers"])

    visto = cliente.get("/instituciones/mia", headers=personas["docente"]["headers"]).json()
    assert (visto["cursos"], visto["alumnos"], visto["cantidad_docentes"]) == (1, 1, 1)


def test_el_administrador_ve_los_alumnos_de_los_cursos_de_sus_docentes(perfiles) -> None:
    cliente, personas, _ = perfiles
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()
    cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=personas["alumno"]["headers"])

    alumnos = cliente.get("/instituciones/mia/alumnos", headers=personas["director"]["headers"]).json()
    assert [(a["id"], a["curso"]) for a in alumnos] == [(personas["alumno"]["id"], "Ajedrez 1ro A")]
    assert alumnos[0]["docente"] == "Persona Prueba"
    assert cliente.get("/instituciones/mia/alumnos", headers=personas["docente"]["headers"]).status_code == 403
    assert cliente.get("/instituciones/mia/alumnos", headers=personas["alumno"]["headers"]).status_code == 403


def test_no_se_repite_el_nombre_ni_se_acepta_un_tipo_invalido(perfiles) -> None:
    cliente, personas, _ = perfiles
    plataforma = personas["plataforma"]["headers"]
    _crear_institucion(cliente, personas, nombre="Colegio San Martín")

    repetido = cliente.post("/instituciones", json={"nombre": "colegio san martín", "tipo": "colegio"}, headers=plataforma)
    assert repetido.status_code == 409
    assert cliente.post("/instituciones", json={"nombre": "Otra", "tipo": "club"}, headers=plataforma).status_code == 422


def test_quitar_al_administrador_o_borrar_su_cuenta_deja_la_institucion_sin_administrador(perfiles) -> None:
    cliente, personas, fabrica = perfiles
    plataforma = personas["plataforma"]["headers"]
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])

    quitado = cliente.delete(f"/instituciones/{institucion['id']}/docentes/{personas['director']['id']}", headers=plataforma).json()
    assert quitado["administrador_id"] is None and quitado["cantidad_docentes"] == 0
    assert _yo(cliente, personas["director"])["es_admin_institucion"] is False

    cliente.patch(f"/instituciones/{institucion['id']}", json={"administrador_id": personas["director"]["id"]}, headers=plataforma)
    assert cliente.delete(f"/auth/usuarios/{personas['director']['id']}", headers=plataforma).status_code == 200
    with fabrica() as db:
        assert db.get(InstitucionORM, institucion["id"]).administrador_id is None


def test_un_usuario_pertenece_a_una_sola_institucion(perfiles) -> None:
    cliente, personas, _ = perfiles
    plataforma = personas["plataforma"]["headers"]
    primera = _crear_institucion(cliente, personas, nombre="Primera", administrador_id=personas["director"]["id"])
    segunda = _crear_institucion(cliente, personas, nombre="Segunda")

    cliente.put(f"/instituciones/{segunda['id']}/docentes/{personas['director']['id']}", headers=plataforma)

    anterior = [i for i in cliente.get("/instituciones", headers=plataforma).json() if i["id"] == primera["id"]][0]
    assert anterior["cantidad_docentes"] == 0 and anterior["administrador_id"] is None  # al mudarse, la primera queda sin administrador
    assert _yo(cliente, personas["director"])["institucion"]["id"] == segunda["id"]
