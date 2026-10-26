"""Lo social entre jugadores: compañeros y amigos, retos, búsqueda de rival y avisos (SQLite en memoria)."""
from __future__ import annotations

import pytest

from backend.modelos.tablas_orm import AmistadORM, UsuarioORM
from backend.repositorios.repositorio_partida import RepositorioPartidasPostgres
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.partida import servicio_partida
from backend.servicios.sala import presencia_sala, servicio_sala
from backend.servicios.social import amigos, emparejamiento, en_linea, retos

CINCO_MIN = 300_000


@pytest.fixture
def social(contexto, monkeypatch):
    """Un docente con un curso donde están `ana`, `beto` y `caro`, y `dani` y `eli` sin curso. Devuelve `(cliente, gente, fabrica, curso)`."""
    cliente, registrar, fabrica = contexto
    repo = RepositorioPartidasPostgres(fabrica)
    monkeypatch.setattr(servicio_sala, "_repositorio", repo)
    monkeypatch.setattr(servicio_partida, "_repositorio", repo)
    for memoria in (retos._retos, emparejamiento._cola, en_linea._visto, amigos._solicitudes_recientes, presencia_sala._ultima_senal):
        memoria.clear()
    servicio_sala._locks_partida.clear()

    gente = {
        "docente": registrar("docente@test.com", "facilitador"),
        "plataforma": registrar("plataforma@test.com", "facilitador"),
        **{nombre: registrar(f"{nombre}@test.com") for nombre in ("ana", "beto", "caro", "dani", "eli")},
    }
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")  # solo la plataforma queda como principal
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=gente["docente"]["headers"]).json()
    for nombre in ("ana", "beto", "caro"):
        assert cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=gente[nombre]["headers"]).status_code in (200, 201)
    return cliente, gente, fabrica, curso


def _nivel(fabrica, gente, nombre: str, nivel: int | None) -> None:
    with fabrica() as db:
        db.get(UsuarioORM, gente[nombre]["id"]).nivel_estimado = nivel
        db.commit()


def _avisos(cliente, gente, nombre):
    return cliente.get("/social/avisos", headers=gente[nombre]["headers"])


def _retar(cliente, gente, de, a, **cuerpo):
    return cliente.post("/social/retos", json={"retado_id": gente[a]["id"], **cuerpo}, headers=gente[de]["headers"])


# --- compañeros y amigos -------------------------------------------------------------------------------------------------------------


def test_los_companeros_de_curso_se_ven_entre_si_y_los_demas_no(social) -> None:
    cliente, gente, _, _ = social

    lista = cliente.get("/social/amigos", headers=gente["ana"]["headers"]).json()

    assert {p["id"] for p in lista["gente"]} == {gente["beto"]["id"], gente["caro"]["id"]}
    assert all(p["relacion"] == "compañero" and p["nombre"] == "Persona P." for p in lista["gente"])  # nombre abreviado, nunca el completo
    assert cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["gente"] == []
    assert len(lista["mi_codigo"]) == 9 and lista["mi_codigo"][4] == "-"


def test_quien_esta_en_linea_va_primero(social) -> None:
    cliente, gente, _, _ = social
    _avisos(cliente, gente, "caro")  # caro consultó sus avisos: su pantalla está abierta

    lista = cliente.get("/social/amigos", headers=gente["ana"]["headers"]).json()["gente"]

    assert [(p["id"], p["en_linea"]) for p in lista] == [(gente["caro"]["id"], True), (gente["beto"]["id"], False)]


def test_solo_los_jugadores_usan_lo_social(social) -> None:
    cliente, gente, _, _ = social

    for quien in ("docente", "plataforma"):
        assert cliente.get("/social/avisos", headers=gente[quien]["headers"]).status_code == 403
    assert cliente.get("/social/avisos").status_code == 401


def test_la_solicitud_por_correo_responde_igual_exista_o_no_la_cuenta(social) -> None:
    cliente, gente, fabrica, _ = social

    existente = cliente.post("/social/amigos/solicitudes", json={"email": "dani@test.com"}, headers=gente["eli"]["headers"])
    inexistente = cliente.post("/social/amigos/solicitudes", json={"email": "nadie@test.com"}, headers=gente["eli"]["headers"])
    a_uno_mismo = cliente.post("/social/amigos/solicitudes", json={"email": "eli@test.com"}, headers=gente["eli"]["headers"])
    a_la_plataforma = cliente.post("/social/amigos/solicitudes", json={"email": "plataforma@test.com"}, headers=gente["eli"]["headers"])

    assert [r.status_code for r in (existente, inexistente, a_uno_mismo, a_la_plataforma)] == [202, 202, 202, 202]
    assert existente.json() == inexistente.json() == a_uno_mismo.json()
    with fabrica() as db:
        assert [(f.solicitante_id, f.destinatario_id) for f in db.query(AmistadORM)] == [(gente["eli"]["id"], gente["dani"]["id"])]


def test_la_solicitud_se_acepta_y_desde_ahi_pueden_jugar(social) -> None:
    cliente, gente, _, _ = social
    cliente.post("/social/amigos/solicitudes", json={"email": "DANI@test.com"}, headers=gente["eli"]["headers"])
    pendiente = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"]
    assert [(s["de"]["id"], s["de"]["nombre"]) for s in pendiente] == [(gente["eli"]["id"], "Persona P.")]
    assert _avisos(cliente, gente, "dani").json()["solicitudes_amistad"] == 1
    assert _retar(cliente, gente, "eli", "dani").status_code == 403  # todavía no son amigos

    assert cliente.post(f"/social/amigos/solicitudes/{pendiente[0]['id']}/aceptar", headers=gente["dani"]["headers"]).status_code == 204

    assert [p["relacion"] for p in cliente.get("/social/amigos", headers=gente["eli"]["headers"]).json()["gente"]] == ["amigo"]
    assert _avisos(cliente, gente, "dani").json()["solicitudes_amistad"] == 0
    assert _retar(cliente, gente, "eli", "dani").status_code == 201


def test_rechazar_una_solicitud_la_borra_sin_avisar_a_quien_la_mando(social) -> None:
    cliente, gente, fabrica, _ = social
    cliente.post("/social/amigos/solicitudes", json={"email": "dani@test.com"}, headers=gente["eli"]["headers"])
    solicitud = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"][0]

    assert cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/rechazar", headers=gente["dani"]["headers"]).status_code == 204

    with fabrica() as db:
        assert db.query(AmistadORM).count() == 0
    assert cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=gente["dani"]["headers"]).status_code == 404
    assert cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=gente["eli"]["headers"]).status_code == 404  # tampoco la puede aceptar otro


def test_el_codigo_de_amigo_sirve_para_mandar_la_solicitud_y_si_se_cruzan_quedan_amigos(social) -> None:
    cliente, gente, _, _ = social
    codigo_de_dani = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["mi_codigo"]
    assert cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["mi_codigo"] == codigo_de_dani  # no cambia

    cliente.post("/social/amigos/solicitudes", json={"codigo": codigo_de_dani.lower()}, headers=gente["eli"]["headers"])
    assert len(cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"]) == 1
    codigo_de_eli = cliente.get("/social/amigos", headers=gente["eli"]["headers"]).json()["mi_codigo"]
    cliente.post("/social/amigos/solicitudes", json={"codigo": codigo_de_eli}, headers=gente["dani"]["headers"])  # dani también le pidió: quedan amigos

    assert [p["relacion"] for p in cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["gente"]] == ["amigo"]


def test_a_un_companero_de_curso_se_le_manda_la_solicitud_desde_la_lista_pero_a_un_desconocido_no(social) -> None:
    cliente, gente, fabrica, _ = social

    a_companero = cliente.post("/social/amigos/solicitudes", json={"usuario_id": gente["beto"]["id"]}, headers=gente["ana"]["headers"])
    a_desconocido = cliente.post("/social/amigos/solicitudes", json={"usuario_id": gente["dani"]["id"]}, headers=gente["ana"]["headers"])

    assert (a_companero.status_code, a_desconocido.status_code) == (202, 202)  # la respuesta no delata a quién llegó
    with fabrica() as db:
        assert [(f.solicitante_id, f.destinatario_id) for f in db.query(AmistadORM)] == [(gente["ana"]["id"], gente["beto"]["id"])]
    assert [p["relacion"] for p in cliente.get("/social/amigos", headers=gente["beto"]["headers"]).json()["gente"] if p["id"] == gente["ana"]["id"]] == ["compañero"]
    assert len(cliente.get("/social/amigos", headers=gente["beto"]["headers"]).json()["solicitudes_recibidas"]) == 1


def test_las_solicitudes_seguidas_tienen_un_limite(social) -> None:
    cliente, gente, _, _ = social

    respuestas = [
        cliente.post("/social/amigos/solicitudes", json={"email": f"nadie{i}@test.com"}, headers=gente["eli"]["headers"]).status_code for i in range(12)
    ]

    assert respuestas == [202] * amigos.SOLICITUDES_POR_VENTANA + [429, 429]
    assert cliente.post("/social/amigos/solicitudes", json={}, headers=gente["dani"]["headers"]).status_code == 400


def test_dejar_de_ser_amigos(social) -> None:
    cliente, gente, _, _ = social
    cliente.post("/social/amigos/solicitudes", json={"email": "dani@test.com"}, headers=gente["eli"]["headers"])
    solicitud = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"][0]
    cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=gente["dani"]["headers"])

    assert cliente.delete(f"/social/amigos/{gente['dani']['id']}", headers=gente["eli"]["headers"]).status_code == 204

    assert cliente.get("/social/amigos", headers=gente["eli"]["headers"]).json()["gente"] == []
    assert _retar(cliente, gente, "eli", "dani").status_code == 403


# --- retos -----------------------------------------------------------------------------------------------------------------------------


def test_un_reto_a_un_companero_se_acepta_y_la_partida_empieza_con_el_reloj_elegido(social) -> None:
    cliente, gente, _, _ = social

    reto = _retar(cliente, gente, "ana", "beto", color="blancas", control_tiempo_ms=CINCO_MIN)

    assert reto.status_code == 201 and reto.json()["tu_color"] == "blancas"
    recibidos = _avisos(cliente, gente, "beto").json()["retos_recibidos"]
    assert [(r["de"]["id"], r["tu_color"], r["control_tiempo_ms"]) for r in recibidos] == [(gente["ana"]["id"], "negras", CINCO_MIN)]
    assert _avisos(cliente, gente, "ana").json()["mi_reto"]["estado"] == "pendiente"

    aceptado = cliente.post(f"/social/retos/{recibidos[0]['id']}/aceptar", headers=gente["beto"]["headers"])

    codigo = aceptado.json()["codigo"]
    sala = cliente.get(f"/sala/{codigo}", headers=gente["ana"]["headers"]).json()
    assert (sala["estado"], sala["rol"], sala["control_tiempo_ms"]) == ("en_curso", "blancas", CINCO_MIN)
    assert (sala["tiempo_blancas_ms"], sala["tiempo_negras_ms"], sala["reloj_corre"]) == (CINCO_MIN, CINCO_MIN, False)
    assert (sala["negras"]["id"], sala["negras"]["nombre_publico"]) == (gente["beto"]["id"], "Persona P.")
    aviso_de_ana = _avisos(cliente, gente, "ana").json()["mi_reto"]
    assert (aviso_de_ana["estado"], aviso_de_ana["codigo"]) == ("aceptado", codigo)
    assert _avisos(cliente, gente, "ana").json()["mi_reto"] is None  # el final se informa una sola vez
    assert _avisos(cliente, gente, "beto").json()["retos_recibidos"] == []


def test_no_se_puede_retar_a_un_desconocido_ni_a_un_docente(social) -> None:
    cliente, gente, _, _ = social

    assert _retar(cliente, gente, "ana", "dani").status_code == 403  # no es de su curso ni amigo
    assert _retar(cliente, gente, "dani", "ana").status_code == 403
    assert _retar(cliente, gente, "ana", "docente").status_code == 404
    assert _retar(cliente, gente, "ana", "ana").status_code == 404
    assert cliente.post("/social/retos", json={"retado_id": 9999}, headers=gente["ana"]["headers"]).status_code == 404
    assert _retar(cliente, gente, "ana", "beto", control_tiempo_ms=12_345).status_code == 400


def test_rechazar_un_reto_cancela_la_sala_y_se_le_avisa_a_quien_reto(social) -> None:
    cliente, gente, _, _ = social
    codigo = _retar(cliente, gente, "ana", "beto").json()["codigo"]
    reto_id = _avisos(cliente, gente, "beto").json()["retos_recibidos"][0]["id"]

    assert cliente.post(f"/social/retos/{reto_id}/rechazar", headers=gente["beto"]["headers"]).status_code == 204

    assert _avisos(cliente, gente, "ana").json()["mi_reto"]["estado"] == "rechazado"
    assert cliente.get(f"/sala/{codigo}", headers=gente["ana"]["headers"]).status_code == 404
    assert cliente.post(f"/social/retos/{reto_id}/aceptar", headers=gente["beto"]["headers"]).status_code == 409


def test_un_reto_sin_respuesta_vence_y_la_sala_desaparece(social, monkeypatch) -> None:
    cliente, gente, _, _ = social
    reloj = {"t": 1000.0}
    monkeypatch.setattr(retos, "_instante", lambda: reloj["t"])
    codigo = _retar(cliente, gente, "ana", "beto").json()["codigo"]
    reloj["t"] += retos.SEGUNDOS_DE_VIGENCIA - 1
    assert len(_avisos(cliente, gente, "beto").json()["retos_recibidos"]) == 1

    reloj["t"] += 5

    assert _avisos(cliente, gente, "beto").json()["retos_recibidos"] == []
    assert _avisos(cliente, gente, "ana").json()["mi_reto"]["estado"] == "expirado"
    assert cliente.get(f"/sala/{codigo}", headers=gente["ana"]["headers"]).status_code == 404


def test_quien_reto_puede_cancelar_y_un_reto_nuevo_reemplaza_al_anterior(social) -> None:
    cliente, gente, _, _ = social
    primero = _retar(cliente, gente, "ana", "beto").json()
    segundo = _retar(cliente, gente, "ana", "caro").json()

    assert cliente.get(f"/sala/{primero['codigo']}", headers=gente["ana"]["headers"]).status_code == 404
    assert _avisos(cliente, gente, "beto").json()["retos_recibidos"] == []
    assert len(_avisos(cliente, gente, "caro").json()["retos_recibidos"]) == 1

    assert cliente.delete(f"/social/retos/{segundo['id']}", headers=gente["ana"]["headers"]).status_code == 204
    assert _avisos(cliente, gente, "caro").json()["retos_recibidos"] == []
    assert cliente.delete(f"/social/retos/{segundo['id']}", headers=gente["beto"]["headers"]).status_code == 404


def test_no_se_puede_retar_ni_aceptar_mientras_se_juega_otra_partida(social) -> None:
    cliente, gente, _, _ = social
    codigo = _retar(cliente, gente, "ana", "beto").json()["codigo"]
    reto_id = _avisos(cliente, gente, "beto").json()["retos_recibidos"][0]["id"]
    cliente.post(f"/social/retos/{reto_id}/aceptar", headers=gente["beto"]["headers"])

    assert _retar(cliente, gente, "caro", "beto").status_code == 409  # beto ya está jugando
    assert _retar(cliente, gente, "ana", "caro").status_code == 409  # ana también
    assert cliente.post("/social/emparejamiento", json={}, headers=gente["ana"]["headers"]).status_code == 409
    assert cliente.get(f"/sala/{codigo}", headers=gente["ana"]["headers"]).json()["estado"] == "en_curso"


def test_la_revancha_cambia_los_colores_y_no_exige_ser_amigos(social) -> None:
    cliente, gente, _, _ = social
    cliente.post("/social/amigos/solicitudes", json={"email": "dani@test.com"}, headers=gente["eli"]["headers"])
    solicitud = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"][0]
    cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=gente["dani"]["headers"])
    codigo = _retar(cliente, gente, "eli", "dani", color="blancas", control_tiempo_ms=CINCO_MIN).json()["codigo"]
    cliente.post(f"/social/retos/{_avisos(cliente, gente, 'dani').json()['retos_recibidos'][0]['id']}/aceptar", headers=gente["dani"]["headers"])
    cliente.delete(f"/social/amigos/{gente['eli']['id']}", headers=gente["dani"]["headers"])  # ya no son amigos
    assert cliente.post(f"/social/retos/revancha/{codigo}", headers=gente["dani"]["headers"]).status_code == 409  # la partida sigue en juego

    cliente.post(f"/sala/{codigo}/abandonar", headers=gente["eli"]["headers"])
    revancha = cliente.post(f"/social/retos/revancha/{codigo}", headers=gente["eli"]["headers"])

    assert revancha.status_code == 201
    assert (revancha.json()["tu_color"], revancha.json()["control_tiempo_ms"]) == ("negras", CINCO_MIN)
    recibido = _avisos(cliente, gente, "dani").json()["retos_recibidos"][0]
    assert (recibido["de"]["id"], recibido["tu_color"]) == (gente["eli"]["id"], "blancas")
    assert cliente.post(f"/social/retos/revancha/{codigo}", headers=gente["ana"]["headers"]).status_code == 403  # ana no jugó esa partida
    assert cliente.post("/social/retos/revancha/ZZZZZZ", headers=gente["ana"]["headers"]).status_code == 404


# --- buscar rival ----------------------------------------------------------------------------------------------------------------------


def test_dos_jugadores_de_nivel_parecido_y_mismo_reloj_se_emparejan(social) -> None:
    cliente, gente, fabrica, _ = social
    _nivel(fabrica, gente, "dani", 6)
    _nivel(fabrica, gente, "eli", 7)

    primero = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["dani"]["headers"])
    assert primero.json()["estado"] == "buscando"
    segundo = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["eli"]["headers"])

    assert segundo.json()["estado"] == "encontrado"
    codigo = segundo.json()["codigo"]
    sala = cliente.get(f"/sala/{codigo}", headers=gente["dani"]["headers"]).json()
    assert (sala["estado"], sala["control_tiempo_ms"]) == ("en_curso", CINCO_MIN)
    assert {sala["blancas"]["id"], sala["negras"]["id"]} == {gente["dani"]["id"], gente["eli"]["id"]}
    avisada = _avisos(cliente, gente, "dani").json()["busqueda"]
    assert (avisada["estado"], avisada["codigo"]) == ("encontrado", codigo)
    assert _avisos(cliente, gente, "dani").json()["busqueda"] is None  # se informa una sola vez


def test_no_se_emparejan_si_el_reloj_es_distinto_o_el_nivel_esta_lejos(social, monkeypatch) -> None:
    cliente, gente, fabrica, _ = social
    reloj = {"t": 1000.0}
    monkeypatch.setattr(emparejamiento, "_instante", lambda: reloj["t"])
    _nivel(fabrica, gente, "dani", 2)
    _nivel(fabrica, gente, "eli", 9)
    cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["dani"]["headers"])
    distinto = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": 600_000}, headers=gente["eli"]["headers"])
    assert distinto.json()["estado"] == "buscando"

    cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["eli"]["headers"])  # mismo reloj, pero 7 niveles lejos
    assert _avisos(cliente, gente, "dani").json()["busqueda"]["estado"] == "buscando"

    reloj["t"] += 75  # tras 75 segundos la tolerancia ya llegó a 5 niveles y a los 7 todavía no
    assert _avisos(cliente, gente, "dani").json()["busqueda"]["estado"] == "buscando"
    assert emparejamiento.tolerancia(emparejamiento._cola[gente["dani"]["id"]], reloj["t"]) == 5


def test_con_el_tiempo_la_tolerancia_sube_y_se_emparejan(social, monkeypatch) -> None:
    cliente, gente, fabrica, _ = social
    reloj = {"t": 1000.0}
    monkeypatch.setattr(emparejamiento, "_instante", lambda: reloj["t"])
    _nivel(fabrica, gente, "dani", 4)
    _nivel(fabrica, gente, "eli", 7)  # 3 niveles de diferencia: no alcanza al principio
    cliente.post("/social/emparejamiento", json={}, headers=gente["dani"]["headers"])
    assert cliente.post("/social/emparejamiento", json={}, headers=gente["eli"]["headers"]).json()["estado"] == "buscando"

    reloj["t"] += 31  # dos niveles más de tolerancia

    assert _avisos(cliente, gente, "dani").json()["busqueda"]["estado"] == "encontrado"


def test_los_del_curso_no_se_emparejan_con_los_que_no_tienen_curso(social) -> None:
    cliente, gente, fabrica, _ = social
    for nombre in ("ana", "dani"):
        _nivel(fabrica, gente, nombre, 5)
    cliente.post("/social/emparejamiento", json={}, headers=gente["ana"]["headers"])

    afuera = cliente.post("/social/emparejamiento", json={}, headers=gente["dani"]["headers"])
    adentro = cliente.post("/social/emparejamiento", json={}, headers=gente["beto"]["headers"])

    assert afuera.json()["estado"] == "buscando"
    assert adentro.json()["estado"] == "encontrado"  # beto es del curso de ana


def test_se_puede_dejar_de_buscar_y_sin_rival_la_busqueda_termina(social, monkeypatch) -> None:
    cliente, gente, _, _ = social
    reloj = {"t": 1000.0}
    monkeypatch.setattr(emparejamiento, "_instante", lambda: reloj["t"])
    cliente.post("/social/emparejamiento", json={}, headers=gente["dani"]["headers"])
    assert cliente.delete("/social/emparejamiento", headers=gente["dani"]["headers"]).status_code == 204
    assert _avisos(cliente, gente, "dani").json()["busqueda"] is None

    cliente.post("/social/emparejamiento", json={}, headers=gente["dani"]["headers"])
    reloj["t"] += emparejamiento.SEGUNDOS_DE_BUSQUEDA + 1

    assert _avisos(cliente, gente, "dani").json()["busqueda"]["estado"] == "sin_rival"
    assert _avisos(cliente, gente, "dani").json()["busqueda"] is None
    assert cliente.post("/social/emparejamiento", json={"control_tiempo_ms": 12_345}, headers=gente["dani"]["headers"]).status_code == 400


# --- lo que ve el docente --------------------------------------------------------------------------------------------------------------


def test_el_docente_ve_las_salas_abiertas_de_sus_alumnos_y_nadie_mas(social) -> None:
    cliente, gente, _, _ = social
    codigo = _retar(cliente, gente, "ana", "beto", control_tiempo_ms=CINCO_MIN).json()["codigo"]
    cliente.post(f"/social/retos/{_avisos(cliente, gente, 'beto').json()['retos_recibidos'][0]['id']}/aceptar", headers=gente["beto"]["headers"])
    cliente.post("/sala", json={}, headers=gente["dani"]["headers"])  # una sala de alguien que no es alumno del docente

    del_docente = cliente.get("/social/partidas-en-vivo", headers=gente["docente"]["headers"])
    de_la_plataforma = cliente.get("/social/partidas-en-vivo", headers=gente["plataforma"]["headers"])

    assert del_docente.status_code == 200
    assert [(p["codigo"], p["estado"], p["control_tiempo_ms"]) for p in del_docente.json()] == [(codigo, "en_curso", CINCO_MIN)]
    sala = del_docente.json()[0]
    assert {sala["blancas"]["id"], sala["negras"]["id"]} == {gente["ana"]["id"], gente["beto"]["id"]}
    assert sala["blancas"]["nombre"] == "Persona Prueba"  # el docente ve el nombre completo
    assert len(de_la_plataforma.json()) == 2  # la plataforma las ve todas
    assert cliente.get("/social/partidas-en-vivo", headers=gente["ana"]["headers"]).status_code == 403  # un alumno no
    assert cliente.get(f"/sala/{codigo}", headers=gente["docente"]["headers"]).json()["rol"] == "espectador"  # y puede mirarla en vivo


# --- cuenta eliminada ------------------------------------------------------------------------------------------------------------------


def test_al_eliminar_una_cuenta_se_borran_sus_amistades_y_sus_salas(social) -> None:
    cliente, gente, fabrica, _ = social
    cliente.post("/social/amigos/solicitudes", json={"email": "dani@test.com"}, headers=gente["eli"]["headers"])
    solicitud = cliente.get("/social/amigos", headers=gente["dani"]["headers"]).json()["solicitudes_recibidas"][0]
    cliente.post(f"/social/amigos/solicitudes/{solicitud['id']}/aceptar", headers=gente["dani"]["headers"])
    codigo = _retar(cliente, gente, "eli", "dani").json()["codigo"]
    cliente.post(f"/social/retos/{_avisos(cliente, gente, 'dani').json()['retos_recibidos'][0]['id']}/aceptar", headers=gente["dani"]["headers"])

    borrado = cliente.delete(f"/auth/usuarios/{gente['dani']['id']}", headers=gente["plataforma"]["headers"])

    assert borrado.status_code in (200, 204)
    with fabrica() as db:
        assert db.query(AmistadORM).count() == 0
    assert cliente.get(f"/sala/{codigo}", headers=gente["eli"]["headers"]).status_code == 404
    assert cliente.get("/social/amigos", headers=gente["eli"]["headers"]).json()["gente"] == []
