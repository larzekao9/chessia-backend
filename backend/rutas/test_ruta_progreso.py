"""Progreso de los alumnos: antes y después, informe por alumno, resumen del curso y de la institución, salud (SQLite en memoria)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.modelos.tablas_orm import CalibracionORM, PartidaORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.rutas.test_ruta_instituciones import _crear_institucion, perfiles  # noqa: F401  (fixture y ayuda compartidas)
from backend.servicios.curso import servicio_progreso as progreso

AHORA = datetime(2026, 10, 25, 12, 0, 0)
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _calibraciones(precisiones: list[float]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(precision_global=p, nivel_partida=int(p // 5), creado_en=AHORA - timedelta(days=len(precisiones) - i))
        for i, p in enumerate(precisiones)
    ]


# --- la cuenta del antes y el después -------------------------------------------------------------------------------------------


def test_sin_partidas_calibradas_no_hay_nada_que_comparar() -> None:
    resultado = progreso.comparar([])

    assert (resultado["calibradas"], resultado["comparable"], resultado["tendencia"]) == (0, False, "sin_comparacion")
    assert resultado["precision_antes"] is None and resultado["serie"] == []


def test_con_el_diagnostico_incompleto_el_antes_es_provisional_y_no_hay_comparacion() -> None:
    resultado = progreso.comparar(_calibraciones([40, 50]))

    assert (resultado["diagnostico_completo"], resultado["comparable"]) == (False, False)
    assert resultado["precision_antes"] == 45.0 and resultado["precision_despues"] is None


def test_completado_el_diagnostico_hace_falta_una_partida_mas_para_comparar() -> None:
    sin_comparar = progreso.comparar(_calibraciones([40, 42, 44]))
    comparada = progreso.comparar(_calibraciones([40, 42, 44, 60]))

    assert (sin_comparar["diagnostico_completo"], sin_comparar["comparable"]) == (True, False)
    assert (comparada["comparable"], comparada["confirmado"]) == (True, False)  # una sola partida posterior: provisional
    assert (comparada["precision_antes"], comparada["precision_despues"], comparada["variacion_precision"]) == (42.0, 60.0, 18.0)


def test_el_despues_son_las_ultimas_tres_partidas_posteriores_al_diagnostico() -> None:
    resultado = progreso.comparar(_calibraciones([40, 42, 44, 10, 62, 64, 66]))

    assert resultado["confirmado"] is True
    assert resultado["precision_despues"] == 64.0  # 62, 64 y 66: la partida floja del medio ya no entra
    assert resultado["tendencia"] == "mejora" and resultado["variacion_nivel"] > 0
    assert [p["numero"] for p in resultado["serie"]] == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    "despues,tendencia",
    [([50, 50, 50], "estable"), ([51, 51, 51], "estable"), ([52, 52, 52], "mejora"), ([49, 49, 49], "estable"), ([48, 48, 48], "baja")],
)
def test_un_cambio_menor_a_dos_puntos_es_ruido_no_mejora(despues, tendencia) -> None:
    assert progreso.comparar(_calibraciones([50, 50, 50, *despues]))["tendencia"] == tendencia


def test_el_riesgo_de_una_institucion() -> None:
    assert progreso._riesgo(0, 0, None, 3) is None  # recién creada
    assert "ningún alumno" in progreso._riesgo(0, 0, None, 10)
    assert "Nadie jugó" in progreso._riesgo(20, 0, 30, 60)
    assert "Nadie jugó" in progreso._riesgo(20, 0, None, 60)
    assert "cuarto" in progreso._riesgo(20, 3, 2, 60)
    assert progreso._riesgo(20, 12, 2, 60) is None


# --- el informe, el curso y la institución por la API --------------------------------------------------------------------------


@pytest.fixture
def escenario(perfiles, monkeypatch):
    """Una institución con su director y su docente; un curso con dos alumnos, uno con progreso y otro recién llegado."""
    cliente, personas, fabrica = perfiles
    monkeypatch.setattr(progreso, "_ahora", lambda: AHORA)
    monkeypatch.setenv("CORREOS_FACILITADORES", "*@test.com")  # para registrar a un docente de otra institución
    ajeno = cliente.post("/auth/registro", json={"email": "otro@test.com", "nombre": "Otro Docente", "password": "secreto1", "rol": "facilitador"}).json()
    personas["ajeno"] = {"id": ajeno["usuario"]["id"], "headers": {"Authorization": f"Bearer {ajeno['tokens']['access_token']}"}}
    monkeypatch.setenv("CORREOS_FACILITADORES", "plataforma@test.com")
    plataforma = personas["plataforma"]["headers"]
    institucion = _crear_institucion(cliente, personas, administrador_id=personas["director"]["id"])
    cliente.put(f"/instituciones/{institucion['id']}/docentes/{personas['docente']['id']}", headers=plataforma)
    curso = cliente.post("/cursos", json={"nombre": "Ajedrez 1ro A"}, headers=personas["docente"]["headers"]).json()
    nuevo = cliente.post("/auth/registro", json={"email": "nuevo@test.com", "nombre": "Alumno Nuevo", "password": "secreto1"}).json()
    nuevo = {"id": nuevo["usuario"]["id"], "headers": {"Authorization": f"Bearer {nuevo['tokens']['access_token']}"}}
    for alumno in (personas["alumno"], nuevo):
        assert cliente.post("/cursos/unirse", json={"codigo": curso["codigo"]}, headers=alumno["headers"]).status_code in (200, 201)

    with fabrica() as db:
        for i, precision in enumerate([40, 42, 44, 60, 62, 64]):
            db.add(CalibracionORM(usuario_id=personas["alumno"]["id"], partida_id=f"a{i}", precision_global=precision, nivel_partida=int(precision // 5), rango_partida="Intermedio", total_jugadas=20, creado_en=AHORA - timedelta(days=60 - i * 10)))
        for i, precision in enumerate([50, 52]):
            db.add(CalibracionORM(usuario_id=nuevo["id"], partida_id=f"n{i}", precision_global=precision, nivel_partida=10, rango_partida="Intermedio", total_jugadas=20, creado_en=AHORA - timedelta(days=3 - i)))
        db.add(PartidaORM(id="p1", usuario_id=personas["alumno"]["id"], tipo="digital", fen=FEN, nivel=5, jugadas_uci="e2e4", iniciada_en=AHORA - timedelta(days=2)))
        db.add(PartidaORM(id="p2", usuario_id=personas["alumno"]["id"], tipo="digital", fen=FEN, nivel=5, jugadas_uci="d2d4", iniciada_en=AHORA - timedelta(days=20)))
        db.commit()
    return cliente, personas, nuevo, institucion["id"], curso["id"]


def test_el_jugador_ve_su_propio_informe_con_el_antes_y_el_despues(escenario) -> None:
    cliente, personas, _, _, _ = escenario

    informe = cliente.get("/progreso/mio", headers=personas["alumno"]["headers"])

    assert informe.status_code == 200
    cuerpo = informe.json()
    assert cuerpo["alumno"]["curso"] == "Ajedrez 1ro A" and cuerpo["alumno"]["docente"]
    assert (cuerpo["progreso"]["precision_antes"], cuerpo["progreso"]["precision_despues"], cuerpo["progreso"]["tendencia"]) == (42.0, 62.0, "mejora")
    assert cuerpo["progreso"]["confirmado"] is True and len(cuerpo["progreso"]["serie"]) == 6
    assert (cuerpo["actividad"]["partidas"], cuerpo["actividad"]["partidas_7_dias"], cuerpo["actividad"]["partidas_30_dias"], cuerpo["actividad"]["dias_sin_jugar"]) == (2, 1, 2, 2)
    assert any("mejoró 20 puntos" in frase for frase in cuerpo["lectura"])
    assert cliente.get("/progreso/mio").status_code == 401


def test_un_alumno_recien_llegado_se_cuenta_como_en_diagnostico(escenario) -> None:
    cliente, _, nuevo, _, _ = escenario

    cuerpo = cliente.get("/progreso/mio", headers=nuevo["headers"]).json()

    assert (cuerpo["progreso"]["comparable"], cuerpo["progreso"]["tendencia"]) == (False, "sin_comparacion")
    assert "todavía está en su diagnóstico" in cuerpo["lectura"][0] and "le falta 1" in cuerpo["lectura"][0]


def test_cada_docente_ve_el_informe_de_sus_alumnos_y_de_nadie_mas(escenario) -> None:
    cliente, personas, nuevo, _, _ = escenario
    alumno_id = personas["alumno"]["id"]

    assert cliente.get(f"/progreso/alumnos/{alumno_id}", headers=personas["docente"]["headers"]).status_code == 200
    assert cliente.get(f"/progreso/alumnos/{alumno_id}", headers=personas["director"]["headers"]).status_code == 200  # administra su institución
    assert cliente.get(f"/progreso/alumnos/{alumno_id}", headers=personas["plataforma"]["headers"]).status_code == 200
    assert cliente.get(f"/progreso/alumnos/{alumno_id}", headers=personas["ajeno"]["headers"]).status_code == 404
    assert cliente.get(f"/progreso/alumnos/{alumno_id}", headers=personas["alumno"]["headers"]).status_code == 403  # un jugador no pide el de otro
    assert cliente.get(f"/progreso/alumnos/{personas['docente']['id']}", headers=personas["plataforma"]["headers"]).status_code == 404  # no es un alumno


def test_el_curso_cuenta_cuantos_mejoraron_y_a_cuantos_les_falta_el_diagnostico(escenario) -> None:
    cliente, personas, _, _, curso_id = escenario

    respuesta = cliente.get(f"/progreso/cursos/{curso_id}", headers=personas["docente"]["headers"])

    assert respuesta.status_code == 200
    resumen = respuesta.json()["resumen"]
    assert (resumen["alumnos"], resumen["comparables"], resumen["mejoraron"], resumen["porcentaje_mejoraron"], resumen["sin_diagnostico"]) == (2, 1, 1, 100, 1)
    assert (resumen["precision_antes"], resumen["precision_despues"], resumen["mejora_promedio_precision"]) == (42.0, 62.0, 20.0)
    assert (resumen["activos_7_dias"], resumen["activos_30_dias"], resumen["partidas"]) == (1, 1, 2)
    assert respuesta.json()["alumnos"][0]["variacion_precision"] == 20.0  # el que mejoró va primero


def test_el_curso_de_otro_docente_no_se_ve_pero_el_director_y_la_plataforma_si(escenario) -> None:
    cliente, personas, _, _, curso_id = escenario

    assert cliente.get(f"/progreso/cursos/{curso_id}", headers=personas["ajeno"]["headers"]).status_code == 404
    assert cliente.get(f"/progreso/cursos/{curso_id}", headers=personas["director"]["headers"]).status_code == 200
    assert cliente.get(f"/progreso/cursos/{curso_id}", headers=personas["plataforma"]["headers"]).status_code == 200
    assert cliente.get("/progreso/cursos/9999", headers=personas["plataforma"]["headers"]).status_code == 404


def test_el_director_ve_el_progreso_de_su_institucion_y_el_docente_no(escenario) -> None:
    cliente, personas, _, institucion_id, _ = escenario

    director = cliente.get("/progreso/institucion", headers=personas["director"]["headers"])
    plataforma = cliente.get(f"/progreso/institucion?institucion_id={institucion_id}", headers=personas["plataforma"]["headers"])

    assert director.status_code == 200 and director.json()["resumen"]["alumnos"] == 2 and director.json()["resumen"]["mejoraron"] == 1
    assert plataforma.status_code == 200 and plataforma.json()["nombre"] == director.json()["nombre"]
    assert cliente.get("/progreso/institucion", headers=personas["docente"]["headers"]).status_code == 404
    assert cliente.get("/progreso/institucion", headers=personas["alumno"]["headers"]).status_code == 403


def test_la_salud_de_las_instituciones_es_solo_de_la_plataforma_y_avisa_a_las_que_no_juegan(escenario, monkeypatch) -> None:
    cliente, personas, _, institucion_id, _ = escenario

    assert cliente.get("/progreso/instituciones", headers=personas["director"]["headers"]).status_code == 403
    filas = cliente.get("/progreso/instituciones", headers=personas["plataforma"]["headers"]).json()
    fila = next(f for f in filas if f["institucion_id"] == institucion_id)
    assert (fila["alumnos"], fila["activos_7_dias"], fila["en_riesgo"], fila["dias_sin_actividad"]) == (2, 1, False, 2)

    monkeypatch.setattr(progreso, "_ahora", lambda: AHORA + timedelta(days=30))  # un mes después nadie volvió a jugar
    fila = next(f for f in cliente.get("/progreso/instituciones", headers=personas["plataforma"]["headers"]).json() if f["institucion_id"] == institucion_id)
    assert fila["en_riesgo"] is True and "Nadie jugó" in fila["motivo_riesgo"]
