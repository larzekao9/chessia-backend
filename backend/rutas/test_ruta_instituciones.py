"""Perfiles de acceso (plataforma, administrador de institución, docente, jugador) e instituciones (SQLite en memoria)."""
import pytest

from backend.modelos.tablas_orm import InstitucionORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)


def test_dominio_institucional_exige_propiedad_del_correo_y_no_autoriza_por_si_solo(perfiles, monkeypatch):
    from backend.modelos.tablas_orm import UsuarioORM
    from backend.servicios.auth import servicio_verificacion

    cliente, personas, fabrica = perfiles
    institucion = _crear_institucion(cliente, personas)
    plataforma = personas["plataforma"]["headers"]
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=plataforma)
    assert cliente.patch(f"/instituciones/{institucion['id']}", json={"dominio_correo": "test.com"}, headers=plataforma).status_code == 200
    curso = cliente.post("/cursos", json={"nombre": "Curso institucional"}, headers=personas["docente"]["headers"]).json()
    with fabrica() as db:
        db.get(UsuarioORM, personas["alumno"]["id"]).edad = 10
        db.commit()
    enviados = []
    monkeypatch.setenv("SMTP_HOST", "correo.example.com")
    monkeypatch.setenv("SMTP_REMITENTE", "noreply@example.com")
    monkeypatch.setattr(servicio_verificacion, "_enviar_en_segundo_plano", lambda destino, nombre, codigo: enviados.append(codigo))
    servicio_verificacion._ultimo_envio.clear()
    headers = personas["alumno"]["headers"]
    intento = cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=headers)
    assert intento.status_code == 400 and "Verificá" in intento.json()["detail"]
    assert len(enviados) == 1
    assert cliente.post("/auth/verificar-correo", json={"codigo": enviados[0]}, headers=headers).status_code == 200
    assert cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=headers).status_code == 200
    assert cliente.get("/auth/me/autorizacion", headers=headers).json()["estado"] == "falta_tutor"
    cliente.patch(f"/cursos/{curso['id']}", json={"autorizacion_familias": True}, headers=personas["docente"]["headers"])
    assert cliente.get("/auth/me/autorizacion", headers=headers).json()["estado"] == "institucional"


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


# --- Aislamiento de partidas por institución -----------------------------------------------------------------------------------


def _partida_de(cliente, quien) -> str:
    return cliente.post("/partida", json={"nivel": 3}, headers=quien["headers"]).json()["id"]


@pytest.fixture
def aislado(perfiles):
    """Una institución con su director y su docente; un alumno en el curso del docente y otro alumno suelto, sin curso."""
    cliente, personas, _ = perfiles
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()
    cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=personas["alumno"]["headers"])
    registro = cliente.post("/auth/registro", json={"email": "suelto@test.com", "nombre": "Suelto Pérez", "password": "secreto1"}).json()
    personas["suelto"] = {"id": registro["usuario"]["id"], "headers": {"Authorization": f"Bearer {registro['tokens']['access_token']}"}}
    return cliente, personas


def test_un_docente_o_director_solo_ve_las_partidas_de_sus_alumnos(aislado) -> None:
    cliente, personas = aislado
    de_alumno, de_suelto = _partida_de(cliente, personas["alumno"]), _partida_de(cliente, personas["suelto"])

    def ve(quien, partida):
        return cliente.get(f"/partida/{partida}", headers=personas[quien]["headers"]).status_code

    assert (ve("docente", de_alumno), ve("director", de_alumno), ve("plataforma", de_alumno)) == (200, 200, 200)
    assert (ve("docente", de_suelto), ve("director", de_suelto)) == (403, 403)
    assert ve("plataforma", de_suelto) == 200
    assert ve("suelto", de_alumno) == 403  # un jugador sigue sin ver la de otro


def test_un_docente_no_toca_los_permisos_ni_el_analisis_de_partidas_ajenas(aislado) -> None:
    cliente, personas = aislado
    de_alumno, de_suelto = _partida_de(cliente, personas["alumno"]), _partida_de(cliente, personas["suelto"])
    docente = personas["docente"]["headers"]

    def permisos(partida):
        return cliente.patch(f"/partida/{partida}/permisos", json={"permite_camara": True}, headers=docente).status_code

    assert permisos(de_alumno) == 200 and permisos(de_suelto) == 403
    assert cliente.get(f"/partida/{de_suelto}/analisis-red", headers=docente).status_code == 403
    assert cliente.get(f"/partida/{de_alumno}/analisis-red", headers=docente).status_code != 403
    assert cliente.post(f"/partida/{de_suelto}/calibrar", headers=docente).status_code == 403


def test_el_registro_de_partidas_se_limita_a_los_alumnos_de_cada_facilitador(aislado, monkeypatch) -> None:
    from backend.modelos.partida import Partida

    monkeypatch.setattr(Partida, "jugadas_jugador", property(lambda self: 1))  # sin jugar de verdad: alcanza con que cuenten
    cliente, personas = aislado
    de_alumno, de_suelto = _partida_de(cliente, personas["alumno"]), _partida_de(cliente, personas["suelto"])

    def lista(quien):
        return {p["id"] for p in cliente.get("/partida", headers=personas[quien]["headers"]).json()}

    assert lista("plataforma") >= {de_alumno, de_suelto}
    assert lista("docente") == {de_alumno} and lista("director") == {de_alumno}
    assert lista("suelto") == {de_suelto}


def test_la_transmision_en_vivo_de_un_docente_llega_a_sus_alumnos_y_no_a_otros(aislado) -> None:
    cliente, personas = aislado
    transmision = _partida_de(cliente, personas["docente"])
    assert cliente.patch(f"/partida/{transmision}/permisos", json={"es_demostracion": True}, headers=personas["docente"]["headers"]).status_code == 200

    def activa(quien):
        return cliente.get("/partida/demostracion-activa", headers=personas[quien]["headers"]).json()

    assert activa("alumno")["id"] == transmision and activa("director")["id"] == transmision and activa("plataforma")["id"] == transmision
    assert activa("suelto") is None
    assert cliente.get(f"/partida/{transmision}", headers=personas["suelto"]["headers"]).status_code == 403
    assert cliente.get(f"/partida/{transmision}", headers=personas["alumno"]["headers"]).status_code == 200
