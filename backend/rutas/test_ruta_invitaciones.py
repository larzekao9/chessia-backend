"""Códigos de invitación: la única forma de crear un facilitador nuevo (con correo o con Google)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.modelos.tablas_orm import CodigoInvitacionORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)


def _sin_atajos(monkeypatch) -> None:
    """Deja autorizado solo a `prof@test.com` (la plataforma) en vez del comodín de `conftest.py`: así rige la regla real y
    las demás cuentas necesitan un código de invitación."""
    monkeypatch.setenv("CORREOS_FACILITADORES", "prof@test.com")


def _generar(cliente, headers, **cuerpo) -> dict:
    respuesta = cliente.post("/auth/codigos-facilitador", json=cuerpo, headers=headers)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _registro(cliente, email: str, codigo: str | None, rol: str = "facilitador"):
    cuerpo = {"email": email, "nombre": "Persona Nueva", "password": "secreto1", "rol": rol}
    if codigo is not None:
        cuerpo["clave_facilitador"] = codigo
    return cliente.post("/auth/registro", json=cuerpo)


def _google(cliente, email: str, codigo: str | None, rol: str = "facilitador"):
    cuerpo = {"credential": f"demo_{email}", "rol_seleccionado": rol}
    if codigo is not None:
        cuerpo["clave_facilitador"] = codigo
    return cliente.post("/auth/google", json=cuerpo)


def test_un_jugador_no_puede_generar_ni_ver_codigos(contexto) -> None:
    cliente, registrar, _ = contexto
    jugador = registrar("alumno-codigos@test.com")

    assert cliente.post("/auth/codigos-facilitador", json={}, headers=jugador["headers"]).status_code == 403
    assert cliente.get("/auth/codigos-facilitador", headers=jugador["headers"]).status_code == 403
    assert cliente.post("/auth/codigos-facilitador", json={}).status_code == 401


def test_el_codigo_generado_tiene_formato_y_no_se_guarda_en_claro(contexto) -> None:
    cliente, registrar, fabrica = contexto
    facilitador = registrar("prof@test.com", "facilitador")

    creado = _generar(cliente, facilitador["headers"], minutos=10, para="Prof. Gómez")

    assert len(creado["codigo"]) == 9 and creado["codigo"][4] == "-" and creado["minutos"] == 10
    with fabrica() as sesion:
        fila = sesion.scalars(select(CodigoInvitacionORM)).one()
        assert creado["codigo"].replace("-", "") not in fila.codigo_hash and fila.para == "Prof. Gómez"
    listado = cliente.get("/auth/codigos-facilitador", headers=facilitador["headers"]).json()
    assert listado[0]["estado"] == "vigente" and "codigo" not in listado[0]


def test_la_duracion_del_codigo_tiene_limites(contexto) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")

    # 0 y más de 7 días no entran en el esquema (422); entre 24 horas y 7 días solo valen para un código de institución (400 sin ella)
    for minutos in (0, 7 * 24 * 60 + 1):
        assert cliente.post("/auth/codigos-facilitador", json={"minutos": minutos}, headers=facilitador["headers"]).status_code == 422
    assert cliente.post("/auth/codigos-facilitador", json={"minutos": 24 * 60 + 1}, headers=facilitador["headers"]).status_code == 400


def test_sin_codigo_no_se_puede_registrar_un_facilitador(contexto, monkeypatch) -> None:
    cliente, *_ = contexto
    _sin_atajos(monkeypatch)

    sin_codigo = _registro(cliente, "nuevo@test.com", None)  # ya no vale el atajo de @test.com
    equivocado = _registro(cliente, "nuevo@test.com", "ABCD-EFGH")
    como_jugador = _registro(cliente, "nuevo@test.com", None, rol="jugador")

    assert sin_codigo.status_code == equivocado.status_code == 400
    assert "invitación" in sin_codigo.json()["detail"]
    assert como_jugador.status_code == 201 and como_jugador.json()["usuario"]["rol"] == "jugador"


def test_con_un_codigo_valido_se_registra_un_facilitador_y_el_codigo_se_gasta(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    codigo = _generar(cliente, facilitador["headers"], para="Ana")["codigo"]
    _sin_atajos(monkeypatch)

    primero = _registro(cliente, "ana@uni.edu", codigo.lower().replace("-", " "))  # minúsculas y sin guion también sirven
    segundo = _registro(cliente, "beto@uni.edu", codigo)

    assert primero.status_code == 201 and primero.json()["usuario"]["rol"] == "facilitador"
    assert segundo.status_code == 400  # el código sirve una sola vez
    estado = cliente.get("/auth/codigos-facilitador", headers=facilitador["headers"]).json()[0]
    assert estado["estado"] == "usado" and estado["usado_por"] == "Persona Nueva"


def test_un_codigo_vencido_no_sirve(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    codigo = _generar(cliente, facilitador["headers"], minutos=5)["codigo"]
    with fabrica() as sesion:
        fila = sesion.scalars(select(CodigoInvitacionORM)).one()
        fila.expira_en = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        sesion.commit()
    _sin_atajos(monkeypatch)

    assert _registro(cliente, "tarde@uni.edu", codigo).status_code == 400
    assert cliente.get("/auth/codigos-facilitador", headers=facilitador["headers"]).json()[0]["estado"] == "vencido"


def test_un_codigo_anulado_no_sirve(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    creado = _generar(cliente, facilitador["headers"])
    _sin_atajos(monkeypatch)

    assert cliente.delete(f"/auth/codigos-facilitador/{creado['id']}", headers=facilitador["headers"]).status_code == 204
    assert _registro(cliente, "ana@uni.edu", creado["codigo"]).status_code == 400
    assert cliente.delete(f"/auth/codigos-facilitador/{creado['id']}", headers=facilitador["headers"]).status_code == 404


def test_un_codigo_usado_no_se_puede_anular(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    creado = _generar(cliente, facilitador["headers"])
    _sin_atajos(monkeypatch)
    assert _registro(cliente, "ana@uni.edu", creado["codigo"]).status_code == 201

    assert cliente.delete(f"/auth/codigos-facilitador/{creado['id']}", headers=facilitador["headers"]).status_code == 404


def test_con_google_una_cuenta_nueva_de_facilitador_tambien_pide_el_codigo(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    codigo = _generar(cliente, facilitador["headers"])["codigo"]
    _sin_atajos(monkeypatch)

    sin_codigo = _google(cliente, "docente@gmail.com", None)
    equivocado = _google(cliente, "docente@gmail.com", "ZZZZ-ZZZZ")
    con_codigo = _google(cliente, "docente@gmail.com", codigo)
    reutilizado = _google(cliente, "otro@gmail.com", codigo)

    assert sin_codigo.status_code == equivocado.status_code == 400
    assert "invitación" in sin_codigo.json()["detail"]
    assert con_codigo.status_code == 200 and con_codigo.json()["usuario"]["rol"] == "facilitador"
    assert reutilizado.status_code == 400


def test_con_google_un_jugador_nuevo_entra_sin_codigo(contexto, monkeypatch) -> None:
    cliente, *_ = contexto
    _sin_atajos(monkeypatch)

    respuesta = _google(cliente, "alumno@gmail.com", None, rol="jugador")

    assert respuesta.status_code == 200 and respuesta.json()["usuario"]["rol"] == "jugador"


def test_con_google_un_correo_autorizado_no_obliga_a_ser_facilitador(contexto, monkeypatch) -> None:
    cliente, *_ = contexto
    monkeypatch.setenv("CORREOS_FACILITADORES", "admin@uni.edu")

    como_jugador = _google(cliente, "admin@uni.edu", None, rol="jugador")

    assert como_jugador.json()["usuario"]["rol"] == "jugador"  # antes el correo autorizado forzaba el rol


def test_un_facilitador_que_ya_existe_entra_con_google_sin_codigo(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    registrar("docente@test.com", "facilitador")
    _sin_atajos(monkeypatch)

    respuesta = _google(cliente, "docente@test.com", None)

    assert respuesta.status_code == 200 and respuesta.json()["usuario"]["rol"] == "facilitador"


def test_los_correos_configurados_pueden_ser_el_primer_facilitador_sin_codigo(contexto, monkeypatch) -> None:
    cliente, *_ = contexto
    monkeypatch.setenv("CORREOS_FACILITADORES", "Admin@Uni.edu, *@mi-escuela.org")

    exacto = _registro(cliente, "admin@uni.edu", None)
    patron = _registro(cliente, "profe@mi-escuela.org", None)
    otro = _registro(cliente, "intruso@gmail.com", None)

    assert exacto.status_code == patron.status_code == 201 and exacto.json()["usuario"]["rol"] == "facilitador"
    assert otro.status_code == 400


def test_ya_no_hay_correos_autorizados_de_fabrica(contexto, monkeypatch) -> None:
    cliente, *_ = contexto
    _sin_atajos(monkeypatch)

    for correo in ("facilitador@test.com", "admin@kairos-chess.ai", "suarezburgoshebert@gmail.com"):
        assert _registro(cliente, correo, None).status_code == 400, correo


def test_eliminar_a_un_facilitador_que_genero_codigos_no_deja_basura(contexto) -> None:
    cliente, registrar, fabrica = contexto
    uno, otro = registrar("uno@test.com", "facilitador"), registrar("otro@test.com", "facilitador")
    _generar(cliente, uno["headers"])

    respuesta = cliente.delete(f"/auth/usuarios/{uno['id']}", headers=otro["headers"])

    assert respuesta.status_code == 200, respuesta.text
    with fabrica() as sesion:
        assert sesion.scalars(select(CodigoInvitacionORM)).all() == []


def _verificar(cliente, codigo: str):
    return cliente.post("/auth/codigos-facilitador/verificar", json={"codigo": codigo})


def test_verificar_un_codigo_valido_no_lo_gasta(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    codigo = _generar(cliente, facilitador["headers"], minutos=10)["codigo"]
    _sin_atajos(monkeypatch)

    primera, segunda = _verificar(cliente, codigo), _verificar(cliente, codigo.lower())

    assert primera.status_code == 200 and primera.json()["valido"] is True
    assert 0 < primera.json()["segundos_restantes"] <= 600 and segunda.json()["valido"] is True
    assert _registro(cliente, "ana@uni.edu", codigo).status_code == 201  # sigue sirviendo después de verificarlo


def test_verificar_no_necesita_sesion_y_no_dice_por_que_falla(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    usado = _generar(cliente, facilitador["headers"])["codigo"]
    vencido = _generar(cliente, facilitador["headers"])["codigo"]
    with fabrica() as sesion:
        filas = sesion.scalars(select(CodigoInvitacionORM).order_by(CodigoInvitacionORM.id)).all()
        filas[1].expira_en = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        sesion.commit()
    _sin_atajos(monkeypatch)
    assert _registro(cliente, "ana@uni.edu", usado).status_code == 201

    for codigo in ("ZZZZ-ZZZZ", usado, vencido, "", "   "):
        respuesta = _verificar(cliente, codigo)
        assert respuesta.status_code == 200 and respuesta.json() == {
            "valido": False,
            "segundos_restantes": None,
            "institucion_nombre": None,
            "como_administrador": False,
            "es_evaluacion": False,
        }, codigo  # no dice por qué falla ni a qué institución pertenecía


def test_despues_de_varios_codigos_incorrectos_se_bloquea_al_cliente(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    bueno = _generar(cliente, facilitador["headers"])["codigo"]
    _sin_atajos(monkeypatch)

    for _ in range(8):
        assert _verificar(cliente, "AAAA-AAAA").status_code == 200
    bloqueado = _verificar(cliente, "AAAA-AAAA")
    # Bloqueado también para registrarse con código (aunque sea el bueno) y por Google.
    registro = _registro(cliente, "ana@uni.edu", bueno)
    google = _google(cliente, "nuevo@gmail.com", bueno)

    assert bloqueado.status_code == registro.status_code == google.status_code == 429
    assert "Demasiados intentos" in bloqueado.json()["detail"]
    # Quien no usa códigos no se ve afectado: un jugador se registra normalmente.
    assert _registro(cliente, "alumno@uni.edu", None, rol="jugador").status_code == 201


def test_los_intentos_correctos_no_cuentan_para_el_bloqueo(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    facilitador = registrar("prof@test.com", "facilitador")
    codigo = _generar(cliente, facilitador["headers"])["codigo"]
    _sin_atajos(monkeypatch)

    for _ in range(12):
        assert _verificar(cliente, codigo).json()["valido"] is True
