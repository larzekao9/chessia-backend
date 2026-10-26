"""Tests para `servicio_sala.py` (Fase 1).

Cubre creación, unión, mover, abandonar, limpieza y exclusiones.
"""
from __future__ import annotations

import pytest
import threading

from backend.repositorios.repositorio_partida import RepositorioPartidasEnMemoria
from backend.servicios.sala.servicio_sala import (
    crear_sala,
    unirse,
    obtener_estado,
    mover_humano,
    abandonar,
    sala_activa_de,
    limpiar_salas_inactivas,
    _lock_para,
)


@pytest.fixture
def repo():
    """Repositorio en memoria limpio para cada test."""
    return RepositorioPartidasEnMemoria()


@pytest.fixture(autouse=True)
def _usar_repo(monkeypatch, repo):
    """Hace que el módulo use el repo de test."""
    import backend.servicios.sala.servicio_sala as svc
    monkeypatch.setattr(svc, "_repositorio", repo)
    # Limpiar locks
    svc._locks_partida.clear()


def test_crear_sala_codigo_unico_y_valido():
    """Crear sala genera código de 6 chars en alfabeto sin ambiguos."""
    p1 = crear_sala(1, "blancas")
    assert p1.tipo_oponente == "humano"
    assert p1.estado == "esperando"
    assert p1.codigo_sala is not None
    assert len(p1.codigo_sala) == 6
    assert all(c in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" for c in p1.codigo_sala)
    assert p1.usuario_blancas_id == 1
    assert p1.usuario_negras_id is None
    assert p1.nivel == 0
    assert p1.control_tiempo_ms == 0


def test_crear_sala_color_aleatorio_asigna_uno():
    """Color 'aleatorio' asigna blancas o negras."""
    for _ in range(10):
        p = crear_sala(1, "aleatorio")
        assert p.usuario_blancas_id == 1 or p.usuario_negras_id == 1


def test_unirse_segundo_jugador_pasa_a_en_curso():
    """Unirse ocupa asiento libre y pasa la sala a 'en_curso'."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)

    assert p2.estado == "en_curso"
    assert p2.usuario_blancas_id == 1
    assert p2.usuario_negras_id == 2
    assert p2.iniciada_en is not None


def test_unirse_idempotente_si_ya_esta_sentado():
    """Unirse dos veces no cambia nada."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)
    p3 = unirse(p1.codigo_sala, 2)

    assert p3.id == p2.id
    assert p3.usuario_negras_id == 2


def test_unirse_tercer_usuario_es_espectador():
    """Tercer usuario recibe rol espectador (no error)."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)  # Sala pasa a en_curso
    p3 = unirse(p1.codigo_sala, 3)  # Sala llena

    # Devuelve la sala sin error
    assert p3.id == p1.id
    # El tercer usuario no ocupa asiento
    assert p3.usuario_blancas_id == 1
    assert p3.usuario_negras_id == 2


def test_unirse_codigo_inexistente_lanza_keyerror():
    """Unirse a código inexistente lanza KeyError."""
    with pytest.raises(KeyError):
        unirse("NOEXISTE", 1)


def test_mover_solo_jugador_del_turno():
    """Solo el jugador al que le toca puede mover."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)

    # Blancas mueven primero
    p3 = mover_humano(p1.codigo_sala, 1, "e2e4")
    assert p3.tablero.turn == 0  # turno de negras

    # Negras mueven
    p4 = mover_humano(p1.codigo_sala, 2, "e7e5")
    assert p4.tablero.turn == 1  # turno de blancas


def test_mover_fuera_de_turno_lanza_permissionerror():
    """Mover fuera de turno lanza PermissionError (403)."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)

    # Intenta mover negras cuando le toca a blancas
    with pytest.raises(PermissionError, match="No es tu turno"):
        mover_humano(p1.codigo_sala, 2, "e7e5")


def test_mover_espectador_lanza_permissionerror():
    """Espectador no puede mover."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)
    p3 = unirse(p1.codigo_sala, 3)  # espectador

    with pytest.raises(PermissionError):
        mover_humano(p1.codigo_sala, 3, "e2e4")


def test_mover_en_espera_lanza_valueerror():
    """Mover en estado 'esperando' lanza error."""
    p1 = crear_sala(1, "blancas")
    with pytest.raises(ValueError, match="esperando"):
        mover_humano(p1.codigo_sala, 1, "e2e4")


def test_abandonar_gana_el_otro():
    """Abandonar marca terminada y gana el otro."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)

    # Blancas abandonan → negras ganan (0-1)
    p3 = abandonar(p1.codigo_sala, 1)
    assert p3.estado == "terminada"
    assert p3.resultado == "0-1"

    # Blancas abandonan (otro test) → negras ganan (0-1)
    p4 = crear_sala(4, "negras")  # user 4 = negras
    p5 = unirse(p4.codigo_sala, 5)  # user 5 = blancas
    p6 = abandonar(p4.codigo_sala, 5)  # user 5 (blancas) abandona
    assert p6.resultado == "0-1"  # negras (user 4) ganan

    # Negras abandonan → blancas ganan (1-0)
    p7 = crear_sala(7, "blancas")  # user 7 = blancas
    p8 = unirse(p7.codigo_sala, 8)  # user 8 = negras
    p9 = abandonar(p7.codigo_sala, 8)  # user 8 (negras) abandona
    assert p9.resultado == "1-0"  # blancas (user 7) ganan


def test_abandonar_espectador_lanza_permissionerror():
    """Espectador no puede abandonar."""
    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)
    p3 = unirse(p1.codigo_sala, 3)

    with pytest.raises(PermissionError):
        abandonar(p1.codigo_sala, 3)


def test_sala_activa_de_devuelve_sala_esperando_o_en_curso():
    """sala_activa_de devuelve la sala más reciente del usuario."""
    p1 = crear_sala(1, "blancas")
    p2 = crear_sala(1, "negras")
    p3 = unirse(p2.codigo_sala, 2)  # en_curso

    activa = sala_activa_de(1)
    assert activa is not None
    assert activa.id == p3.id  # La más reciente (en_curso)

    # Usuario sin salas
    assert sala_activa_de(999) is None


def test_limpiar_salas_inactivas_espera_30_min(repo):
    """Sala 'esperando' > 30 min sin segundo jugador se elimina."""
    from datetime import datetime, timedelta, timezone

    p1 = crear_sala(1, "blancas")
    # Simular antigüedad > 30 min
    p1.creada_en = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    repo.guardar(p1)

    limpiar_salas_inactivas(repo)
    with pytest.raises(KeyError):
        repo.obtener(p1.id)


def test_limpiar_salas_inactivas_en_curso_sin_jugadas_60_min(repo):
    """Sala 'en_curso' sin jugadas > 60 min se elimina."""
    from datetime import datetime, timedelta, timezone

    p1 = crear_sala(1, "blancas")
    p2 = unirse(p1.codigo_sala, 2)
    # Simular antigüedad > 60 min sin jugadas
    p2.creada_en = (datetime.now(timezone.utc) - timedelta(minutes=65)).isoformat()
    p2.actualizada_en = p2.creada_en
    repo.guardar(p2)

    limpiar_salas_inactivas(repo)
    with pytest.raises(KeyError):
        repo.obtener(p2.id)


def test_lock_para_evita_carrera_unirse(repo):
    """Lock por partida evita que dos usuarios ocupen el mismo asiento."""
    p1 = crear_sala(1, "blancas")  # user 1 = blancas
    resultados = []

    def unir_usuario(uid):
        try:
            partida = unirse(p1.codigo_sala, uid)
            # Verificar si ocupó asiento o es espectador
            if partida.usuario_blancas_id == uid or partida.usuario_negras_id == uid:
                resultados.append(("asiento", uid))
            else:
                resultados.append(("espectador", uid))
        except Exception as e:
            resultados.append(("error", uid, str(e)))

    t1 = threading.Thread(target=unir_usuario, args=(2,))
    t2 = threading.Thread(target=unir_usuario, args=(3,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Solo uno debería haber ocupado el asiento (el otro es espectador)
    asientos = [r for r in resultados if r[0] == "asiento"]
    espectadores = [r for r in resultados if r[0] == "espectador"]
    assert len(asientos) == 1
    assert len(espectadores) == 1


def test_crear_sala_agota_intentos_lanza_runtimeerror(monkeypatch):
    """Si se agotan 10 intentos de código único, lanza RuntimeError."""
    # Mockear _generar_codigo para que siempre devuelva lo mismo
    import backend.servicios.sala.servicio_sala as svc
    monkeypatch.setattr(svc, "_generar_codigo", lambda: "AAAAAA")

    # Primera vez crea la sala
    crear_sala(1, "blancas")
    # Segunda vez falla tras 10 intentos
    with pytest.raises(RuntimeError, match="No se pudo generar"):
        crear_sala(2, "blancas")

def test_crear_sala_cancela_las_esperando_anteriores_del_mismo_creador(repo):
    """Al crear una sala nueva se eliminan las `esperando` previas de ese creador (no las de otros ni las en curso)."""
    vieja = crear_sala(1, "blancas")
    ajena = crear_sala(2, "blancas")
    en_curso = crear_sala(1, "negras")
    unirse(en_curso.codigo_sala, 3)
    # la sala en curso se creó después de `vieja`, que ya se canceló al crearla
    with pytest.raises(KeyError):
        repo.obtener(vieja.id)
    nueva = crear_sala(1, "blancas")
    assert repo.obtener(ajena.id).estado == "esperando"
    assert repo.obtener(en_curso.id).estado == "en_curso"
    assert repo.obtener(nueva.id).estado == "esperando"


def test_cancelar_sala_solo_creador_y_solo_esperando(repo):
    from backend.servicios.sala.servicio_sala import cancelar_sala

    sala = crear_sala(1, "blancas")
    with pytest.raises(PermissionError):
        cancelar_sala(sala.codigo_sala, 2)
    cancelar_sala(sala.codigo_sala, 1)
    with pytest.raises(KeyError):
        repo.obtener(sala.id)
    with pytest.raises(KeyError):
        cancelar_sala(sala.codigo_sala, 1)

    otra = crear_sala(1, "blancas")
    unirse(otra.codigo_sala, 2)
    with pytest.raises(ValueError):
        cancelar_sala(otra.codigo_sala, 1)
    assert repo.obtener(otra.id).estado == "en_curso"


def test_abandonar_sala_esperando_lanza_valueerror_y_no_la_modifica(repo):
    sala = crear_sala(1, "blancas")
    with pytest.raises(ValueError):
        abandonar(sala.codigo_sala, 1)
    guardada = repo.obtener(sala.id)
    assert guardada.estado == "esperando" and guardada.resultado is None


def test_sala_sin_reloj_vencida_a_los_2_dias_pierde_quien_tenia_el_turno(repo):
    from datetime import datetime, timedelta, timezone

    from backend.servicios.sala.analisis_partida_sala import motivo_fin

    sala = crear_sala(1, "blancas")
    sala = unirse(sala.codigo_sala, 2)
    # Tras estas 5 jugadas le toca mover a negras (usuario 2): si no responde, pierde.
    for i, jugada in enumerate(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"]):
        mover_humano(sala.codigo_sala, 1 if i % 2 == 0 else 2, jugada)
    sala = repo.obtener(sala.id)
    sala.actualizada_en = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    repo.guardar(sala)

    limpiar_salas_inactivas(repo)
    vencida = repo.obtener(sala.id)
    assert vencida.estado == "abandonada"
    assert vencida.resultado == "1-0"  # ganan blancas: negras no respondió
    assert motivo_fin(vencida) == "sin_respuesta"


def test_sala_sin_reloj_con_menos_de_13_horas_sigue_en_curso(repo):
    """Todavía no pasaron los 2 días: no hay que esperar hasta el último minuto para confirmar que no se cierra antes de tiempo."""
    from datetime import datetime, timedelta, timezone

    sala = crear_sala(1, "blancas")
    sala = unirse(sala.codigo_sala, 2)
    mover_humano(sala.codigo_sala, 1, "e2e4")
    sala = repo.obtener(sala.id)
    sala.actualizada_en = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    repo.guardar(sala)

    limpiar_salas_inactivas(repo)
    assert repo.obtener(sala.id).estado == "en_curso"


def test_motivo_fin_distingue_mate_tablas_y_abandono(repo):
    from backend.servicios.sala.analisis_partida_sala import motivo_fin

    sala = crear_sala(1, "blancas")
    assert motivo_fin(sala) is None
    unirse(sala.codigo_sala, 2)
    for i, jugada in enumerate(["f2f3", "e7e5", "g2g4", "d8h4"]):
        sala = mover_humano(sala.codigo_sala, 1 if i % 2 == 0 else 2, jugada)
    assert motivo_fin(sala) == "mate"

    otra = crear_sala(3, "blancas")
    unirse(otra.codigo_sala, 4)
    assert motivo_fin(abandonar(otra.codigo_sala, 3)) == "abandono"
