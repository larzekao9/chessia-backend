"""Aula virtual: menores y autorización, modo clase, tablas, bloquear y denunciar, cuentas de alumnos, avisos, tareas y torneos (SQLite en memoria)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.modelos.tablas_orm import AmistadORM, CalibracionORM, UsuarioORM
from backend.repositorios.repositorio_partida import RepositorioPartidasPostgres
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.partida import servicio_partida
from backend.servicios.sala import presencia_sala, servicio_sala
from backend.servicios.social import amigos, emparejamiento, en_linea, moderacion, retos, torneos

CINCO_MIN = 300_000


@pytest.fixture
def aula(contexto, monkeypatch):
    """Un docente con un curso donde están `ana`, `beto`, `caro` y `dani` (adultos) y `chico` (menor sin autorización); `eli` queda sin curso.
    Devuelve `(cliente, gente, fabrica, curso)`."""
    cliente, registrar, fabrica = contexto
    repo = RepositorioPartidasPostgres(fabrica)
    monkeypatch.setattr(servicio_sala, "_repositorio", repo)
    monkeypatch.setattr(servicio_partida, "_repositorio", repo)
    for memoria in (retos._retos, emparejamiento._cola, en_linea._visto, amigos._solicitudes_recientes, moderacion._denuncias_recientes, presencia_sala._ultima_senal):
        memoria.clear()
    servicio_sala._locks_partida.clear()
    servicio_sala._ofertas_de_tablas.clear()
    servicio_sala._ultima_oferta.clear()

    gente = {
        "docente": registrar("docente@test.com", "facilitador"),
        "otro_docente": registrar("otrodocente@test.com", "facilitador"),
        "plataforma": registrar("plataforma@test.com", "facilitador"),
        **{nombre: registrar(f"{nombre}@test.com") for nombre in ("ana", "beto", "caro", "dani", "chico", "eli")},
    }
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")
    with fabrica() as db:
        db.get(UsuarioORM, gente["chico"]["id"]).edad = 10
        db.commit()
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=gente["docente"]["headers"]).json()
    for nombre in ("ana", "beto", "caro", "dani", "chico"):
        assert cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=gente[nombre]["headers"]).status_code in (200, 201)
    return cliente, gente, fabrica, curso


def _h(gente, nombre):
    return gente[nombre]["headers"]


def _avisos(cliente, gente, nombre):
    return cliente.get("/social/avisos", headers=_h(gente, nombre))


def _retar(cliente, gente, de, a, **cuerpo):
    return cliente.post("/social/retos", json={"retado_id": gente[a]["id"], **cuerpo}, headers=_h(gente, de))


def _partida(cliente, gente, blancas="ana", negras="beto", reloj=CINCO_MIN):
    """Una sala en curso entre dos jugadores adultos: devuelve el código."""
    codigo = cliente.post("/sala", json={"color": "blancas", "control_tiempo_ms": reloj}, headers=_h(gente, blancas)).json()["codigo"]
    cliente.post(f"/sala/{codigo}/unirse", headers=_h(gente, negras))
    return codigo


# --- menores y autorización -----------------------------------------------------------------------------------------------------------


def test_un_menor_sin_autorizacion_no_juega_con_otros_pero_se_le_explica_que_le_falta(aula) -> None:
    cliente, gente, _, _ = aula

    estado = cliente.get("/auth/me/autorizacion", headers=_h(gente, "chico")).json()
    avisos = _avisos(cliente, gente, "chico").json()

    assert (estado["estado"], estado["puede_jugar_con_otros"], estado["falta"], estado["es_menor"]) == ("falta_tutor", False, ["tutor"], True)
    assert avisos["restriccion"]["motivo"] == "autorizacion" and avisos["restriccion"]["falta"] == ["tutor"]
    assert _retar(cliente, gente, "chico", "ana").status_code == 403
    assert cliente.post("/sala", json={}, headers=_h(gente, "chico")).status_code == 403
    assert cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "chico")).status_code == 403
    assert cliente.post("/social/amigos/solicitudes", json={"usuario_id": gente["ana"]["id"]}, headers=_h(gente, "chico")).status_code == 403
    assert _retar(cliente, gente, "ana", "chico").status_code == 409  # y a él tampoco se lo puede retar: todavía no puede jugar con otros
    assert cliente.get("/auth/me/autorizacion", headers=_h(gente, "ana")).json()["estado"] == "adulto"
    assert _avisos(cliente, gente, "ana").json()["restriccion"] is None


def test_el_menor_se_autoriza_cargando_los_datos_de_su_tutor(aula) -> None:
    cliente, gente, _, _ = aula
    incompleto = cliente.patch("/auth/me", json={"tutor_nombre": "María Pérez", "autorizacion_tutor": True}, headers=_h(gente, "chico"))
    telefono_malo = cliente.patch("/auth/me", json={"tutor_nombre": "María Pérez", "tutor_telefono": "abc", "autorizacion_tutor": True}, headers=_h(gente, "chico"))
    assert (incompleto.status_code, telefono_malo.status_code) == (400, 400)
    assert cliente.get("/auth/me/autorizacion", headers=_h(gente, "chico")).json()["estado"] == "falta_tutor"

    ok = cliente.patch("/auth/me", json={"tutor_nombre": " María   Pérez ", "tutor_telefono": "+591 7123-4567", "autorizacion_tutor": True}, headers=_h(gente, "chico"))

    assert ok.status_code == 200 and ok.json()["tutor_telefono"] == "+59171234567" and ok.json()["tutor_nombre"] == "María Pérez"
    estado = cliente.get("/auth/me/autorizacion", headers=_h(gente, "chico")).json()
    assert (estado["estado"], estado["puede_jugar_con_otros"]) == ("familia", True)
    assert _retar(cliente, gente, "chico", "ana").status_code == 201


def test_si_el_docente_declara_la_autorizacion_de_las_familias_los_alumnos_del_curso_quedan_cubiertos(aula) -> None:
    cliente, gente, _, curso = aula
    assert cliente.patch(f"/cursos/{curso['id']}", json={"autorizacion_familias": True}, headers=_h(gente, "otro_docente")).status_code == 404  # solo su docente

    curso_nuevo = cliente.patch(f"/cursos/{curso['id']}", json={"autorizacion_familias": True}, headers=_h(gente, "docente")).json()

    assert curso_nuevo["autorizacion_familias"] is True and curso_nuevo["autorizacion_familias_en"]
    assert cliente.get("/auth/me/autorizacion", headers=_h(gente, "chico")).json()["estado"] == "institucional"
    assert _retar(cliente, gente, "chico", "ana").status_code == 201
    estudiantes = cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=_h(gente, "docente")).json()
    assert {e["nombre"]: e["autorizado"] for e in estudiantes}["Persona Prueba"] is True
    cliente.patch(f"/cursos/{curso['id']}", json={"autorizacion_familias": False}, headers=_h(gente, "docente"))
    assert cliente.get("/auth/me/autorizacion", headers=_h(gente, "chico")).json()["estado"] == "falta_tutor"


def test_sin_edad_cargada_tampoco_se_juega_con_otros_hasta_completarla(aula) -> None:
    cliente, gente, fabrica, _ = aula
    with fabrica() as db:
        db.get(UsuarioORM, gente["eli"]["id"]).edad = None
        db.commit()

    estado = cliente.get("/auth/me/autorizacion", headers=_h(gente, "eli")).json()
    assert (estado["estado"], estado["falta"], estado["es_menor"]) == ("falta_edad", ["edad"], None)
    assert cliente.post("/sala", json={}, headers=_h(gente, "eli")).status_code == 403

    cliente.patch("/auth/me", json={"edad": 25}, headers=_h(gente, "eli"))
    assert cliente.get("/auth/me/autorizacion", headers=_h(gente, "eli")).json()["estado"] == "adulto"
    assert cliente.patch("/auth/me", json={"edad": 2}, headers=_h(gente, "eli")).status_code == 400


def test_el_registro_guarda_la_edad_y_el_tutor_y_revisa_los_datos_antes_de_crear_la_cuenta(aula) -> None:
    cliente, gente, fabrica, _ = aula
    base = {"nombre": "Lucía Gómez", "password": "secreto12", "rol": "jugador"}

    malo = cliente.post("/auth/registro", json={**base, "email": "lucia@test.com", "edad": 9, "tutor_nombre": "Rosa", "tutor_telefono": "12", "autorizacion_tutor": True})
    sin_datos = cliente.post("/auth/registro", json={**base, "email": "lucia@test.com", "edad": 9, "autorizacion_tutor": True})
    bueno = cliente.post("/auth/registro", json={**base, "email": "lucia@test.com", "edad": 9, "tutor_nombre": "Rosa Gómez", "tutor_telefono": "71234567", "autorizacion_tutor": True})

    assert (malo.status_code, sin_datos.status_code, bueno.status_code) == (400, 400, 201)  # los dos errores no dejaron una cuenta a medias
    usuario = bueno.json()["usuario"]
    assert (usuario["edad"], usuario["es_menor"], usuario["tutor_nombre"], usuario["tutor_telefono"]) == (9, True, "Rosa Gómez", "71234567")
    token = {"Authorization": f"Bearer {bueno.json()['tokens']['access_token']}"}
    assert cliente.get("/auth/me/autorizacion", headers=token).json()["estado"] == "familia"


def test_el_docente_carga_la_edad_y_el_contacto_de_la_familia_de_un_alumno(aula) -> None:
    cliente, gente, _, curso = aula
    ruta = f"/cursos/{curso['id']}/estudiantes/{gente['chico']['id']}/familia"

    ok = cliente.patch(ruta, json={"edad": 11, "tutor_nombre": "Rosa Gómez", "tutor_telefono": "71234567"}, headers=_h(gente, "docente"))

    assert ok.status_code == 200 and (ok.json()["edad"], ok.json()["tutor_telefono"], ok.json()["autorizado"]) == (11, "71234567", False)  # tener el contacto no es tener la autorización
    assert cliente.patch(ruta, json={"tutor_telefono": "xx"}, headers=_h(gente, "docente")).status_code == 400
    assert cliente.patch(ruta, json={"edad": 11}, headers=_h(gente, "otro_docente")).status_code == 404
    assert cliente.patch(f"/cursos/{curso['id']}/estudiantes/{gente['eli']['id']}/familia", json={"edad": 11}, headers=_h(gente, "docente")).status_code == 404  # no es de este curso


# --- modo clase --------------------------------------------------------------------------------------------------------------------------


def test_el_docente_pausa_los_retos_del_curso_y_despues_los_reanuda(aula) -> None:
    cliente, gente, _, curso = aula
    reto = _retar(cliente, gente, "ana", "beto").json()
    assert len(_avisos(cliente, gente, "beto").json()["retos_recibidos"]) == 1
    cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "caro"))

    pausado = cliente.patch(f"/cursos/{curso['id']}", json={"permite_retos": False}, headers=_h(gente, "docente"))

    assert pausado.status_code == 200 and pausado.json()["permite_retos"] is False
    assert _avisos(cliente, gente, "beto").json()["retos_recibidos"] == []  # lo que estaba pendiente se canceló
    assert cliente.get(f"/sala/{reto['codigo']}", headers=_h(gente, "ana")).status_code == 404
    assert _avisos(cliente, gente, "ana").json()["restriccion"]["motivo"] == "pausa"
    assert _avisos(cliente, gente, "caro").json()["busqueda"] is None  # y la búsqueda de rival también
    assert _retar(cliente, gente, "ana", "beto").status_code == 403
    assert cliente.post("/sala", json={}, headers=_h(gente, "ana")).status_code == 403
    assert cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "caro")).status_code == 403
    assert _avisos(cliente, gente, "eli").json()["restriccion"] is None  # quien no es del curso no se ve afectado

    cliente.patch(f"/cursos/{curso['id']}", json={"permite_retos": True}, headers=_h(gente, "docente"))
    assert _retar(cliente, gente, "ana", "beto").status_code == 201


# --- tablas --------------------------------------------------------------------------------------------------------------------------------


def test_las_tablas_se_ofrecen_y_al_aceptarlas_la_partida_termina_empatada(aula) -> None:
    cliente, gente, _, _ = aula
    codigo = _partida(cliente, gente)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=_h(gente, "ana"))

    ofrecidas = cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "beto")).json()
    assert ofrecidas["tablas_ofrecidas_por"] == "negras" and ofrecidas["terminada"] is False
    assert cliente.get(f"/sala/{codigo}", headers=_h(gente, "ana")).json()["tablas_ofrecidas_por"] == "negras"
    assert cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "beto")).status_code == 400  # ya ofreció

    aceptada = cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "ana")).json()

    assert (aceptada["terminada"], aceptada["resultado"], aceptada["motivo_fin"]) == (True, "1/2-1/2", "acuerdo")


def test_una_oferta_de_tablas_se_rechaza_caduca_con_una_jugada_y_no_se_repite_enseguida(aula) -> None:
    cliente, gente, _, _ = aula
    codigo = _partida(cliente, gente)
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e2e4"}, headers=_h(gente, "ana"))
    cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "beto"))

    rechazada = cliente.post(f"/sala/{codigo}/tablas/rechazar", headers=_h(gente, "ana")).json()
    assert rechazada["tablas_ofrecidas_por"] is None and rechazada["estado"] == "en_curso"
    assert cliente.post(f"/sala/{codigo}/tablas/rechazar", headers=_h(gente, "ana")).status_code == 400  # ya no hay oferta
    assert cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "beto")).status_code == 400  # hay que esperar unas jugadas

    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "e7e5"}, headers=_h(gente, "beto"))
    cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "ana"))  # ana ofrece
    cliente.post(f"/sala/{codigo}/mover", json={"jugada": "g1f3"}, headers=_h(gente, "ana"))  # y juega: su propia oferta caduca
    assert cliente.get(f"/sala/{codigo}", headers=_h(gente, "beto")).json()["tablas_ofrecidas_por"] is None
    assert cliente.post(f"/sala/{codigo}/tablas", headers=_h(gente, "caro")).status_code == 403  # un espectador no ofrece ni responde


# --- bloquear y denunciar ------------------------------------------------------------------------------------------------------------------


def test_bloquear_oculta_al_otro_cancela_los_retos_y_se_puede_deshacer(aula) -> None:
    cliente, gente, _, _ = aula
    reto = _retar(cliente, gente, "beto", "ana").json()
    assert len(_avisos(cliente, gente, "ana").json()["retos_recibidos"]) == 1

    assert cliente.post(f"/social/bloqueos/{gente['beto']['id']}", headers=_h(gente, "ana")).status_code == 204

    assert _avisos(cliente, gente, "ana").json()["retos_recibidos"] == []
    assert cliente.get(f"/sala/{reto['codigo']}", headers=_h(gente, "beto")).status_code == 404
    assert gente["beto"]["id"] not in {p["id"] for p in cliente.get("/social/amigos", headers=_h(gente, "ana")).json()["gente"]}
    assert gente["ana"]["id"] not in {p["id"] for p in cliente.get("/social/amigos", headers=_h(gente, "beto")).json()["gente"]}  # él tampoco la ve
    assert _retar(cliente, gente, "beto", "ana").status_code == 403  # sin decirle por qué
    assert [p["id"] for p in cliente.get("/social/bloqueos", headers=_h(gente, "ana")).json()] == [gente["beto"]["id"]]
    assert cliente.get("/social/bloqueos", headers=_h(gente, "beto")).json() == []  # quien fue bloqueado no se entera
    assert cliente.delete(f"/social/bloqueos/{gente['ana']['id']}", headers=_h(gente, "beto")).status_code == 204  # beto no puede levantarle el bloqueo a ana
    assert _retar(cliente, gente, "beto", "ana").status_code == 403

    cliente.delete(f"/social/bloqueos/{gente['beto']['id']}", headers=_h(gente, "ana"))
    assert _retar(cliente, gente, "beto", "ana").status_code == 201


def test_un_bloqueo_pisa_la_amistad_y_no_se_levanta_quitando_al_amigo(aula) -> None:
    cliente, gente, fabrica, _ = aula
    cliente.post("/social/amigos/solicitudes", json={"email": "eli@test.com"}, headers=_h(gente, "ana"))
    solicitud = cliente.get("/social/amigos", headers=_h(gente, "eli")).json()["solicitudes_recibidas"][0]
    cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=_h(gente, "eli"))

    cliente.post(f"/social/bloqueos/{gente['eli']['id']}", headers=_h(gente, "ana"))

    with fabrica() as db:
        assert [(f.solicitante_id, f.estado) for f in db.query(AmistadORM)] == [(gente["ana"]["id"], "bloqueada")]
    assert cliente.delete(f"/social/amigos/{gente['ana']['id']}", headers=_h(gente, "eli")).status_code == 204
    with fabrica() as db:
        assert db.query(AmistadORM).count() == 1  # el bloqueo sigue
    assert cliente.post("/social/amigos/solicitudes", json={"email": "ana@test.com"}, headers=_h(gente, "eli")).status_code == 202
    with fabrica() as db:
        assert [f.estado for f in db.query(AmistadORM)] == ["bloqueada"]  # no se pudo saltear el bloqueo con una solicitud


def test_dos_jugadores_bloqueados_no_se_emparejan_entre_si(aula, monkeypatch) -> None:
    cliente, gente, fabrica, _ = aula
    for nombre in ("ana", "beto", "caro"):
        with fabrica() as db:
            db.get(UsuarioORM, gente[nombre]["id"]).nivel_estimado = 5
            db.commit()
    cliente.post(f"/social/bloqueos/{gente['beto']['id']}", headers=_h(gente, "ana"))
    cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "ana"))

    de_beto = cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "beto"))
    de_caro = cliente.post("/social/emparejamiento", json={}, headers=_h(gente, "caro"))

    assert de_beto.json()["estado"] == "buscando"  # no cayó con ana
    assert de_caro.json()["estado"] == "encontrado"  # caro sí, y se llevó a ana


def test_cada_jugador_conserva_su_propio_bloqueo_aunque_el_otro_bloquee_y_desbloquee(aula):
    cliente, gente, _, _ = aula
    ruta_a_b = f"/social/bloqueos/{gente['beto']['id']}"
    ruta_b_a = f"/social/bloqueos/{gente['ana']['id']}"
    assert cliente.post(ruta_a_b, headers=_h(gente, "ana")).status_code == 204
    assert cliente.post(ruta_b_a, headers=_h(gente, "beto")).status_code == 204
    assert cliente.post(ruta_b_a, headers=_h(gente, "beto")).status_code == 204  # repetir no duplica
    assert [p["id"] for p in cliente.get("/social/bloqueos", headers=_h(gente, "ana")).json()] == [gente["beto"]["id"]]
    assert cliente.delete(ruta_b_a, headers=_h(gente, "beto")).status_code == 204
    assert cliente.get("/social/bloqueos", headers=_h(gente, "beto")).json() == []
    assert [p["id"] for p in cliente.get("/social/bloqueos", headers=_h(gente, "ana")).json()] == [gente["beto"]["id"]]
    assert _retar(cliente, gente, "beto", "ana").status_code == 403


def test_denunciar_solo_a_conocidos_el_docente_la_ve_y_la_marca_revisada(aula) -> None:
    cliente, gente, _, _ = aula
    cuerpo = {"usuario_id": gente["beto"]["id"], "motivo": "conducta", "detalle": "  me\n insultó   en la partida " + "x" * 250}

    assert cliente.post("/social/denuncias", json={**cuerpo, "usuario_id": gente["eli"]["id"]}, headers=_h(gente, "ana")).status_code == 403  # eli no es compañera ni amiga
    assert cliente.post("/social/denuncias", json={**cuerpo, "motivo": "insulto"}, headers=_h(gente, "ana")).status_code == 422
    hecha = cliente.post("/social/denuncias", json={**cuerpo, "bloquear": True}, headers=_h(gente, "ana"))

    assert hecha.status_code == 201 and "bloqueaste" in hecha.json()["mensaje"]
    assert [p["id"] for p in cliente.get("/social/bloqueos", headers=_h(gente, "ana")).json()] == [gente["beto"]["id"]]
    denuncias = cliente.get("/social/denuncias", headers=_h(gente, "docente")).json()
    assert len(denuncias) == 1 and denuncias[0]["estado"] == "nueva" and denuncias[0]["motivo"] == "conducta"
    assert denuncias[0]["detalle"] == "me insultó en la partida " + "x" * 250  # sin saltos de línea ni espacios de más
    assert (denuncias[0]["denunciante"], denuncias[0]["denunciado"]) == ("Persona Prueba", "Persona Prueba")
    assert cliente.get("/social/denuncias", headers=_h(gente, "otro_docente")).json() == []  # otro docente no ve las de mi curso
    assert cliente.get("/social/denuncias", headers=_h(gente, "ana")).status_code == 403

    assert cliente.post(f"/social/denuncias/{denuncias[0]['id']}/revisada", headers=_h(gente, "otro_docente")).status_code == 404
    assert cliente.post(f"/social/denuncias/{denuncias[0]['id']}/revisada", headers=_h(gente, "docente")).status_code == 204
    assert cliente.get("/social/denuncias?solo_nuevas=true", headers=_h(gente, "docente")).json() == []
    assert cliente.get("/social/denuncias", headers=_h(gente, "plataforma")).json()[0]["estado"] == "revisada"


def test_se_puede_denunciar_a_quien_se_jugo_una_sala_y_hay_un_tope_por_hora(aula) -> None:
    cliente, gente, _, _ = aula
    codigo = _partida(cliente, gente, "eli", "ana")  # eli no tiene curso: no es compañera de ana

    assert cliente.post("/social/denuncias", json={"usuario_id": gente["ana"]["id"], "motivo": "trampa"}, headers=_h(gente, "eli")).status_code == 403
    assert cliente.post("/social/denuncias", json={"usuario_id": gente["ana"]["id"], "motivo": "trampa", "codigo_sala": codigo}, headers=_h(gente, "eli")).status_code == 201

    respuestas = [
        cliente.post("/social/denuncias", json={"usuario_id": gente["ana"]["id"], "motivo": "otro", "codigo_sala": codigo}, headers=_h(gente, "eli")).status_code for _ in range(5)
    ]
    assert respuestas == [201] * 4 + [429]


# --- cuentas de alumnos y claves -----------------------------------------------------------------------------------------------------------


def test_el_docente_crea_las_cuentas_de_sus_alumnos_y_ellos_entran_con_su_usuario(aula) -> None:
    cliente, gente, fabrica, curso = aula

    creadas = cliente.post(
        f"/cursos/{curso['id']}/estudiantes/crear",
        json={"alumnos": [{"nombre": "José Luis Méndez", "edad": 9, "tutor_nombre": "Rosa Méndez", "tutor_telefono": "70011223"}, {"nombre": "Ana Ríos"}]},
        headers=_h(gente, "docente"),
    )

    assert creadas.status_code == 201 and creadas.json()["dominio"] == "alumnos.example.com"
    cuentas = creadas.json()["cuentas"]
    assert [c["nombre"] for c in cuentas] == ["José Luis Méndez", "Ana Ríos"]
    assert cuentas[0]["usuario"].startswith("jose.mendez") and len(cuentas[0]["password"]) == 8
    ingreso = cliente.post("/auth/login", json={"email": f"{cuentas[0]['usuario']}@alumnos.example.com", "password": cuentas[0]["password"]})
    assert ingreso.status_code == 200 and ingreso.json()["usuario"]["edad"] == 9
    estudiantes = {e["nombre"]: e for e in cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=_h(gente, "docente")).json()}
    assert estudiantes["José Luis Méndez"]["cuenta_gestionada"] is True and estudiantes["José Luis Méndez"]["tutor_telefono"] == "70011223"
    assert cliente.post("/auth/login", json={"email": f"{cuentas[1]['usuario']}@alumnos.example.com", "password": "otraclave1"}).status_code == 401
    assert cliente.post(f"/cursos/{curso['id']}/estudiantes/crear", json={"alumnos": [{"nombre": "X Y"}]}, headers=_h(gente, "otro_docente")).status_code == 404


def test_crear_cuentas_es_todo_o_nada_y_respeta_el_cupo_del_curso(aula) -> None:
    cliente, gente, fabrica, curso = aula
    antes = len(cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=_h(gente, "docente")).json())

    con_telefono_malo = cliente.post(
        f"/cursos/{curso['id']}/estudiantes/crear",
        json={"alumnos": [{"nombre": "Bien Hecho"}, {"nombre": "Mal Telefono", "tutor_telefono": "12"}]},
        headers=_h(gente, "docente"),
    )
    cliente.patch(f"/cursos/{curso['id']}", json={"cupo": antes + 1}, headers=_h(gente, "docente"))
    sin_cupo = cliente.post(f"/cursos/{curso['id']}/estudiantes/crear", json={"alumnos": [{"nombre": "Uno Dos"}, {"nombre": "Tres Cuatro"}]}, headers=_h(gente, "docente"))

    assert con_telefono_malo.status_code == 400 and "Mal Telefono" in con_telefono_malo.json()["detail"]
    assert sin_cupo.status_code == 400 and "cupo" in sin_cupo.json()["detail"]
    assert len(cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=_h(gente, "docente")).json()) == antes  # no quedó nadie a medias


def test_restablecer_la_clave_y_cambiarla(aula) -> None:
    cliente, gente, _, curso = aula
    personal = f"/cursos/{curso['id']}/estudiantes/{gente['ana']['id']}/restablecer-clave"
    assert cliente.post(personal, headers=_h(gente, "docente")).status_code == 400
    creada = cliente.post(f"/cursos/{curso['id']}/estudiantes/crear", json={"alumnos": [{"nombre": "Ana Gestionada", "edad": 10}]}, headers=_h(gente, "docente")).json()["cuentas"][0]
    email = f"{creada['usuario']}@alumnos.example.com"
    original = cliente.post("/auth/login", json={"email": email, "password": creada["password"]}).json()["tokens"]
    ruta = f"/cursos/{curso['id']}/estudiantes/{creada['id']}/restablecer-clave"

    nueva = cliente.post(ruta, headers=_h(gente, "docente"))

    assert nueva.status_code == 200 and nueva.json()["usuario"] == creada["usuario"] and len(nueva.json()["password"]) == 8
    assert cliente.post("/auth/login", json={"email": email, "password": creada["password"]}).status_code == 401
    assert cliente.get("/auth/me", headers={"Authorization": f"Bearer {original['access_token']}"}).status_code == 401
    assert cliente.post("/auth/refresh", json={"refresh_token": original["refresh_token"]}).status_code == 401
    acceso = cliente.post("/auth/login", json={"email": email, "password": nueva.json()["password"]})
    assert acceso.status_code == 200
    token = {"Authorization": f"Bearer {acceso.json()['tokens']['access_token']}"}
    assert cliente.post(ruta, headers=_h(gente, "otro_docente")).status_code == 404
    assert cliente.post(f"/cursos/{curso['id']}/estudiantes/{gente['eli']['id']}/restablecer-clave", headers=_h(gente, "docente")).status_code == 404

    mal = cliente.post("/auth/me/cambiar-password", json={"password_actual": "incorrecta1", "password_nueva": "MiClave2026"}, headers=token)
    corta = cliente.post("/auth/me/cambiar-password", json={"password_actual": nueva.json()["password"], "password_nueva": "abc"}, headers=token)
    ok = cliente.post("/auth/me/cambiar-password", json={"password_actual": nueva.json()["password"], "password_nueva": "MiClave2026"}, headers=token)
    assert (mal.status_code, corta.status_code, ok.status_code) == (400, 422, 200)
    assert cliente.get("/auth/me", headers=token).status_code == 401
    assert cliente.get("/auth/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).status_code == 200
    assert cliente.post("/auth/login", json={"email": email, "password": "MiClave2026"}).status_code == 200


def test_borrar_contacto_familiar_retira_la_declaracion_y_no_borra_otros_campos(aula) -> None:
    cliente, gente, _, curso = aula
    headers = _h(gente, "chico")
    datos = {"tutor_nombre": "Rosa Pérez", "tutor_telefono": "71234567", "tutor_email": "rosa@example.com", "autorizacion_tutor": True}
    assert cliente.patch("/auth/me", json=datos, headers=headers).status_code == 200
    assert cliente.patch("/auth/me", json={"tutor_email": "correo malo"}, headers=headers).status_code == 422
    estudiantes = cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=_h(gente, "docente")).json()
    assert next(alumno for alumno in estudiantes if alumno["id"] == gente["chico"]["id"])["tutor_email"] == "rosa@example.com"
    assert "rosa@example.com" not in cliente.get("/social/amigos", headers=_h(gente, "ana")).text
    assert cliente.patch("/auth/me", json={"tutor_telefono": None}, headers=headers).json()["tutor_telefono"] is None
    estado = cliente.get("/auth/me/autorizacion", headers=headers).json()
    assert estado["estado"] == "falta_tutor" and estado["tutor_nombre"] == "Rosa Pérez"
    # Volver a cargar el teléfono no restaura una declaración que ya se retiró.
    cliente.patch("/auth/me", json={"tutor_telefono": "71234567"}, headers=headers)
    assert cliente.get("/auth/me/autorizacion", headers=headers).json()["estado"] == "falta_tutor"
    ruta = f"/cursos/{curso['id']}/estudiantes/{gente['chico']['id']}/familia"
    borrado = cliente.patch(ruta, json={"tutor_nombre": "", "tutor_telefono": "", "tutor_email": None}, headers=_h(gente, "docente"))
    assert borrado.status_code == 200 and borrado.json()["tutor_nombre"] is None and borrado.json()["tutor_telefono"] is None and borrado.json()["tutor_email"] is None


# --- avisos y tareas -----------------------------------------------------------------------------------------------------------------------


def test_los_avisos_del_docente_llegan_a_los_alumnos_del_curso(aula) -> None:
    cliente, gente, _, curso = aula
    ruta = f"/cursos/{curso['id']}/avisos"

    assert cliente.post(ruta, json={"texto": "Clase en vivo", "enlace": "javascript:alert(1)"}, headers=_h(gente, "docente")).status_code == 400
    assert cliente.post(ruta, json={"texto": "ab"}, headers=_h(gente, "docente")).status_code == 422
    assert cliente.post(ruta, json={"texto": "Hola"}, headers=_h(gente, "otro_docente")).status_code == 404
    publicado = cliente.post(ruta, json={"texto": "  Hoy   clase en vivo a las 15:00  ", "enlace": "https://meet.example.com/abc"}, headers=_h(gente, "docente"))

    assert publicado.status_code == 201 and publicado.json()["texto"] == "Hoy clase en vivo a las 15:00"
    de_la_alumna = cliente.get("/cursos/mio/avisos", headers=_h(gente, "ana")).json()
    assert [(a["texto"], a["enlace"]) for a in de_la_alumna] == [("Hoy clase en vivo a las 15:00", "https://meet.example.com/abc")]
    assert cliente.get("/cursos/mio/avisos", headers=_h(gente, "eli")).json() == []  # quien no es del curso no recibe nada
    assert _avisos(cliente, gente, "ana").json()["aviso_docente"]["texto"] == "Hoy clase en vivo a las 15:00"
    assert _avisos(cliente, gente, "eli").json()["aviso_docente"] is None

    assert cliente.delete(f"{ruta}/{publicado.json()['id']}", headers=_h(gente, "otro_docente")).status_code == 404
    assert cliente.delete(f"{ruta}/{publicado.json()['id']}", headers=_h(gente, "docente")).status_code == 204
    assert cliente.get("/cursos/mio/avisos", headers=_h(gente, "ana")).json() == []


def test_una_tarea_se_mide_sola_con_lo_que_juegan_los_alumnos(aula) -> None:
    cliente, gente, fabrica, curso = aula
    ruta = f"/cursos/{curso['id']}/tareas"
    assert cliente.post(ruta, json={"titulo": "Jugá 2 partidas con compañeros", "tipo": "partidas_amigos", "meta": 500, "dias": 7}, headers=_h(gente, "docente")).status_code == 422
    assert cliente.post(ruta, json={"titulo": "Precisión", "tipo": "precision", "meta": 5, "dias": 7}, headers=_h(gente, "docente")).status_code == 400  # fuera del rango del tipo
    amigos_tarea = cliente.post(ruta, json={"titulo": "Jugá 1 partida con un compañero", "tipo": "partidas_amigos", "meta": 1, "dias": 7}, headers=_h(gente, "docente"))
    precision = cliente.post(ruta, json={"titulo": "Una partida con 60 % de precisión", "tipo": "precision", "meta": 60, "dias": 7}, headers=_h(gente, "docente"))
    assert amigos_tarea.status_code == 201 and precision.status_code == 201 and amigos_tarea.json()["total"] == 5

    codigo = _partida(cliente, gente, "ana", "beto")
    cliente.post(f"/sala/{codigo}/abandonar", headers=_h(gente, "beto"))  # la partida terminó
    with fabrica() as db:
        en_cinco_minutos = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)  # dentro de la ventana de la tarea
        db.add(CalibracionORM(usuario_id=gente["caro"]["id"], partida_id="p1", precision_global=72.4, nivel_partida=8, rango_partida="Intermedio", total_jugadas=20, creado_en=en_cinco_minutos))
        db.commit()

    de_ana = {t["tipo"]: t for t in cliente.get("/cursos/mio/tareas", headers=_h(gente, "ana")).json()}
    de_caro = {t["tipo"]: t for t in cliente.get("/cursos/mio/tareas", headers=_h(gente, "caro")).json()}
    assert (de_ana["partidas_amigos"]["valor"], de_ana["partidas_amigos"]["cumplida"], de_ana["precision"]["cumplida"]) == (1, True, False)
    assert (de_caro["partidas_amigos"]["cumplida"], de_caro["precision"]["valor"], de_caro["precision"]["cumplida"]) == (False, 72, True)
    del_docente = {t["tipo"]: t for t in cliente.get(ruta, headers=_h(gente, "docente")).json()}
    assert del_docente["partidas_amigos"]["cumplieron"] == 2 and del_docente["precision"]["cumplieron"] == 1  # ana y beto jugaron; caro tiene la precisión
    assert cliente.get(ruta, headers=_h(gente, "otro_docente")).status_code == 404
    assert cliente.delete(f"{ruta}/{amigos_tarea.json()['id']}", headers=_h(gente, "docente")).status_code == 204
    assert [t["tipo"] for t in cliente.get("/cursos/mio/tareas", headers=_h(gente, "ana")).json()] == ["precision"]


# --- torneos relámpago ----------------------------------------------------------------------------------------------------------------------


def test_emparejar_evita_repetir_rival_reparte_colores_y_descansa_a_uno_distinto_cada_vez() -> None:
    jugadores = [{"id": i, "puntos": 0.0, "descansos": 0, "blancas": 0} for i in (1, 2, 3, 4)]
    parejas, descansa = torneos.emparejar(jugadores, set())
    assert descansa is None and sorted(x for p in parejas for x in p) == [1, 2, 3, 4]

    ya = {frozenset(p) for p in parejas}
    for j in jugadores:
        j["puntos"] = 1.0 if j["id"] in {p[0] for p in parejas} else 0.0
    segunda, _ = torneos.emparejar(jugadores, ya)
    assert not any(frozenset(p) in ya for p in segunda)  # nadie repite rival

    impares = [{"id": i, "puntos": 0.0, "descansos": 0, "blancas": 0} for i in (1, 2, 3)]
    _, primero = torneos.emparejar(impares, set())
    impares[[j["id"] for j in impares].index(primero)]["descansos"] = 1
    _, segundo = torneos.emparejar(impares, set())
    assert primero != segundo  # no le toca descansar dos veces antes que a los demás

    con_blancas = [{"id": 1, "puntos": 1.0, "descansos": 0, "blancas": 3}, {"id": 2, "puntos": 0.0, "descansos": 0, "blancas": 0}]
    assert torneos.emparejar(con_blancas, set())[0] == [(2, 1)]  # juega con blancas quien menos las tuvo


def _torneo(cliente, gente, curso, rondas=2):
    respuesta = cliente.post("/torneos", json={"curso_id": curso["id"], "nombre": "Relámpago de octubre", "control_tiempo_ms": CINCO_MIN, "rondas": rondas}, headers=_h(gente, "docente"))
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _jugar_ronda_abandonando(cliente, gente, torneo_id, por_el_de_negras=True):
    """Termina todas las partidas de la ronda: en cada una abandona quien tiene negras (gana el de blancas)."""
    detalle = cliente.get(f"/torneos/{torneo_id}", headers=_h(gente, "docente")).json()
    por_id = {g["id"]: nombre for nombre, g in gente.items()}
    from backend.servicios.sala import servicio_sala as svc

    for partida in detalle["partidas_de_la_ronda"]:
        if partida["descanso"] or partida["codigo"] is None:
            continue
        sala = svc._repositorio.obtener_por_codigo(partida["codigo"])
        abandona = sala.usuario_negras_id if por_el_de_negras else sala.usuario_blancas_id
        cliente.post(f"/sala/{partida['codigo']}/abandonar", headers=_h(gente, por_id[abandona]))


def test_un_torneo_completo_se_inscribe_empareja_juega_y_termina_con_su_tabla(aula) -> None:
    cliente, gente, _, curso = aula
    assert cliente.post("/torneos", json={"curso_id": curso["id"], "nombre": "Sin reloj", "control_tiempo_ms": 0, "rondas": 2}, headers=_h(gente, "docente")).status_code == 400
    assert cliente.post("/torneos", json={"curso_id": curso["id"], "nombre": "Ajeno", "control_tiempo_ms": CINCO_MIN, "rondas": 2}, headers=_h(gente, "otro_docente")).status_code == 404
    torneo = _torneo(cliente, gente, curso)
    assert cliente.post("/torneos", json={"curso_id": curso["id"], "nombre": "Otro más", "control_tiempo_ms": CINCO_MIN, "rondas": 2}, headers=_h(gente, "docente")).status_code == 409

    # los alumnos ven que hay un torneo y se inscriben; el menor sin autorización no puede
    assert _avisos(cliente, gente, "ana").json()["torneo"]["estado"] == "inscripcion"
    assert _avisos(cliente, gente, "eli").json()["torneo"] is None
    assert cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, "ana")).json()["inscrito"] is True
    assert cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, "chico")).status_code == 403
    assert cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, "eli")).status_code == 404  # no es del curso
    masiva = cliente.post(f"/torneos/{torneo['id']}/inscribir-a-todos", headers=_h(gente, "docente")).json()
    assert masiva["inscritos"] == 3 and [o["nombre"] for o in masiva["omitidos"]] == ["Persona Prueba"]  # beto, caro, dani entraron; el menor no
    assert cliente.post(f"/torneos/{torneo['id']}/inscribir-a-todos", headers=_h(gente, "ana")).status_code == 403

    # ronda 1: dos partidas
    ronda1 = cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).json()
    assert (ronda1["estado"], ronda1["ronda_actual"], len(ronda1["partidas_de_la_ronda"])) == ("en_juego", 1, 2)
    aviso = _avisos(cliente, gente, "ana").json()["torneo"]
    assert aviso["mi_partida"]["ronda"] == 1 and aviso["mi_partida"]["rival"]["id"] != gente["ana"]["id"]
    sala = cliente.get(f"/sala/{aviso['mi_partida']['codigo']}", headers=_h(gente, "ana")).json()
    assert (sala["estado"], sala["control_tiempo_ms"], sala["reloj_corre"]) == ("en_curso", CINCO_MIN, True)  # el reloj corre desde que se arma la ronda
    assert cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).status_code == 409  # faltan terminar las partidas

    _jugar_ronda_abandonando(cliente, gente, torneo["id"])
    tabla1 = cliente.get(f"/torneos/{torneo['id']}", headers=_h(gente, "ana")).json()["clasificacion"]
    assert sorted(f["puntos"] for f in tabla1) == [0.0, 0.0, 1.0, 1.0]

    # ronda 2 (la última), nadie repite rival
    ronda2 = cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).json()
    assert ronda2["ronda_actual"] == 2
    _jugar_ronda_abandonando(cliente, gente, torneo["id"])
    final = cliente.get(f"/torneos/{torneo['id']}", headers=_h(gente, "ana")).json()
    assert final["estado"] == "terminado" and sum(f["puntos"] for f in final["clasificacion"]) == 4.0
    assert [f["puesto"] for f in final["clasificacion"]][0] == 1
    assert [f["puntos"] for f in final["clasificacion"]] == sorted((f["puntos"] for f in final["clasificacion"]), reverse=True)
    assert cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).status_code == 409
    assert _avisos(cliente, gente, "ana").json()["torneo"] is None  # ya terminó: deja de molestar
    assert len(cliente.get(f"/torneos/curso/{curso['id']}", headers=_h(gente, "ana")).json()) == 1


def test_con_jugadores_impares_a_uno_le_toca_descansar_y_suma_un_punto(aula) -> None:
    cliente, gente, _, curso = aula
    torneo = _torneo(cliente, gente, curso, rondas=3)
    for nombre in ("ana", "beto", "caro"):
        cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, nombre))

    ronda = cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).json()

    descansos = [p for p in ronda["partidas_de_la_ronda"] if p["descanso"]]
    assert len(ronda["partidas_de_la_ronda"]) == 2 and len(descansos) == 1 and descansos[0]["resultado"] == "1-0"
    assert sorted(f["puntos"] for f in ronda["clasificacion"]) == [0.0, 0.0, 1.0]
    assert cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).status_code == 409
    forzada = cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={"forzar": True}, headers=_h(gente, "docente"))
    assert forzada.status_code == 200 and forzada.json()["ronda_actual"] == 2  # la partida sin terminar quedó sin ganador


def test_quien_no_se_presenta_pierde_por_tiempo_y_el_docente_puede_cerrar_el_torneo(aula, monkeypatch) -> None:
    cliente, gente, _, curso = aula
    torneo = _torneo(cliente, gente, curso)
    for nombre in ("ana", "beto"):
        cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, nombre))
    cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente"))
    detalle = cliente.get(f"/torneos/{torneo['id']}", headers=_h(gente, "docente")).json()
    assert detalle["partidas_de_la_ronda"][0]["resultado"] is None

    monkeypatch.setattr(servicio_sala, "_ahora", lambda: datetime.now(timezone.utc) + timedelta(seconds=CINCO_MIN // 1000 + 60))  # pasaron más de 5 minutos y las blancas nunca movieron
    despues = cliente.get(f"/torneos/{torneo['id']}", headers=_h(gente, "docente")).json()

    assert despues["partidas_de_la_ronda"][0]["resultado"] == "0-1"  # ganaron las negras: las blancas se quedaron sin tiempo
    assert sorted(f["puntos"] for f in despues["clasificacion"]) == [0.0, 1.0]
    cerrado = cliente.post(f"/torneos/{torneo['id']}/cerrar", headers=_h(gente, "docente")).json()
    assert cerrado["estado"] == "terminado"
    assert cliente.post(f"/torneos/{torneo['id']}/cerrar", headers=_h(gente, "ana")).status_code == 403


def test_borrar_un_curso_borra_sus_torneos_avisos_y_tareas(aula) -> None:
    cliente, gente, fabrica, curso = aula
    _torneo(cliente, gente, curso)
    cliente.post(f"/cursos/{curso['id']}/avisos", json={"texto": "Hola a todos"}, headers=_h(gente, "docente"))
    cliente.post(f"/cursos/{curso['id']}/tareas", json={"titulo": "Una tarea", "tipo": "partidas_ia", "meta": 1, "dias": 3}, headers=_h(gente, "docente"))

    assert cliente.delete(f"/cursos/{curso['id']}", headers=_h(gente, "docente")).status_code == 204

    from backend.modelos.tablas_orm import AvisoCursoORM, TareaCursoORM, TorneoORM

    with fabrica() as db:
        assert (db.query(TorneoORM).count(), db.query(AvisoCursoORM).count(), db.query(TareaCursoORM).count()) == (0, 0, 0)


def test_el_torneo_no_crea_una_ronda_si_un_jugador_tiene_otra_partida_abierta(aula):
    cliente, gente, _, curso = aula
    torneo = _torneo(cliente, gente, curso)
    for nombre in ("ana", "beto"):
        assert cliente.post(f"/torneos/{torneo['id']}/inscribirse", headers=_h(gente, nombre)).status_code == 200
    sala = cliente.post("/sala", json={}, headers=_h(gente, "ana")).json()
    ronda = cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente"))
    assert ronda.status_code == 409 and "otra partida" in ronda.json()["detail"]
    detalle = cliente.get(f"/torneos/{torneo['id']}", headers=_h(gente, "docente")).json()
    assert detalle["ronda_actual"] == 0 and detalle["partidas_de_la_ronda"] == []
    cliente.delete(f"/sala/{sala['codigo']}", headers=_h(gente, "ana"))
    assert cliente.post(f"/torneos/{torneo['id']}/iniciar-ronda", json={}, headers=_h(gente, "docente")).status_code == 200
