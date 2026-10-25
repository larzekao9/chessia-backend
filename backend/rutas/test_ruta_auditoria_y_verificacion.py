"""Auditoría de acciones de los administradores y verificación del correo (SQLite en memoria)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.modelos.tablas_orm import AuditoriaORM, UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.servicios.auditoria import depurar_antiguos
from backend.servicios.auth import servicio_verificacion


@pytest.fixture
def perfiles(contexto, monkeypatch):
    """La plataforma (único correo autorizado), un docente y un jugador."""
    cliente, registrar, fabrica = contexto
    personas = {
        "plataforma": registrar("plataforma@test.com", "facilitador"),
        "docente": registrar("docente@test.com", "facilitador"),
        "alumno": registrar("alumno@test.com"),
    }
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")
    return cliente, personas, fabrica


def _eventos(cliente, personas, **consulta):
    return cliente.get("/auditoria", params=consulta, headers=personas["plataforma"]["headers"]).json()


# --- auditoría -----------------------------------------------------------------------------------------------------------------


def test_solo_la_plataforma_lee_la_auditoria(perfiles) -> None:
    cliente, personas, _ = perfiles

    assert cliente.get("/auditoria", headers=personas["plataforma"]["headers"]).status_code == 200
    assert cliente.get("/auditoria", headers=personas["docente"]["headers"]).status_code == 403
    assert cliente.get("/auditoria", headers=personas["alumno"]["headers"]).status_code == 403
    assert cliente.get("/auditoria").status_code == 401


def test_las_acciones_de_los_administradores_quedan_anotadas_sin_datos_personales(perfiles) -> None:
    cliente, personas, _ = perfiles
    plataforma = personas["plataforma"]["headers"]
    institucion = cliente.post("/instituciones", json={"nombre": "Academia Uno", "tipo": "academia"}, headers=plataforma).json()
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=plataforma)
    cliente.patch(f"/auth/usuarios/{personas['alumno']['id']}", json={"rango_estimado": "Intermedio"}, headers=plataforma)
    codigo = cliente.post("/auth/codigos-facilitador", json={"minutos": 10}, headers=plataforma).json()
    cliente.delete(f"/auth/codigos-facilitador/{codigo['id']}", headers=plataforma)
    cliente.delete(f"/auth/usuarios/{personas['alumno']['id']}", headers=plataforma)

    acciones = [e["accion"] for e in _eventos(cliente, personas)]

    assert acciones == [
        "usuario.eliminar",
        "codigo.anular",
        "codigo.generar",
        "usuario.editar",
        "institucion.docente_asignar",
        "institucion.crear",
    ]
    todo = str(_eventos(cliente, personas))
    assert "alumno@test.com" not in todo and "docente@test.com" not in todo  # solo ids y datos sueltos
    eliminado = _eventos(cliente, personas, accion="usuario.eliminar")[0]
    assert eliminado["objetivo_id"] == personas["alumno"]["id"] and eliminado["actor_id"] == personas["plataforma"]["id"]
    assert eliminado["actor_nombre"] == "Persona Prueba"


def test_la_baja_propia_queda_anotada_y_el_registro_sobrevive_a_la_cuenta(perfiles) -> None:
    cliente, personas, _ = perfiles
    cliente.post("/auth/me/eliminar", json={"password": "secreto1"}, headers=personas["alumno"]["headers"])

    evento = _eventos(cliente, personas, accion="usuario.baja_propia")[0]

    assert evento["actor_id"] == personas["alumno"]["id"] == evento["objetivo_id"]
    assert evento["actor_nombre"] is None  # la cuenta ya no existe, el evento sí


def test_los_eventos_de_mas_de_un_ano_se_depuran(perfiles) -> None:
    cliente, personas, fabrica = perfiles
    cliente.post("/instituciones", json={"nombre": "Academia Uno", "tipo": "academia"}, headers=personas["plataforma"]["headers"])
    with fabrica() as db:
        db.add(AuditoriaORM(accion="vieja", creado_en=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)))
        db.commit()

    assert depurar_antiguos(fabrica) == 1
    assert [e["accion"] for e in _eventos(cliente, personas)] == ["institucion.crear"]


# --- verificación del correo ---------------------------------------------------------------------------------------------------


@pytest.fixture
def correos(monkeypatch):
    """Activa el envío de correos y captura lo que se mandaría (sin hilos ni red)."""
    monkeypatch.setenv("SMTP_HOST", "smtp.prueba.local")
    monkeypatch.setenv("SMTP_REMITENTE", "no-responder@prueba.local")
    enviados: list[tuple[str, str]] = []
    monkeypatch.setattr(servicio_verificacion, "_enviar_en_segundo_plano", lambda destino, nombre, codigo: enviados.append((destino, codigo)))
    servicio_verificacion._ultimo_envio.clear()
    return enviados


def _registrar_con_correo(cliente, email="nueva@test.com"):
    respuesta = cliente.post("/auth/registro", json={"email": email, "nombre": "Persona Nueva", "password": "secreto1"})
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    return cuerpo["usuario"], {"Authorization": f"Bearer {cuerpo['tokens']['access_token']}"}


def test_sin_correo_configurado_las_cuentas_nuevas_quedan_verificadas(contexto, monkeypatch) -> None:
    cliente, _, _ = contexto
    monkeypatch.delenv("SMTP_HOST", raising=False)

    usuario, _ = _registrar_con_correo(cliente)

    assert usuario["correo_verificado"] is True


def test_con_correo_configurado_la_cuenta_nueva_se_verifica_con_el_codigo_que_llega(contexto, correos) -> None:
    cliente, _, _ = contexto

    usuario, headers = _registrar_con_correo(cliente)

    assert usuario["correo_verificado"] is False
    assert [destino for destino, _ in correos] == ["nueva@test.com"]
    codigo = correos[0][1]
    assert len(codigo) == 6 and codigo.isdigit()
    assert cliente.post("/auth/verificar-correo", json={"codigo": "000000" if codigo != "000000" else "111111"}, headers=headers).status_code == 400
    correcto = cliente.post("/auth/verificar-correo", json={"codigo": f"{codigo[:3]} {codigo[3:]}"}, headers=headers)  # con espacio también sirve
    assert correcto.status_code == 200 and correcto.json()["correo_verificado"] is True
    assert cliente.get("/auth/me", headers=headers).json()["correo_verificado"] is True


def test_el_codigo_vence_y_los_intentos_fallidos_se_limitan(contexto, correos) -> None:
    cliente, _, fabrica = contexto
    usuario, headers = _registrar_con_correo(cliente)
    codigo = correos[0][1]
    with fabrica() as db:
        fila = db.get(UsuarioORM, usuario["id"])
        fila.codigo_verificacion_expira = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()

    assert cliente.post("/auth/verificar-correo", json={"codigo": codigo}, headers=headers).status_code == 400  # venció

    estados = [cliente.post("/auth/verificar-correo", json={"codigo": "999999"}, headers=headers).status_code for _ in range(9)]
    assert estados[-1] == 429


def test_se_puede_pedir_un_codigo_nuevo_pero_no_a_cada_instante(contexto, correos) -> None:
    cliente, _, _ = contexto
    _, headers = _registrar_con_correo(cliente)
    primero = correos[0][1]

    pronto = cliente.post("/auth/verificar-correo/reenviar", headers=headers)
    assert pronto.status_code == 429 and len(correos) == 1

    servicio_verificacion._ultimo_envio.clear()
    assert cliente.post("/auth/verificar-correo/reenviar", headers=headers).status_code == 200
    segundo = correos[1][1]
    if segundo != primero:  # el viejo deja de servir
        assert cliente.post("/auth/verificar-correo", json={"codigo": primero}, headers=headers).status_code == 400
    assert cliente.post("/auth/verificar-correo", json={"codigo": segundo}, headers=headers).status_code == 200
    assert cliente.post("/auth/verificar-correo/reenviar", headers=headers).status_code == 400  # ya está verificado


def test_el_codigo_no_se_guarda_en_claro(contexto, correos) -> None:
    cliente, _, fabrica = contexto
    usuario, _ = _registrar_con_correo(cliente)

    with fabrica() as db:
        guardado = db.scalar(select(UsuarioORM.codigo_verificacion_hash).where(UsuarioORM.id == usuario["id"]))

    assert guardado and correos[0][1] not in guardado and len(guardado) == 64
