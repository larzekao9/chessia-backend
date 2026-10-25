"""Medallas (figuritas): ganarlas, avisarlas, que no se pierdan, imágenes y ranking (SQLite en memoria)."""
import uuid
from datetime import datetime, timedelta, timezone

import chess
import pytest

from backend.modelos.tablas_orm import CalibracionORM, MedallaGanadaORM, MensajeTutorORM, PartidaORM
from backend.rutas.test_ruta_cursos import _crear, _solo_principal, _unirse
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.rutas.test_ruta_ranking import _calibrar, _ranking
from backend.servicios.medallas import servicio_medallas
from backend.servicios.medallas.catalogo import CATALOGO, Medalla

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JUGADAS_CORTAS = "e2e4 e7e5 d2d4 d7d5"  # 2 jugadas propias: ya cuenta como partida jugada
MATE_DEL_PASTOR = "e2e4 e7e5 d1h5 b8c6 f1c4 g8f6 h5f7"


@pytest.fixture(autouse=True)
def _imagenes_en_carpeta_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(servicio_medallas, "MEDIA_MEDALLAS_DIR", tmp_path)


def _jugar(fabrica, usuario_id: int, resultado: str | None = "1-0", jugadas: str = JUGADAS_CORTAS, dia: datetime | None = None,
           tipo: str = "motor", nivel: int = 5) -> str:
    partida_id = uuid.uuid4().hex
    with fabrica() as sesion:
        sesion.add(
            PartidaORM(
                id=partida_id, usuario_id=usuario_id, tipo="digital", fen=chess.STARTING_FEN, nivel=nivel, tipo_oponente=tipo,
                jugadas_uci=jugadas, resultado=resultado, estado="terminada" if resultado else "en_curso",
                iniciada_en=dia or datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        sesion.commit()
    return partida_id


def _album(cliente, jugador) -> dict:
    respuesta = cliente.get("/usuario/medallas", headers=jugador["headers"])
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _ganadas(cliente, jugador) -> set[str]:
    return {m["codigo"] for m in _album(cliente, jugador)["medallas"] if m["ganada"]}


def test_sin_partidas_el_album_esta_completo_y_sin_ganadas(contexto) -> None:
    cliente, registrar, _ = contexto
    jugador = registrar("j@test.com")

    album = _album(cliente, jugador)

    assert album["total"] == len(CATALOGO) and album["ganadas"] == 0 and album["nuevas"] == [] and album["es_primera"] is False
    assert all(m["progreso"] == 0 and m["imagen_url"] is None for m in album["medallas"])
    assert cliente.get("/usuario/medallas").status_code == 401


def test_la_primera_medalla_es_celebracion_grande_y_las_siguientes_aviso_normal(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    _jugar(fabrica, jugador["id"], resultado="0-1")  # perdió: solo "Primer Peón"

    album = _album(cliente, jugador)
    assert album["nuevas"] == ["primer_peon"] and album["es_primera"] is True
    assert _album(cliente, jugador)["nuevas"] == ["primer_peon"]  # sigue pendiente de avisar hasta que se marque

    assert cliente.post("/usuario/medallas/vistas", json={}, headers=jugador["headers"]).json() == {"marcadas": 1}
    assert _album(cliente, jugador)["nuevas"] == []

    _jugar(fabrica, jugador["id"], resultado="1-0")  # ganó: "Salto de Caballo"
    siguiente = _album(cliente, jugador)
    assert siguiente["nuevas"] == ["salto_de_caballo"] and siguiente["es_primera"] is False
    assert siguiente["ganadas"] == 2


def test_una_partida_sin_resultado_o_abandonada_al_instante_no_cuenta(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    _jugar(fabrica, jugador["id"], resultado=None)  # en curso
    _jugar(fabrica, jugador["id"], resultado="0-1", jugadas="e2e4")  # 1 sola jugada propia

    assert _ganadas(cliente, jugador) == set()


def test_ganar_contra_turing_y_contra_stockfish_fuerte(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador, otro = registrar("j@test.com"), registrar("o@test.com")
    _jugar(fabrica, jugador["id"], tipo="modelo")
    _jugar(fabrica, jugador["id"], tipo="motor", nivel=8)
    _jugar(fabrica, otro["id"], tipo="motor", nivel=5)  # nivel bajo: no es "Rompe Motores"
    _jugar(fabrica, otro["id"], tipo="modelo", resultado="0-1")  # perdió: no es "Domador"

    assert {"domador_de_turing", "rompe_motores"} <= _ganadas(cliente, jugador)
    assert _ganadas(cliente, otro).isdisjoint({"domador_de_turing", "rompe_motores"})


def test_la_racha_de_victorias_se_corta_con_una_derrota(contexto) -> None:
    cliente, registrar, fabrica = contexto
    seguido, cortado = registrar("a@test.com"), registrar("b@test.com")
    base = datetime(2026, 10, 1, 10)
    for n, resultado in enumerate(("1-0", "1-0", "1-0")):
        _jugar(fabrica, seguido["id"], resultado=resultado, dia=base + timedelta(hours=n))
    for n, resultado in enumerate(("1-0", "1-0", "0-1", "1-0")):
        _jugar(fabrica, cortado["id"], resultado=resultado, dia=base + timedelta(hours=n))

    assert "rey_de_la_racha" in _ganadas(cliente, seguido)
    assert "rey_de_la_racha" not in _ganadas(cliente, cortado)


def test_cinco_partidas_en_un_dia_y_tres_dias_en_una_semana(contexto) -> None:
    cliente, registrar, fabrica = contexto
    intenso, constante = registrar("a@test.com"), registrar("b@test.com")
    lunes = datetime(2026, 10, 5, 9)
    for n in range(5):
        _jugar(fabrica, intenso["id"], dia=lunes + timedelta(minutes=n))
    for n in range(3):
        _jugar(fabrica, constante["id"], dia=lunes + timedelta(days=n))

    assert "torre_incansable" in _ganadas(cliente, intenso) and "constancia_de_roque" not in _ganadas(cliente, intenso)
    assert "constancia_de_roque" in _ganadas(cliente, constante) and "torre_incansable" not in _ganadas(cliente, constante)


def test_coronar_cuenta_solo_si_lo_hizo_el_propio_jugador(contexto) -> None:
    cliente, registrar, fabrica = contexto
    coronador, rival = registrar("a@test.com"), registrar("b@test.com")
    _jugar(fabrica, coronador["id"], resultado="0-1", jugadas="e2e4 e7e5 a7a8q d7d5")  # la jugada 3 es de las blancas (propia)
    _jugar(fabrica, rival["id"], resultado="0-1", jugadas="e2e4 a2a1q e7e5 d7d5")  # la coronación es del rival

    assert "peon_coronado" in _ganadas(cliente, coronador)
    assert "peon_coronado" not in _ganadas(cliente, rival)


def test_mate_relampago_exige_ganar_por_jaque_mate_en_20_jugadas_o_menos(contexto) -> None:
    cliente, registrar, fabrica = contexto
    rapido, sin_mate = registrar("a@test.com"), registrar("b@test.com")
    _jugar(fabrica, rapido["id"], jugadas=MATE_DEL_PASTOR)
    _jugar(fabrica, sin_mate["id"], jugadas=JUGADAS_CORTAS)  # ganó pero la partida no terminó en mate

    assert "mate_relampago" in _ganadas(cliente, rapido)
    assert "mate_relampago" not in _ganadas(cliente, sin_mate)


def test_diagnostico_precision_y_preguntas_al_tutor(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    with fabrica() as sesion:
        for _ in range(3):
            sesion.add(CalibracionORM(usuario_id=jugador["id"], partida_id=uuid.uuid4().hex, precision_global=85.0, nivel_partida=9,
                                      rango_partida="Intermedio", total_jugadas=20))
        for _ in range(10):
            sesion.add(MensajeTutorORM(usuario_id=jugador["id"], rol="user", contenido="¿cómo se mueve el caballo?"))
        sesion.add(MensajeTutorORM(usuario_id=jugador["id"], rol="assistant", contenido="así"))
        sesion.commit()

    assert {"mente_revelada", "ojo_de_alfil", "aprendiz_de_turing"} <= _ganadas(cliente, jugador)


def test_una_medalla_ganada_no_se_pierde_aunque_se_borre_la_partida(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    partida_id = _jugar(fabrica, jugador["id"])
    assert "primer_peon" in _ganadas(cliente, jugador)

    with fabrica() as sesion:
        sesion.delete(sesion.get(PartidaORM, partida_id))
        sesion.commit()

    album = _album(cliente, jugador)
    assert "primer_peon" in {m["codigo"] for m in album["medallas"] if m["ganada"]}
    primera = next(m for m in album["medallas"] if m["codigo"] == "primer_peon")
    assert primera["ganada_en"] is not None and primera["progreso"] == primera["meta"]


def test_una_medalla_nueva_del_catalogo_se_otorga_con_retroactividad(contexto, monkeypatch) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    for _ in range(3):
        _jugar(fabrica, jugador["id"])
    assert "tres_en_el_historial" not in _ganadas(cliente, jugador)

    nueva = Medalla("tres_en_el_historial", "Tres en el Historial", "Jugá 3 partidas.", "plata", "alfil", 3, lambda d: d["terminadas"])
    monkeypatch.setattr(servicio_medallas, "catalogo_completo", lambda db: CATALOGO + (nueva,))

    album = _album(cliente, jugador)
    assert "tres_en_el_historial" in {m["codigo"] for m in album["medallas"] if m["ganada"]}
    assert album["total"] == len(CATALOGO) + 1 and "tres_en_el_historial" in album["nuevas"]


def test_solo_el_principal_sube_la_imagen_de_una_medalla_y_se_valida_el_contenido(contexto, monkeypatch) -> None:
    cliente, registrar, _ = contexto
    principal, comun, jugador = registrar("admin@test.com", "facilitador"), registrar("comun@test.com", "facilitador"), registrar("j@test.com")
    _solo_principal(monkeypatch, "admin@test.com")
    ruta = "/facilitador/medallas/primer_peon/imagen"

    def subir(quien, nombre="figurita.png", contenido=PNG, codigo="primer_peon"):
        return cliente.post(f"/facilitador/medallas/{codigo}/imagen", files={"archivo": (nombre, contenido, "image/png")}, headers=quien["headers"])

    assert subir(comun).status_code == 403 and subir(jugador).status_code == 403
    assert subir(principal, nombre="figurita.gif").status_code == 400  # extensión no permitida
    assert subir(principal, contenido=b"esto no es una imagen").status_code == 400  # el contenido no coincide
    assert subir(principal, contenido=PNG + b"0" * (2 * 1024 * 1024)).status_code == 413  # más de 2 MB (se corta al leer)
    assert subir(principal, codigo="medalla_que_no_existe").status_code == 400

    subida = subir(principal)
    assert subida.status_code == 200 and subida.json()["imagen_url"].startswith("/media/medallas/primer_peon.png?v=")
    primera = next(m for m in _album(cliente, jugador)["medallas"] if m["codigo"] == "primer_peon")
    assert primera["imagen_url"] == subida.json()["imagen_url"]  # todos los jugadores ven la nueva figurita

    assert cliente.delete(ruta, headers=comun["headers"]).status_code == 403
    assert cliente.delete(ruta, headers=principal["headers"]).status_code == 204
    assert cliente.delete(ruta, headers=principal["headers"]).status_code == 404
    assert next(m for m in _album(cliente, jugador)["medallas"] if m["codigo"] == "primer_peon")["imagen_url"] is None


def test_el_ranking_muestra_las_medallas_de_cada_jugador(contexto) -> None:
    cliente, registrar, fabrica = contexto
    profe, ana, beto = registrar("prof@test.com", "facilitador"), registrar("ana@test.com"), registrar("beto@test.com")
    _calibrar(fabrica, ana["id"], 12, [79], nombre="Ana Pérez")
    _calibrar(fabrica, beto["id"], 7, [50], nombre="Beto Gómez")
    curso = _crear(cliente, profe)
    for jugador in (ana, beto):
        _unirse(cliente, jugador, curso["codigo"])
    _jugar(fabrica, ana["id"], tipo="modelo")  # Ana: Primer Peón, Salto de Caballo, Domador de Turing

    filas = {f["nombre"]: f for f in _ranking(cliente, beto).json()["jugadores"]}

    assert filas["Ana P."]["medallas"]["total"] == 3
    assert [m["codigo"] for m in filas["Ana P."]["medallas"]["destacadas"]][0] == "domador_de_turing"  # la de mayor nivel primero
    assert filas["Beto G."]["medallas"] == {"total": 0, "destacadas": []}


def test_las_medallas_se_guardan_una_sola_vez_por_jugador(contexto) -> None:
    cliente, registrar, fabrica = contexto
    jugador = registrar("j@test.com")
    _jugar(fabrica, jugador["id"])

    for _ in range(3):
        _album(cliente, jugador)

    with fabrica() as sesion:
        codigos = [m.codigo for m in sesion.query(MedallaGanadaORM).filter_by(usuario_id=jugador["id"]).all()]
    assert len(codigos) == len(set(codigos))
