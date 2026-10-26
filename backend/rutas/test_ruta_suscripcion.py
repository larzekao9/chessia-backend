"""Suscripción de las instituciones: planes, prueba gratis, pagos, tope de alumnos e ingresos (SQLite en memoria)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.modelos.tablas_orm import SuscripcionORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.rutas.test_ruta_instituciones import _crear_institucion, perfiles  # noqa: F401  (fixture y ayuda compartidas)
from backend.servicios.suscripcion import servicio_suscripciones as suscripciones
from backend.servicios.suscripcion.servicio_suscripciones import Plan

AHORA = datetime(2026, 10, 25, 12, 0, 0)


@pytest.fixture
def institucion(perfiles, monkeypatch):
    """Una institución con su director y su docente, y un reloj fijo. Devuelve (cliente, personas, id de la institución)."""
    cliente, personas, _ = perfiles
    monkeypatch.setattr(suscripciones, "_ahora", lambda: AHORA)
    creada = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{creada['id']}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])
    return cliente, personas, creada["id"]


def _mia(cliente, personas):
    return cliente.get("/suscripciones/mia", headers=personas["director"]["headers"])


def _avisar(cliente, personas, plan="plan_60", ciclo="mensual", **extra):
    return cliente.post("/suscripciones/mia/pagos", json={"plan": plan, "ciclo": ciclo, **extra}, headers=personas["director"]["headers"])


def _confirmar(cliente, personas, pago_id):
    return cliente.post(f"/suscripciones/pagos/{pago_id}/confirmar", json={}, headers=personas["plataforma"]["headers"])


def _alumno(cliente, email):
    registro = cliente.post("/auth/registro", json={"email": email, "nombre": "Alumno Prueba", "password": "secreto1"}).json()
    return {"id": registro["usuario"]["id"], "headers": {"Authorization": f"Bearer {registro['tokens']['access_token']}"}}


def _curso_del_docente(cliente, personas) -> str:
    return cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()["codigo"]


# --- precios -----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plan,ciclo,precio,ahorro",
    [
        ("plan_60", "mensual", 350, 0),
        ("plan_60", "trimestral", 945, 105),
        ("plan_60", "anual", 3500, 700),
        ("plan_120", "mensual", 500, 0),
        ("plan_120", "trimestral", 1350, 150),
        ("plan_120", "anual", 5000, 1000),
    ],
)
def test_los_precios_por_ciclo_llevan_el_descuento(plan, ciclo, precio, ahorro) -> None:
    assert suscripciones.precio(plan, ciclo) == precio and suscripciones.ahorro(plan, ciclo) == ahorro


def test_el_catalogo_lo_ve_cualquier_facilitador_y_nadie_mas(institucion) -> None:
    cliente, personas, _ = institucion

    catalogo = cliente.get("/suscripciones/planes", headers=personas["docente"]["headers"]).json()

    assert catalogo["dias_de_prueba"] == 30 and [p["tope_alumnos"] for p in catalogo["planes"]] == [60, 120]
    assert cliente.get("/suscripciones/planes", headers=personas["alumno"]["headers"]).status_code == 403


def test_el_fin_de_mes_no_se_pasa_de_dia() -> None:
    assert suscripciones.sumar_meses(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert suscripciones.sumar_meses(datetime(2026, 11, 15), 3) == datetime(2027, 2, 15)


# --- la prueba gratis y quién ve qué -----------------------------------------------------------------------------------------


def test_una_institucion_nueva_arranca_con_30_dias_de_prueba(institucion) -> None:
    cliente, personas, _ = institucion

    mia = _mia(cliente, personas).json()

    assert (mia["estado"], mia["dias_restantes"], mia["plan"], mia["tope_alumnos"], mia["alumnos"]) == ("prueba", 30, "plan_60", 60, 0)
    assert mia["vigente_hasta"] is None and mia["pagos"] == []


def test_solo_el_administrador_ve_la_suscripcion_de_su_institucion(institucion) -> None:
    cliente, personas, _ = institucion

    assert cliente.get("/suscripciones/mia", headers=personas["docente"]["headers"]).status_code == 403
    assert cliente.get("/suscripciones/mia", headers=personas["alumno"]["headers"]).status_code == 403
    assert cliente.get("/suscripciones/mia").status_code == 401
    assert cliente.get("/suscripciones/mia", headers=personas["plataforma"]["headers"]).status_code == 404  # la plataforma no tiene institución


def test_una_institucion_anterior_a_las_suscripciones_recibe_su_prueba_al_consultarla(institucion, perfiles) -> None:
    cliente, personas, institucion_id = institucion
    _, _, fabrica = perfiles
    with fabrica() as db:
        db.execute(delete(SuscripcionORM))
        db.commit()

    assert _mia(cliente, personas).json()["estado"] == "prueba"


def test_la_plataforma_puede_alargar_la_prueba(institucion) -> None:
    cliente, personas, institucion_id = institucion

    hecha = cliente.post(f"/suscripciones/{institucion_id}/extender-prueba", json={"dias": 15}, headers=personas["plataforma"]["headers"])

    assert hecha.status_code == 200 and hecha.json()["dias_restantes"] == 45
    assert cliente.post(f"/suscripciones/{institucion_id}/extender-prueba", json={"dias": 15}, headers=personas["director"]["headers"]).status_code == 403


# --- pagos -------------------------------------------------------------------------------------------------------------------


def test_un_pago_avisado_no_cambia_nada_hasta_que_la_plataforma_lo_confirma(institucion) -> None:
    cliente, personas, _ = institucion

    aviso = _avisar(cliente, personas, "plan_60", "trimestral", referencia="Transferencia 4521, Juan Pérez")

    assert aviso.status_code == 201 and (aviso.json()["monto_bs"], aviso.json()["estado"]) == (945, "pendiente")
    mia = _mia(cliente, personas).json()
    assert mia["estado"] == "prueba" and mia["pago_pendiente"] is True and mia["vigente_hasta"] is None
    assert _avisar(cliente, personas).status_code == 409  # uno a la vez

    pendientes = cliente.get("/suscripciones/ingresos", headers=personas["plataforma"]["headers"]).json()["pendientes"]
    assert [(p["id"], p["referencia"]) for p in pendientes] == [(aviso.json()["id"], "Transferencia 4521, Juan Pérez")]


def test_confirmar_el_pago_activa_la_suscripcion_por_el_ciclo_pagado(institucion) -> None:
    cliente, personas, _ = institucion
    pago = _avisar(cliente, personas, "plan_120", "trimestral").json()

    confirmado = _confirmar(cliente, personas, pago["id"])
    mia = _mia(cliente, personas).json()

    assert confirmado.status_code == 200 and confirmado.json()["estado"] == "confirmado"
    assert (mia["estado"], mia["plan"], mia["ciclo"], mia["tope_alumnos"]) == ("activa", "plan_120", "trimestral", 120)
    assert mia["vigente_hasta"].startswith("2027-02-24") and mia["pago_pendiente"] is False  # empieza cuando termina la prueba (24/11)
    assert [p["estado"] for p in mia["pagos"]] == ["confirmado"]
    assert _confirmar(cliente, personas, pago["id"]).status_code == 409  # no se confirma dos veces


def test_pagar_antes_de_que_venza_suma_desde_el_vencimiento_y_no_pierde_dias(institucion) -> None:
    cliente, personas, _ = institucion
    _confirmar(cliente, personas, _avisar(cliente, personas, "plan_60", "mensual").json()["id"])  # prueba hasta el 24/11, pagado hasta el 24/12
    _confirmar(cliente, personas, _avisar(cliente, personas, "plan_60", "anual").json()["id"])

    assert _mia(cliente, personas).json()["vigente_hasta"].startswith("2027-12-24")


def test_pagar_durante_la_prueba_conserva_los_dias_que_quedaban(institucion, monkeypatch) -> None:
    cliente, personas, _ = institucion
    monkeypatch.setattr(suscripciones, "_ahora", lambda: AHORA + timedelta(days=10))  # le quedan 20 de los 30 días de prueba

    _confirmar(cliente, personas, _avisar(cliente, personas, "plan_60", "mensual").json()["id"])
    mia = _mia(cliente, personas).json()

    assert mia["prueba_hasta"].startswith("2026-11-24") and mia["vigente_hasta"].startswith("2026-12-24")
    assert mia["estado"] == "activa" and mia["dias_restantes"] == 50  # 20 de prueba + 30 pagados


def test_un_pago_rechazado_no_toca_la_suscripcion_y_se_puede_avisar_de_nuevo(institucion) -> None:
    cliente, personas, _ = institucion
    pago = _avisar(cliente, personas).json()

    rechazado = cliente.post(f"/suscripciones/pagos/{pago['id']}/rechazar", json={"nota": "No llegó la transferencia"}, headers=personas["plataforma"]["headers"])

    assert rechazado.status_code == 200 and rechazado.json()["nota"] == "No llegó la transferencia"
    assert _mia(cliente, personas).json()["estado"] == "prueba"
    assert _avisar(cliente, personas).status_code == 201


def test_solo_la_plataforma_confirma_pagos_y_ve_los_ingresos(institucion) -> None:
    cliente, personas, _ = institucion
    pago = _avisar(cliente, personas).json()

    for quien in ("director", "docente", "alumno"):
        headers = personas[quien]["headers"]
        assert cliente.post(f"/suscripciones/pagos/{pago['id']}/confirmar", json={}, headers=headers).status_code == 403
        assert cliente.get("/suscripciones/ingresos", headers=headers).status_code == 403
        assert cliente.get("/suscripciones", headers=headers).status_code == 403
        assert cliente.post("/suscripciones/demostracion", headers=headers).status_code == 403


def test_los_planes_y_ciclos_invalidos_se_rechazan(institucion) -> None:
    cliente, personas, _ = institucion

    assert _avisar(cliente, personas, "plan_999").status_code == 422
    assert _avisar(cliente, personas, "plan_60", "semanal").status_code == 422


def test_las_acciones_de_dinero_quedan_en_la_auditoria(institucion) -> None:
    cliente, personas, _ = institucion
    _confirmar(cliente, personas, _avisar(cliente, personas).json()["id"])

    acciones = [e["accion"] for e in cliente.get("/auditoria", headers=personas["plataforma"]["headers"]).json()]

    assert "pago.avisar" in acciones and "pago.confirmar" in acciones


# --- el tope de alumnos ------------------------------------------------------------------------------------------------------


@pytest.fixture
def plan_chico(monkeypatch):
    """Un plan de 2 alumnos, para probar el tope sin inscribir a 60 personas."""
    monkeypatch.setitem(suscripciones.PLANES, "plan_60", Plan("plan_60", "Hasta 2 alumnos", 2, 350))


def test_la_institucion_no_inscribe_mas_alumnos_que_el_tope_de_su_plan(institucion, plan_chico) -> None:
    cliente, personas, _ = institucion
    codigo = _curso_del_docente(cliente, personas)
    uno, dos, tres = (_alumno(cliente, f"alumno{i}@test.com") for i in (1, 2, 3))

    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=uno["headers"]).status_code == 200
    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=dos["headers"]).status_code == 200
    lleno = cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=tres["headers"])

    assert lleno.status_code == 400 and "límite" in lleno.json()["detail"]
    assert _mia(cliente, personas).json()["alumnos"] == 2
    # quien ya estaba adentro puede volver a entrar con el mismo código sin ocupar otro lugar
    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=uno["headers"]).status_code == 200


def test_pagar_un_plan_mas_grande_libera_lugares(institucion, plan_chico) -> None:
    cliente, personas, _ = institucion
    codigo = _curso_del_docente(cliente, personas)
    for i in (1, 2):
        cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=_alumno(cliente, f"a{i}@test.com")["headers"])
    tercero = _alumno(cliente, "a3@test.com")
    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=tercero["headers"]).status_code == 400

    _confirmar(cliente, personas, _avisar(cliente, personas, "plan_120", "mensual").json()["id"])

    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=tercero["headers"]).status_code == 200


def test_no_se_puede_elegir_un_plan_que_no_alcanza_para_los_alumnos_que_ya_hay(institucion, monkeypatch) -> None:
    cliente, personas, _ = institucion
    codigo = _curso_del_docente(cliente, personas)
    for i in (1, 2, 3):
        cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=_alumno(cliente, f"a{i}@test.com")["headers"])
    monkeypatch.setitem(suscripciones.PLANES, "plan_60", Plan("plan_60", "Hasta 2 alumnos", 2, 350))

    chico = _avisar(cliente, personas, "plan_60")

    assert chico.status_code == 400 and "3 alumnos" in chico.json()["detail"]
    assert _avisar(cliente, personas, "plan_120").status_code == 201


def test_con_la_suscripcion_vencida_no_entran_alumnos_nuevos_pero_los_que_estan_siguen(institucion, monkeypatch) -> None:
    cliente, personas, _ = institucion
    codigo = _curso_del_docente(cliente, personas)
    de_antes = _alumno(cliente, "antes@test.com")
    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=de_antes["headers"]).status_code == 200

    monkeypatch.setattr(suscripciones, "_ahora", lambda: AHORA + timedelta(days=31))  # terminó la prueba y nadie pagó
    nuevo = _alumno(cliente, "nuevo@test.com")
    bloqueado = cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=nuevo["headers"])

    assert bloqueado.status_code == 400 and "vencida" in bloqueado.json()["detail"]
    assert _mia(cliente, personas).json()["estado"] == "vencida"
    assert cliente.get("/cursos/mio", headers=de_antes["headers"]).json() is not None  # el alumno de antes sigue en su curso


def test_un_curso_de_un_docente_sin_institucion_no_tiene_tope_ni_vencimiento(institucion, monkeypatch) -> None:
    cliente, personas, _ = institucion
    cliente.delete(f"/instituciones/{_}/docentes/{personas['docente']['id']}", headers=personas["plataforma"]["headers"])
    codigo = _curso_del_docente(cliente, personas)
    monkeypatch.setattr(suscripciones, "_ahora", lambda: AHORA + timedelta(days=500))

    assert cliente.post("/cursos/unirse", json={"codigo": codigo}, headers=_alumno(cliente, "libre@test.com")["headers"]).status_code == 200


# --- ingresos y datos de demostración ----------------------------------------------------------------------------------------


def test_los_ingresos_separan_lo_real_de_la_demostracion(institucion) -> None:
    cliente, personas, _ = institucion
    plataforma = personas["plataforma"]["headers"]
    _confirmar(cliente, personas, _avisar(cliente, personas, "plan_60", "anual").json()["id"])

    antes = cliente.get("/suscripciones/ingresos", headers=plataforma).json()
    assert antes["hay_demostracion"] is False
    assert antes["por_mes"][-1] == {"mes": "2026-10", "real_bs": 3500, "demostracion_bs": 0}
    assert antes["mensual_equivalente_real_bs"] == pytest.approx(291.67) and antes["conteo_real"]["activa"] == 1
    assert antes["parametros"]["costos_fijos_mensuales_bs"] == 830

    cargado = cliente.post("/suscripciones/demostracion", headers=plataforma)
    assert cargado.status_code == 201 and cargado.json()["instituciones"] == 5
    assert cliente.post("/suscripciones/demostracion", headers=plataforma).status_code == 409  # ya están cargadas

    con = cliente.get("/suscripciones/ingresos", headers=plataforma).json()
    assert con["hay_demostracion"] is True and sum(con["conteo_demostracion"].values()) == 5
    assert con["por_mes"][-1]["real_bs"] == 3500 and sum(m["demostracion_bs"] for m in con["por_mes"]) > 0  # lo real no se mezcla
    assert [p["es_demostracion"] for p in con["pendientes"]] == [True]
    listadas = cliente.get("/instituciones", headers=plataforma).json()
    assert sum(i["es_demostracion"] for i in listadas) == 5 and all("(demostración)" in i["nombre"] for i in listadas if i["es_demostracion"])

    quitado = cliente.delete("/suscripciones/demostracion", headers=plataforma)
    despues = cliente.get("/suscripciones/ingresos", headers=plataforma).json()
    assert quitado.json()["instituciones"] == 5 and despues["hay_demostracion"] is False
    assert despues["por_mes"][-1]["real_bs"] == 3500 and despues["conteo_real"]["activa"] == 1  # lo real quedó intacto


def test_la_lista_de_la_plataforma_pone_primero_lo_que_pide_atencion(institucion) -> None:
    cliente, personas, institucion_id = institucion
    plataforma = personas["plataforma"]["headers"]
    cliente.post("/instituciones", json={"nombre": "Otra Academia", "tipo": "academia"}, headers=plataforma)
    _avisar(cliente, personas)  # la primera tiene un pago por confirmar

    lista = cliente.get("/suscripciones", headers=plataforma).json()

    assert lista[0]["institucion_id"] == institucion_id and lista[0]["pago_pendiente"] is True
    assert {s["estado"] for s in lista} == {"prueba"}


def test_la_plataforma_lista_todos_los_pagos_y_los_filtra_por_estado(institucion) -> None:
    cliente, personas, _ = institucion
    plataforma = personas["plataforma"]["headers"]
    confirmado = _avisar(cliente, personas, "plan_60", "mensual").json()
    _confirmar(cliente, personas, confirmado["id"])
    pendiente = _avisar(cliente, personas, "plan_60", "trimestral").json()

    todos = cliente.get("/suscripciones/pagos", headers=plataforma).json()
    solo_pendientes = cliente.get("/suscripciones/pagos", params={"estado": "pendiente"}, headers=plataforma).json()

    assert [p["id"] for p in todos] == [pendiente["id"], confirmado["id"]]  # los más nuevos primero
    assert [p["id"] for p in solo_pendientes] == [pendiente["id"]] and solo_pendientes[0]["institucion_nombre"]
    assert cliente.get("/suscripciones/pagos", params={"estado": "otro"}, headers=plataforma).status_code == 422
    assert cliente.get("/suscripciones/pagos", headers=personas["director"]["headers"]).status_code == 403


def test_cada_suscripcion_trae_la_fecha_de_alta_de_su_institucion(institucion) -> None:
    cliente, personas, _ = institucion

    lista = cliente.get("/suscripciones", headers=personas["plataforma"]["headers"]).json()

    assert lista and all(s["institucion_creada_en"] for s in lista)
