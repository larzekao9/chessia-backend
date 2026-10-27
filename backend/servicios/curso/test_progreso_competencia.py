"""Pruebas de las partes puras del seguimiento del grupo: el semáforo de constancia, los motivos de atención y las frases de competencia."""
from backend.servicios.curso.servicio_progreso import (
    DIAS_SIN_ACTIVIDAD,
    _frases_de_competencia,
    estado_de_actividad,
    motivos_de_atencion,
)


def _competencia(**cambios):
    base = {
        "elo": 1068,
        "partidas_elo": 6,
        "elo_provisional": True,
        "elo_cambio_30_dias": 68,
        "amistosas": {"ganadas": 0, "empatadas": 0, "perdidas": 0},
        "puntuadas": {"ganadas": 4, "empatadas": 0, "perdidas": 2},
        "torneos": {"ganadas": 0, "empatadas": 0, "perdidas": 0},
        "total": 6,
        "racha_derrotas": 0,
    }
    return {**base, **cambios}


def test_el_semaforo_de_constancia_va_de_verde_a_rojo_segun_los_dias_sin_jugar() -> None:
    assert estado_de_actividad(None) == "sin_empezar"
    assert estado_de_actividad(0) == estado_de_actividad(7) == "verde"
    assert estado_de_actividad(8) == estado_de_actividad(DIAS_SIN_ACTIVIDAD - 1) == "amarillo"
    assert estado_de_actividad(DIAS_SIN_ACTIVIDAD) == "rojo"


def test_los_motivos_de_atencion_avisan_de_lo_que_hay_que_mirar() -> None:
    assert motivos_de_atencion(True, 2, "mejora", True, 0) == []
    assert motivos_de_atencion(True, None, "sin_comparacion", False, 0) == ["Todavía no empezó a jugar"]
    assert motivos_de_atencion(True, 20, "baja", True, 3) == [
        "Hace 20 días que no juega",
        "Bajó su nivel desde el diagnóstico",
        "Perdió sus últimas 3 partidas con compañeros",
    ]
    assert motivos_de_atencion(False, 30, "baja", True, 5) == []  # una cuenta dada de baja no pide atención


def test_las_frases_cuentan_el_elo_los_resultados_y_las_amistosas() -> None:
    frases = _frases_de_competencia(_competencia(amistosas={"ganadas": 1, "empatadas": 0, "perdidas": 0}, total=7))
    assert "ganó 4, empató 0 y perdió 2" in frases[0] and "1068 (provisional" in frases[0] and "subió 68" in frases[0]
    assert frases[1] == "También jugó 1 partida amistosa, que no mueve el Elo."
    assert _frases_de_competencia(_competencia(total=0, partidas_elo=0)) == []


def test_las_frases_cuentan_el_puesto_del_ultimo_torneo_terminado_sin_nombrar_a_nadie() -> None:
    torneos = [
        {"nombre": "Relámpago", "estado": "en_juego", "puesto": 1, "de": 6, "puntos": 1.0, "partidas": 1, "victorias": 1},
        {"nombre": "De otoño", "estado": "terminado", "puesto": 2, "de": 8, "puntos": 3.5, "partidas": 4, "victorias": 3},
    ]
    frases = _frases_de_competencia(_competencia(puestos_en_torneos=torneos))
    assert any("«De otoño» quedó en el puesto 2 de 8, con 3.5 puntos" in frase for frase in frases)
    assert not any("Relámpago" in frase for frase in frases)  # el que sigue en juego todavía no tiene puesto definitivo
