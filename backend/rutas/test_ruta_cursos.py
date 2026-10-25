"""Cursos: el facilitador arma grupos y el jugador entra con un código (SQLite en memoria)."""
import pytest
from sqlalchemy import select

from backend.modelos.tablas_orm import CursoORM, InscripcionORM, UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.auth.servicio_invitaciones import MAX_INTENTOS_FALLIDOS, reiniciar_intentos
from backend.servicios.curso.servicio_cursos import preset_efectivo
from backend.servicios.facilitador import servicio_videos


@pytest.fixture(autouse=True)
def _intentos_limpios():
    reiniciar_intentos()
    yield
    reiniciar_intentos()


def _crear(cliente, facilitador, nombre="Ajedrez 1ro A") -> dict:
    respuesta = cliente.post("/cursos", json={"nombre": nombre}, headers=facilitador["headers"])
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _unirse(cliente, jugador, codigo: str):
    return cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=jugador["headers"])


def test_solo_un_facilitador_gestiona_cursos(contexto) -> None:
    cliente, registrar, _ = contexto
    jugador = registrar("alumno@test.com")

    assert cliente.post("/cursos", json={"nombre": "Mi curso"}, headers=jugador["headers"]).status_code == 403
    assert cliente.get("/cursos", headers=jugador["headers"]).status_code == 403
    assert cliente.post("/cursos", json={"nombre": "Mi curso"}).status_code == 401


def test_el_facilitador_crea_un_curso_con_codigo_y_lo_ve_en_su_lista(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")

    curso = _crear(cliente, facilitador)

    assert len(curso["codigo"]) == 7 and curso["codigo"][3] == "-" and curso["estudiantes"] == 0
    assert curso["acepta_ingresos"] is True
    assert cliente.get("/cursos", headers=facilitador["headers"]).json() == [curso]
    assert cliente.post("/cursos", json={"nombre": "x"}, headers=facilitador["headers"]).status_code == 422


def test_el_jugador_entra_con_el_codigo_en_cualquier_formato(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, jugador = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")
    curso = _crear(cliente, facilitador)

    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json() is None
    respuesta = _unirse(cliente, jugador, curso["codigo"].lower().replace("-", " "))

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["nombre"] == "Ajedrez 1ro A" and respuesta.json()["facilitador_nombre"] == "Persona Prueba"
    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json()["id"] == curso["id"]
    estudiantes = cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=facilitador["headers"]).json()
    assert [e["id"] for e in estudiantes] == [jugador["id"]]
    assert cliente.get("/cursos", headers=facilitador["headers"]).json()[0]["estudiantes"] == 1


def test_un_codigo_malo_da_400_y_tras_varios_intentos_429(contexto) -> None:
    cliente, registrar, _ = contexto
    jugador = registrar("alumno@test.com")

    for _ in range(MAX_INTENTOS_FALLIDOS):
        assert _unirse(cliente, jugador, "ZZZ-ZZZ").status_code == 400
    assert _unirse(cliente, jugador, "ZZZ-ZZZ").status_code == 429


def test_un_facilitador_no_puede_inscribirse_en_un_curso(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, otro = registrar("prof@test.com", "facilitador"), registrar("prof2@test.com", "facilitador")
    curso = _crear(cliente, facilitador)

    respuesta = _unirse(cliente, otro, curso["codigo"])

    assert respuesta.status_code == 400 and "jugadores" in respuesta.json()["detail"]


def test_cerrar_los_ingresos_hace_que_el_codigo_no_sirva_hasta_reabrir(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, jugador = registrar("prof@test.com", "facilitador"), registrar("alumno@test.com")
    curso = _crear(cliente, facilitador)

    cerrado = cliente.patch(f"/cursos/{curso['id']}", json={"acepta_ingresos": False}, headers=facilitador["headers"])
    assert cerrado.status_code == 200 and cerrado.json()["acepta_ingresos"] is False
    assert _unirse(cliente, jugador, curso["codigo"]).status_code == 400

    cliente.patch(f"/cursos/{curso['id']}", json={"acepta_ingresos": True, "nombre": "Renombrado"}, headers=facilitador["headers"])
    assert _unirse(cliente, jugador, curso["codigo"]).json()["nombre"] == "Renombrado"


def test_regenerar_el_codigo_invalida_el_viejo_pero_no_saca_a_los_inscritos(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, jugador, nuevo = registrar("prof@test.com", "facilitador"), registrar("a@test.com"), registrar("b@test.com")
    curso = _crear(cliente, facilitador)
    _unirse(cliente, jugador, curso["codigo"])

    regenerado = cliente.post(f"/cursos/{curso['id']}/codigo", headers=facilitador["headers"]).json()

    assert regenerado["codigo"] != curso["codigo"] and regenerado["estudiantes"] == 1
    assert _unirse(cliente, nuevo, curso["codigo"]).status_code == 400
    assert _unirse(cliente, nuevo, regenerado["codigo"]).status_code == 200
    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json()["id"] == curso["id"]


def test_entrar_a_otro_curso_lo_mueve_y_un_jugador_esta_en_uno_solo(contexto) -> None:
    cliente, registrar, fabrica = contexto
    profe_a, profe_b, jugador = registrar("a@test.com", "facilitador"), registrar("b@test.com", "facilitador"), registrar("j@test.com")
    curso_a, curso_b = _crear(cliente, profe_a, "Curso A"), _crear(cliente, profe_b, "Curso B")

    _unirse(cliente, jugador, curso_a["codigo"])
    _unirse(cliente, jugador, curso_b["codigo"])

    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json()["nombre"] == "Curso B"
    with fabrica() as sesion:
        assert len(sesion.scalars(select(InscripcionORM)).all()) == 1


def _solo_principal(monkeypatch, correo: str) -> None:
    """Después de registrar a todos (con `*@test.com`, como en `conftest.py`), deja como único principal a `correo`."""
    monkeypatch.setenv("CORREOS_FACILITADORES", correo)


def test_un_facilitador_no_ve_ni_toca_los_cursos_de_otro(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    profe_a, profe_b = registrar("a@test.com", "facilitador"), registrar("b@test.com", "facilitador")
    curso = _crear(cliente, profe_a)
    _solo_principal(monkeypatch, "otro@test.com")  # ni a ni b son principales

    assert cliente.get("/cursos", headers=profe_b["headers"]).json() == []
    assert cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=profe_b["headers"]).status_code == 404
    assert cliente.patch(f"/cursos/{curso['id']}", json={"nombre": "Robado"}, headers=profe_b["headers"]).status_code == 404
    assert cliente.post(f"/cursos/{curso['id']}/codigo", headers=profe_b["headers"]).status_code == 404
    assert cliente.delete(f"/cursos/{curso['id']}", headers=profe_b["headers"]).status_code == 404


def test_quitar_un_estudiante_y_salir_por_cuenta_propia(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, uno, dos = registrar("prof@test.com", "facilitador"), registrar("a@test.com"), registrar("b@test.com")
    curso = _crear(cliente, facilitador)
    _unirse(cliente, uno, curso["codigo"])
    _unirse(cliente, dos, curso["codigo"])

    assert cliente.delete(f"/cursos/{curso['id']}/estudiantes/{uno['id']}", headers=facilitador["headers"]).status_code == 204
    assert cliente.delete(f"/cursos/{curso['id']}/estudiantes/{uno['id']}", headers=facilitador["headers"]).status_code == 404
    assert cliente.get("/cursos/mio", headers=uno["headers"]).json() is None

    assert cliente.post("/cursos/salir", headers=dos["headers"]).status_code == 204
    assert cliente.post("/cursos/salir", headers=dos["headers"]).status_code == 404


def test_eliminar_un_curso_no_borra_a_los_estudiantes(contexto) -> None:
    cliente, registrar, fabrica = contexto
    facilitador, jugador = registrar("prof@test.com", "facilitador"), registrar("a@test.com")
    curso = _crear(cliente, facilitador)
    _unirse(cliente, jugador, curso["codigo"])

    assert cliente.delete(f"/cursos/{curso['id']}", headers=facilitador["headers"]).status_code == 204

    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json() is None
    with fabrica() as sesion:
        assert sesion.get(UsuarioORM, jugador["id"]) is not None and sesion.scalars(select(CursoORM)).all() == []


def _subir_video(cliente, facilitador, pieza: str) -> None:
    respuesta = cliente.post(
        f"/facilitador/videos/{pieza}", files={"archivo": ("v.mp4", b"\x00\x00\x00\x18ftypmp42video", "video/mp4")}, headers=facilitador["headers"]
    )
    assert respuesta.status_code == 200, respuesta.text


def _piezas(cliente, jugador) -> dict:
    """`pieza -> nombre del archivo` de los videos que ve el jugador (sin la ruta ni la versión)."""
    videos = cliente.get("/cursos/mio/videos", headers=jugador["headers"]).json()
    return {pieza: url.split("/")[-1].split("?")[0] for pieza, url in videos.items()}


def test_sin_curso_el_jugador_ve_los_videos_generales_del_principal(contexto, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)
    cliente, registrar, _ = contexto
    principal, comun, jugador = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("j@test.com")
    _subir_video(cliente, principal, "rey")
    _subir_video(cliente, comun, "dama")  # un facilitador común no es general
    _solo_principal(monkeypatch, "admin@test.com")

    assert _piezas(cliente, jugador) == {"rey": f"{principal['id']}_rey.mp4"}
    assert ".mp4?v=" in cliente.get("/cursos/mio/videos", headers=jugador["headers"]).json()["rey"]


def test_el_video_del_facilitador_del_curso_reemplaza_al_general_solo_en_esa_pieza(contexto, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)
    cliente, registrar, _ = contexto
    principal, comun, jugador = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("j@test.com")
    for pieza in ("rey", "dama"):
        _subir_video(cliente, principal, pieza)
    _subir_video(cliente, comun, "rey")
    _subir_video(cliente, comun, "torre")
    curso = _crear(cliente, comun)
    _solo_principal(monkeypatch, "admin@test.com")
    assert _piezas(cliente, jugador) == {"rey": f"{principal['id']}_rey.mp4", "dama": f"{principal['id']}_dama.mp4"}

    _unirse(cliente, jugador, curso["codigo"])

    assert _piezas(cliente, jugador) == {
        "rey": f"{comun['id']}_rey.mp4",  # el del facilitador reemplaza al general
        "dama": f"{principal['id']}_dama.mp4",  # sin video propio: el general
        "torre": f"{comun['id']}_torre.mp4",  # solo el facilitador lo subió
    }


def test_quitar_el_video_propio_devuelve_el_general(contexto, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)
    cliente, registrar, _ = contexto
    principal, comun, jugador = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("j@test.com")
    _subir_video(cliente, principal, "rey")
    _subir_video(cliente, comun, "rey")
    _unirse(cliente, jugador, _crear(cliente, comun)["codigo"])
    _solo_principal(monkeypatch, "admin@test.com")
    assert _piezas(cliente, jugador)["rey"] == f"{comun['id']}_rey.mp4"

    assert cliente.delete("/facilitador/videos/rey", headers=comun["headers"]).status_code == 204

    assert _piezas(cliente, jugador)["rey"] == f"{principal['id']}_rey.mp4"
    assert cliente.get("/facilitador/videos", headers=comun["headers"]).json() == {}
    assert cliente.delete("/facilitador/videos/rey", headers=comun["headers"]).status_code == 404  # ya no tenía
    assert cliente.delete("/facilitador/videos/reina", headers=comun["headers"]).status_code == 400
    assert cliente.delete("/facilitador/videos/rey", headers=jugador["headers"]).status_code == 403
    assert cliente.delete("/facilitador/videos/rey").status_code == 401


def test_con_varios_principales_gana_el_mas_antiguo_que_haya_subido_esa_pieza(contexto, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)
    cliente, registrar, _ = contexto
    primero, segundo, jugador = registrar("a-admin@test.com", "facilitador"), registrar("b-admin@test.com", "facilitador"), registrar("j@test.com")
    _subir_video(cliente, segundo, "rey")
    _subir_video(cliente, segundo, "dama")
    _subir_video(cliente, primero, "dama")
    _solo_principal(monkeypatch, "*-admin@test.com")

    assert _piezas(cliente, jugador) == {"rey": f"{segundo['id']}_rey.mp4", "dama": f"{primero['id']}_dama.mp4"}


def test_el_tono_del_facilitador_llega_a_sus_estudiantes(contexto) -> None:
    cliente, registrar, fabrica = contexto
    facilitador, jugador = registrar("prof@test.com", "facilitador"), registrar("a@test.com")
    cliente.patch("/auth/me", json={"preset_ensenanza": "infantil"}, headers=facilitador["headers"])
    curso = _crear(cliente, facilitador)

    with fabrica() as sesion:
        assert preset_efectivo(sesion, sesion.get(UsuarioORM, jugador["id"])) is None  # sin curso: su propio tono
    assert _unirse(cliente, jugador, curso["codigo"]).json()["preset_ensenanza"] == "infantil"
    with fabrica() as sesion:
        assert preset_efectivo(sesion, sesion.get(UsuarioORM, jugador["id"])) == "infantil"


def test_eliminar_cuentas_limpia_los_cursos(contexto) -> None:
    cliente, registrar, fabrica = contexto
    admin, profe, jugador = registrar("admin@test.com", "facilitador"), registrar("prof@test.com", "facilitador"), registrar("a@test.com")
    curso = _crear(cliente, profe)
    _unirse(cliente, jugador, curso["codigo"])

    assert cliente.delete(f"/auth/usuarios/{jugador['id']}", headers=admin["headers"]).status_code in (200, 204)
    with fabrica() as sesion:
        assert sesion.scalars(select(InscripcionORM)).all() == []
        assert len(sesion.scalars(select(CursoORM)).all()) == 1

    assert cliente.delete(f"/auth/usuarios/{profe['id']}", headers=admin["headers"]).status_code in (200, 204)
    with fabrica() as sesion:
        assert sesion.scalars(select(CursoORM)).all() == []


# --- facilitador principal (correo en CORREOS_FACILITADORES) -----------------------------------------------------
def test_la_respuesta_de_perfil_dice_si_es_principal(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun, jugador = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("j@test.com")
    _solo_principal(monkeypatch, "admin@test.com")

    assert cliente.get("/auth/me", headers=principal["headers"]).json()["es_principal"] is True
    assert cliente.get("/auth/me", headers=comun["headers"]).json()["es_principal"] is False
    assert cliente.get("/auth/me", headers=jugador["headers"]).json()["es_principal"] is False


def test_el_principal_ve_todos_los_cursos_con_su_dueno_y_los_demas_no(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador")
    _crear(cliente, comun, "Curso del común")
    _crear(cliente, principal, "Curso del principal")
    _solo_principal(monkeypatch, "admin@test.com")

    todos = cliente.get("/cursos?todos=true", headers=principal["headers"]).json()
    assert sorted(c["nombre"] for c in todos) == ["Curso del común", "Curso del principal"]
    assert {c["facilitador_id"] for c in todos} == {principal["id"], comun["id"]}
    assert all(c["facilitador_nombre"] for c in todos)
    assert [c["nombre"] for c in cliente.get("/cursos", headers=principal["headers"]).json()] == ["Curso del principal"]
    assert cliente.get("/cursos?todos=true", headers=comun["headers"]).status_code == 403


def test_el_principal_crea_un_curso_para_otro_facilitador_y_un_comun_no(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun, otro = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("otro@test.com", "facilitador")
    jugador = registrar("j@test.com")
    _solo_principal(monkeypatch, "admin@test.com")

    creado = cliente.post("/cursos", json={"nombre": "Para el común", "facilitador_id": comun["id"]}, headers=principal["headers"])
    assert creado.status_code == 201 and creado.json()["facilitador_id"] == comun["id"]
    assert [c["nombre"] for c in cliente.get("/cursos", headers=comun["headers"]).json()] == ["Para el común"]

    robo = cliente.post("/cursos", json={"nombre": "Ajeno", "facilitador_id": otro["id"]}, headers=comun["headers"])
    assert robo.status_code == 403
    # un destino que no es facilitador activo se rechaza
    assert cliente.post("/cursos", json={"nombre": "A un jugador", "facilitador_id": jugador["id"]}, headers=principal["headers"]).status_code == 400
    assert cliente.post("/cursos", json={"nombre": "Inexistente", "facilitador_id": 9999}, headers=principal["headers"]).status_code == 400


def test_el_principal_reasigna_un_curso_y_los_estudiantes_siguen_con_el_nuevo_facilitador(contexto, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(servicio_videos, "MEDIA_VIDEOS_DIR", tmp_path)
    cliente, registrar, fabrica = contexto
    principal, viejo, nuevo = registrar("admin@test.com", "facilitador"), registrar("viejo@test.com", "facilitador"), registrar("nuevo@test.com", "facilitador")
    jugador = registrar("j@test.com")
    cliente.patch("/auth/me", json={"preset_ensenanza": "infantil"}, headers=viejo["headers"])
    cliente.patch("/auth/me", json={"preset_ensenanza": "adultos"}, headers=nuevo["headers"])
    cliente.post("/facilitador/videos/dama", files={"archivo": ("v.mp4", b"\x00\x00\x00\x18ftypmp42video", "video/mp4")}, headers=nuevo["headers"])
    curso = _crear(cliente, viejo)
    _unirse(cliente, jugador, curso["codigo"])
    _solo_principal(monkeypatch, "admin@test.com")
    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json()["preset_ensenanza"] == "infantil"

    reasignado = cliente.patch(f"/cursos/{curso['id']}", json={"facilitador_id": nuevo["id"]}, headers=principal["headers"])

    assert reasignado.status_code == 200, reasignado.text
    assert reasignado.json()["facilitador_id"] == nuevo["id"] and reasignado.json()["estudiantes"] == 1
    del_jugador = cliente.get("/cursos/mio", headers=jugador["headers"]).json()
    assert del_jugador["id"] == curso["id"] and del_jugador["preset_ensenanza"] == "adultos"
    assert list(cliente.get("/cursos/mio/videos", headers=jugador["headers"]).json()) == ["dama"]
    with fabrica() as sesion:
        assert preset_efectivo(sesion, sesion.get(UsuarioORM, jugador["id"])) == "adultos"
    assert cliente.get("/cursos", headers=viejo["headers"]).json() == []
    assert [c["id"] for c in cliente.get("/cursos", headers=nuevo["headers"]).json()] == [curso["id"]]


def test_solo_el_principal_puede_reasignar_y_el_destino_tiene_que_ser_un_facilitador_activo(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun, otro = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("otro@test.com", "facilitador")
    jugador = registrar("j@test.com")
    curso = _crear(cliente, comun)
    _solo_principal(monkeypatch, "admin@test.com")

    assert cliente.patch(f"/cursos/{curso['id']}", json={"facilitador_id": otro["id"]}, headers=comun["headers"]).status_code == 403
    assert cliente.patch(f"/cursos/{curso['id']}", json={"facilitador_id": jugador["id"]}, headers=principal["headers"]).status_code == 400
    # el facilitador dueño sigue pudiendo renombrar el suyo (y "reasignarlo" a sí mismo no es una reasignación)
    propio = cliente.patch(f"/cursos/{curso['id']}", json={"nombre": "Nuevo nombre", "facilitador_id": comun["id"]}, headers=comun["headers"])
    assert propio.status_code == 200 and propio.json()["nombre"] == "Nuevo nombre"

    cliente.patch(f"/auth/usuarios/{otro['id']}", json={"activo": False}, headers=principal["headers"])
    assert cliente.patch(f"/cursos/{curso['id']}", json={"facilitador_id": otro["id"]}, headers=principal["headers"]).status_code == 400


def test_el_principal_administra_cualquier_curso_y_un_comun_solo_los_suyos(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun, ajeno, jugador = (
        registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"),
        registrar("ajeno@test.com", "facilitador"), registrar("j@test.com"),
    )
    curso = _crear(cliente, comun)
    _unirse(cliente, jugador, curso["codigo"])
    _solo_principal(monkeypatch, "admin@test.com")

    assert cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=ajeno["headers"]).status_code == 404
    assert len(cliente.get(f"/cursos/{curso['id']}/estudiantes", headers=principal["headers"]).json()) == 1
    assert cliente.patch(f"/cursos/{curso['id']}", json={"acepta_ingresos": False}, headers=principal["headers"]).status_code == 200
    assert cliente.post(f"/cursos/{curso['id']}/codigo", headers=principal["headers"]).status_code == 200
    assert cliente.delete(f"/cursos/{curso['id']}/estudiantes/{jugador['id']}", headers=principal["headers"]).status_code == 204
    assert cliente.delete(f"/cursos/{curso['id']}", headers=principal["headers"]).status_code == 204


def test_un_curso_no_puede_pasarse_a_un_facilitador_con_el_cupo_lleno(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    principal, lleno, origen = registrar("admin@test.com", "facilitador"), registrar("lleno@test.com", "facilitador"), registrar("origen@test.com", "facilitador")
    curso = _crear(cliente, origen)
    with fabrica() as sesion:
        sesion.add_all([CursoORM(nombre=f"C{n}", facilitador_id=lleno["id"], codigo=f"AAA{n:03d}") for n in range(20)])
        sesion.commit()
    _solo_principal(monkeypatch, "admin@test.com")

    respuesta = cliente.patch(f"/cursos/{curso['id']}", json={"facilitador_id": lleno["id"]}, headers=principal["headers"])

    assert respuesta.status_code == 400 and "máximo" in respuesta.json()["detail"]


# --- cupo de alumnos por curso ------------------------------------------------------------------------------------
def test_el_curso_nace_con_cupo_60_o_el_que_elige_el_creador_y_tiene_tope_120(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")

    assert _crear(cliente, facilitador)["cupo"] == 60
    creado = cliente.post("/cursos", json={"nombre": "Curso chico", "cupo": 2}, headers=facilitador["headers"])
    assert creado.status_code == 201 and creado.json()["cupo"] == 2
    for invalido in (0, 121, -5):
        assert cliente.post("/cursos", json={"nombre": "Mal cupo", "cupo": invalido}, headers=facilitador["headers"]).status_code == 422


def test_un_curso_lleno_rechaza_a_otro_jugador_sin_contarlo_como_codigo_mal_escrito(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    uno, dos, tres = registrar("a@test.com"), registrar("b@test.com"), registrar("c@test.com")
    curso = cliente.post("/cursos", json={"nombre": "De dos", "cupo": 2}, headers=facilitador["headers"]).json()
    assert _unirse(cliente, uno, curso["codigo"]).status_code == 200
    assert _unirse(cliente, dos, curso["codigo"]).status_code == 200

    for _ in range(MAX_INTENTOS_FALLIDOS + 2):  # muchos rechazos por curso lleno: nunca llega al 429
        respuesta = _unirse(cliente, tres, curso["codigo"])
        assert respuesta.status_code == 400 and "completo" in respuesta.json()["detail"]
    assert _unirse(cliente, uno, curso["codigo"]).status_code == 200  # quien ya está adentro no queda afuera
    assert cliente.get("/cursos", headers=facilitador["headers"]).json()[0]["estudiantes"] == 2


def test_subir_el_cupo_deja_entrar_y_no_se_puede_bajar_por_debajo_de_los_inscritos(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador, uno, dos = registrar("prof@test.com", "facilitador"), registrar("a@test.com"), registrar("b@test.com")
    curso = cliente.post("/cursos", json={"nombre": "De uno", "cupo": 1}, headers=facilitador["headers"]).json()
    _unirse(cliente, uno, curso["codigo"])
    assert _unirse(cliente, dos, curso["codigo"]).status_code == 400

    bajar = cliente.patch(f"/cursos/{curso['id']}", json={"cupo": 1}, headers=facilitador["headers"])
    assert bajar.status_code == 200  # igual a los inscritos: permitido
    subir = cliente.patch(f"/cursos/{curso['id']}", json={"cupo": 5}, headers=facilitador["headers"])
    assert subir.status_code == 200 and subir.json()["cupo"] == 5
    assert _unirse(cliente, dos, curso["codigo"]).status_code == 200

    demasiado_bajo = cliente.patch(f"/cursos/{curso['id']}", json={"cupo": 1}, headers=facilitador["headers"])
    assert demasiado_bajo.status_code == 400 and "2 alumnos" in demasiado_bajo.json()["detail"]
    assert cliente.patch(f"/cursos/{curso['id']}", json={"cupo": 500}, headers=facilitador["headers"]).status_code == 422


def test_una_base_con_la_tabla_curso_vieja_recibe_la_columna_cupo() -> None:
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.pool import StaticPool

    from backend.database import _agregar_columnas_faltantes

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conexion:
        conexion.execute(text("CREATE TABLE curso (id INTEGER PRIMARY KEY, nombre VARCHAR NOT NULL, facilitador_id INTEGER, codigo VARCHAR)"))
        conexion.execute(text("INSERT INTO curso (id, nombre, facilitador_id, codigo) VALUES (1, 'Viejo', 1, 'AAA111')"))

    _agregar_columnas_faltantes(engine)

    columnas = {c["name"] for c in inspect(engine).get_columns("curso")}
    assert {"cupo", "preset_ensenanza"} <= columnas
    with engine.connect() as conexion:
        assert conexion.execute(text("SELECT cupo FROM curso WHERE id = 1")).scalar() == 60  # los cursos ya creados quedan en 60


# --- tono de Turing por curso -------------------------------------------------------------------------------------
def test_el_tono_propio_del_curso_manda_sobre_el_del_facilitador_y_se_puede_volver_a_heredar(contexto) -> None:
    cliente, registrar, fabrica = contexto
    profe, jugador = registrar("prof@test.com", "facilitador"), registrar("j@test.com")
    cliente.patch("/auth/me", json={"preset_ensenanza": "adultos"}, headers=profe["headers"])
    sin_tono = _crear(cliente, profe, "Hereda")
    con_tono = cliente.post("/cursos", json={"nombre": "De niños", "preset_ensenanza": "infantil"}, headers=profe["headers"]).json()

    assert (sin_tono["preset_ensenanza"], sin_tono["preset_efectivo"]) == (None, "adultos")
    assert (con_tono["preset_ensenanza"], con_tono["preset_efectivo"]) == ("infantil", "infantil")

    assert _unirse(cliente, jugador, con_tono["codigo"]).json()["preset_ensenanza"] == "infantil"
    with fabrica() as sesion:
        assert preset_efectivo(sesion, sesion.get(UsuarioORM, jugador["id"])) == "infantil"

    heredar = cliente.patch(f"/cursos/{con_tono['id']}", json={"preset_ensenanza": "facilitador"}, headers=profe["headers"]).json()
    assert (heredar["preset_ensenanza"], heredar["preset_efectivo"]) == (None, "adultos")
    assert cliente.get("/cursos/mio", headers=jugador["headers"]).json()["preset_ensenanza"] == "adultos"
    with fabrica() as sesion:
        assert preset_efectivo(sesion, sesion.get(UsuarioORM, jugador["id"])) == "adultos"

    cambiado = cliente.patch(f"/cursos/{con_tono['id']}", json={"preset_ensenanza": "estandar"}, headers=profe["headers"]).json()
    assert cambiado["preset_efectivo"] == "estandar"
    # editar otra cosa no toca el tono
    assert cliente.patch(f"/cursos/{con_tono['id']}", json={"nombre": "Otro nombre"}, headers=profe["headers"]).json()["preset_ensenanza"] == "estandar"
    assert cliente.post("/cursos", json={"nombre": "Mal tono", "preset_ensenanza": "gracioso"}, headers=profe["headers"]).status_code == 422


def test_un_curso_reasignado_conserva_su_tono_propio(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, viejo, nuevo = registrar("admin@test.com", "facilitador"), registrar("viejo@test.com", "facilitador"), registrar("nuevo@test.com", "facilitador")
    cliente.patch("/auth/me", json={"preset_ensenanza": "adultos"}, headers=nuevo["headers"])
    curso = cliente.post("/cursos", json={"nombre": "Infantil", "preset_ensenanza": "infantil"}, headers=viejo["headers"]).json()
    sin_tono = _crear(cliente, viejo, "Sin tono")
    _solo_principal(monkeypatch, "admin@test.com")

    for c in (curso, sin_tono):
        assert cliente.patch(f"/cursos/{c['id']}", json={"facilitador_id": nuevo["id"]}, headers=principal["headers"]).status_code == 200

    por_nombre = {c["nombre"]: c for c in cliente.get("/cursos", headers=nuevo["headers"]).json()}
    assert por_nombre["Infantil"]["preset_efectivo"] == "infantil"  # conserva el suyo
    assert por_nombre["Sin tono"]["preset_efectivo"] == "adultos"  # pasa a heredar el del nuevo facilitador
