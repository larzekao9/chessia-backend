"""Elo entre jugadores: la cuenta, que se anote una sola vez por partida, cómo se muestra y los filtros de la búsqueda de rival (SQLite en memoria)."""
from __future__ import annotations

from backend.modelos.tablas_orm import EloMovimientoORM, UsuarioORM
from backend.rutas.test_ruta_gestion_usuarios import contexto  # noqa: F401  (fixture compartida)
from backend.rutas.test_ruta_social import CINCO_MIN, _nivel, social  # noqa: F401  (fixture compartida)
from backend.servicios.social import elo


def _poner_elo(fabrica, gente, nombre: str, valor: int) -> None:
    with fabrica() as db:
        db.get(UsuarioORM, gente[nombre]["id"]).elo = valor
        db.commit()


def _jugar(cliente, gente, codigo: str, jugadas: list[tuple[str, str]]) -> None:
    for quien, uci in jugadas:
        r = cliente.post(f"/sala/{codigo}/mover", json={"jugada": uci}, headers=gente[quien]["headers"])
        assert r.status_code == 200, r.text


def test_la_cuenta_del_elo_es_la_de_siempre() -> None:
    assert elo.esperado(1000, 1000) == 0.5 and round(elo.esperado(1400, 1000), 2) == 0.91
    assert elo.nuevo_elo(1000, 1000, 1.0, 0) == 1020  # K = 40 con pocas partidas: gana la mitad de 40
    assert elo.nuevo_elo(1000, 1000, 0.5, 0) == 1000
    assert elo.nuevo_elo(1000, 1000, 1.0, 50) == 1010  # K = 20 ya con experiencia
    assert elo.nuevo_elo(110, 110, 0.0, 0) == elo.ELO_MINIMO  # perdería 20 y quedaría en 90: nunca baja del piso
    assert elo.es_provisional(5) and not elo.es_provisional(elo.PARTIDAS_PROVISIONAL)


def test_una_partida_entre_dos_mueve_el_elo_una_sola_vez_y_se_muestra(social) -> None:
    cliente, gente, fabrica, _ = social
    sala = cliente.post("/sala", json={"color": "blancas"}, headers=gente["dani"]["headers"]).json()
    codigo = sala["codigo"]
    assert cliente.post(f"/sala/{codigo}/unirse", headers=gente["eli"]["headers"]).status_code == 200
    _jugar(cliente, gente, codigo, [("dani", "e2e4"), ("eli", "e7e5"), ("dani", "g1f3"), ("eli", "b8c6")])

    final = cliente.post(f"/sala/{codigo}/abandonar", headers=gente["eli"]["headers"]).json()  # eli abandona: gana dani
    assert final["resultado"] == "1-0"
    assert final["elo"]["blancas"] == {"antes": 1000, "despues": 1020, "cambio": 20}
    assert final["elo"]["negras"] == {"antes": 1000, "despues": 980, "cambio": -20}
    cliente.get(f"/sala/{codigo}", headers=gente["dani"]["headers"])  # la otra pantalla la consulta: no se vuelve a contar
    with fabrica() as db:
        assert db.query(EloMovimientoORM).filter_by(sala_codigo=codigo).count() == 2
        assert (db.get(UsuarioORM, gente["dani"]["id"]).elo, db.get(UsuarioORM, gente["eli"]["id"]).elo) == (1020, 980)
    mio = cliente.get("/social/mi-elo", headers=gente["dani"]["headers"]).json()
    assert (mio["elo"], mio["partidas"], mio["provisional"]) == (1020, 1, True)
    assert mio["historial"][0]["resultado"] == "ganó" and mio["historial"][0]["cambio"] == 20 and mio["historial"][0]["rival"] == "Persona P."
    assert cliente.get("/auth/me", headers=gente["dani"]["headers"]).json()["elo"] == 1020
    # y el Elo del rival se ve junto a su nivel
    assert cliente.get(f"/sala/{codigo}", headers=gente["dani"]["headers"]).json()["negras"]["elo"] == 980


def test_una_partida_demasiado_corta_no_mueve_el_elo(social) -> None:
    cliente, gente, fabrica, _ = social
    codigo = cliente.post("/sala", json={"color": "blancas"}, headers=gente["dani"]["headers"]).json()["codigo"]
    cliente.post(f"/sala/{codigo}/unirse", headers=gente["eli"]["headers"])
    _jugar(cliente, gente, codigo, [("dani", "e2e4")])
    final = cliente.post(f"/sala/{codigo}/abandonar", headers=gente["eli"]["headers"]).json()
    assert final["resultado"] == "1-0" and final["elo"] is None
    with fabrica() as db:
        assert db.get(UsuarioORM, gente["dani"]["id"]).elo == 1000 and db.query(EloMovimientoORM).count() == 0


def test_buscar_rival_con_filtro_de_elo_solo_empareja_dentro_del_rango(social) -> None:
    cliente, gente, fabrica, _ = social
    for nombre in ("dani", "eli"):
        _nivel(fabrica, gente, nombre, 6)
    _poner_elo(fabrica, gente, "dani", 1000)
    _poner_elo(fabrica, gente, "eli", 1800)
    pedido = {"control_tiempo_ms": CINCO_MIN, "elo_min": 1500, "elo_max": 2500}
    primero = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN, "elo_min": 1000, "elo_max": 1200}, headers=gente["dani"]["headers"])
    assert primero.json()["estado"] == "buscando" and primero.json()["filtros"] == {"elo_min": 1000, "elo_max": 1200} and primero.json()["tolerancia"] is None
    # eli quiere 1500-2500 y dani tiene 1000: no se aceptan entre sí aunque tengan el mismo nivel
    assert cliente.post("/social/emparejamiento", json=pedido, headers=gente["eli"]["headers"]).json()["estado"] == "buscando"
    # dani cambia su búsqueda a 1500-2500 y eli (que pide 1500-2500) tiene 1800 pero dani tiene 1000: sigue sin servir
    cliente.post("/social/emparejamiento", json=pedido, headers=gente["dani"]["headers"])
    assert _estado(cliente, gente, "dani") == "buscando"
    # si eli deja de filtrar, acepta a dani (mismo nivel) y dani acepta a eli (1800 está en 1500-2500): se emparejan
    assert cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["eli"]["headers"]).json()["estado"] == "encontrado"
    assert _estado(cliente, gente, "dani") == "encontrado"


def test_buscar_rival_con_filtro_de_nivel_y_rangos_invalidos(social) -> None:
    cliente, gente, fabrica, _ = social
    _nivel(fabrica, gente, "dani", 10)
    _nivel(fabrica, gente, "eli", 13)
    cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN, "nivel_min": 12, "nivel_max": 15}, headers=gente["dani"]["headers"])
    segundo = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN, "nivel_min": 8, "nivel_max": 11}, headers=gente["eli"]["headers"])
    assert segundo.json()["estado"] == "encontrado"  # dani (10) cae en lo que pide eli (8-11) y eli (13) en lo que pide dani (12-15): saltan los 3 niveles de diferencia
    assert cliente.post("/social/emparejamiento", json={"elo_min": 2000, "elo_max": 1000}, headers=gente["ana"]["headers"]).status_code == 422
    assert cliente.post("/social/emparejamiento", json={"elo_max": 3500}, headers=gente["ana"]["headers"]).status_code == 422


def _estado(cliente, gente, nombre: str) -> str:
    busqueda = cliente.get("/social/avisos", headers=gente[nombre]["headers"]).json()["busqueda"]
    return busqueda["estado"] if busqueda else "ninguna"


def test_una_partida_amistosa_se_juega_igual_pero_no_mueve_el_elo(social) -> None:
    cliente, gente, fabrica, _ = social
    sala = cliente.post("/sala", json={"color": "blancas", "puntuada": False}, headers=gente["dani"]["headers"]).json()
    codigo = sala["codigo"]
    assert sala["puntuada"] is False
    cliente.post(f"/sala/{codigo}/unirse", headers=gente["eli"]["headers"])
    _jugar(cliente, gente, codigo, [("dani", "e2e4"), ("eli", "e7e5"), ("dani", "g1f3"), ("eli", "b8c6")])

    final = cliente.post(f"/sala/{codigo}/abandonar", headers=gente["eli"]["headers"]).json()

    assert final["resultado"] == "1-0" and final["puntuada"] is False and final["elo"] is None
    with fabrica() as db:
        assert (db.get(UsuarioORM, gente["dani"]["id"]).elo, db.get(UsuarioORM, gente["eli"]["id"]).elo) == (1000, 1000)
        assert db.query(EloMovimientoORM).count() == 0


def test_quien_busca_una_partida_amistosa_solo_se_empareja_con_quien_tambien_la_busca_amistosa(social) -> None:
    cliente, gente, fabrica, _ = social
    for nombre in ("dani", "eli"):
        _nivel(fabrica, gente, nombre, 6)
    cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN, "puntuada": False}, headers=gente["dani"]["headers"])
    puntuada = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN}, headers=gente["eli"]["headers"])
    assert puntuada.json()["estado"] == "buscando"  # mismo nivel y mismo reloj, pero uno quiere puntuar y el otro no

    amistosa = cliente.post("/social/emparejamiento", json={"control_tiempo_ms": CINCO_MIN, "puntuada": False}, headers=gente["eli"]["headers"])

    assert amistosa.json()["estado"] == "encontrado"
    assert cliente.get(f"/sala/{amistosa.json()['codigo']}", headers=gente["eli"]["headers"]).json()["puntuada"] is False
