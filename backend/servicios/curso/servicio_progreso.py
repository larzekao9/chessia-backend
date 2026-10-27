"""Progreso de los alumnos: el antes y el después, el informe para el docente y los responsables, y la actividad de un grupo.

**Antes y después.** El «antes» es el diagnóstico: el promedio de las primeras `PARTIDAS_DIAGNOSTICO` partidas calibradas de un
alumno. El «después» es el promedio de sus últimas partidas calibradas *posteriores* al diagnóstico (hasta
`PARTIDAS_DIAGNOSTICO`). Nunca se solapan: con menos de una partida posterior no hay comparación, y mientras haya menos de
`PARTIDAS_DIAGNOSTICO` posteriores se marca como provisional. La precisión es la de las jugadas del alumno contra Stockfish
(ver `servicio_calibracion`); el nivel y el rango salen de esa precisión con la misma regla que el nivel vigente.

**Informe.** Todo sale de datos que ya se guardan (calibraciones, partidas, medallas); no se inventa ni se estima nada que no se
haya medido. Las frases de «lectura» se arman con reglas fijas, sin un modelo de lenguaje.

**Grupo.** Un curso o una institución se resumen con las mismas cuentas: cuántos mejoraron, cuánto, cuántos están activos y a
quiénes hace tiempo que no se los ve.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import (
    CalibracionORM,
    CursoORM,
    EloMovimientoORM,
    InscripcionORM,
    InstitucionORM,
    PartidaORM,
    TorneoJugadorORM,
    TorneoORM,
    UsuarioORM,
)
from backend.servicios.calibracion.servicio_calibracion import PARTIDAS_DIAGNOSTICO
from backend.servicios.institucion import servicio_instituciones as instituciones
from backend.servicios.medallas import servicio_medallas
from backend.servicios.retroalimentacion import calcular_rango_desde_precision
from backend.servicios.usuario.servicio_estadisticas import calcular_estadisticas

UMBRAL_CAMBIO = 2.0
"""Puntos de precisión que tiene que moverse un alumno para decir que mejoró o bajó (menos que eso es ruido entre partidas)."""

DIAS_SIN_ACTIVIDAD = 14
"""Días sin jugar a partir de los cuales se avisa que un alumno se está yendo."""

PUNTOS_DE_LA_SERIE = 30
"""Cuántas calibraciones recientes se devuelven para dibujar la evolución."""

MIN_JUGADAS_FASE = 5
"""Jugadas analizadas que necesita una fase de la partida para compararla con las otras."""

NOMBRES_DE_FASE = {"apertura": "la apertura", "medio": "el medio juego", "final": "el final"}
NOMBRES_DE_ERROR = {"blunder": "errores graves", "error": "errores", "inexactitud": "imprecisiones"}

DIAS_VERDE = 7
"""Hasta estos días sin jugar un alumno se considera «al día» (verde); después pasa a amarillo y, desde `DIAS_SIN_ACTIVIDAD`, a rojo."""

DERROTAS_SEGUIDAS_AVISO = 3
"""Derrotas seguidas con compañeros a partir de las cuales se avisa al docente: un chico que pierde varias veces seguidas puede estar desanimado."""

ALUMNOS_PARA_EL_PLAN = 80
"""Cuántos alumnos como máximo se leen para armar el plan de clase de un grupo (cada uno pide sus estadísticas)."""

MIN_JUGADAS_FASE_GRUPO = 30
"""Jugadas analizadas que necesita una fase, sumando todo el grupo, para compararla con las otras."""

SUGERENCIA_POR_FASE = {
    "apertura": "Dedicar la próxima clase a la apertura: desarrollar las piezas, controlar el centro y enrocar pronto.",
    "medio": "Dedicar la próxima clase al medio juego: antes de mover, preguntarse qué amenaza el rival y qué piezas quedan sin defender.",
    "final": "Dedicar la próxima clase a los finales: activar al rey, crear peones pasados y practicar los mates básicos.",
}
SUGERENCIA_POR_ERROR = {
    "blunder": "Practicar el hábito de revisar cada jugada con «¿dejo algo sin defender?» antes de soltar la pieza.",
    "error": "Repasar partidas del grupo para encontrar juntos en qué jugada se torció la posición.",
    "inexactitud": "Proponer ejercicios de elegir entre dos jugadas buenas para afinar la precisión.",
}


def _ahora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _promedio(valores: list[float]) -> float | None:
    return round(sum(valores) / len(valores), 1) if valores else None


# --- antes y después de un alumno --------------------------------------------------------------------------------------------


def _nivel_y_rango(precision: float | None) -> tuple[int | None, str | None]:
    if precision is None:
        return None, None
    return calcular_rango_desde_precision(precision)


def comparar(calibraciones: list[CalibracionORM]) -> dict:
    """El antes y el después de un alumno a partir de sus calibraciones (de la más vieja a la más nueva).

    `comparable` es `True` cuando completó el diagnóstico y tiene al menos una partida calibrada después; `confirmado`, cuando esas
    partidas posteriores ya llenan la ventana de comparación. `tendencia` es `mejora`, `estable` o `baja` (o `sin_comparacion`).
    """
    total = len(calibraciones)
    antes = calibraciones[:PARTIDAS_DIAGNOSTICO]
    despues = calibraciones[PARTIDAS_DIAGNOSTICO:][-PARTIDAS_DIAGNOSTICO:] if total > PARTIDAS_DIAGNOSTICO else []
    diagnostico_completo = total >= PARTIDAS_DIAGNOSTICO
    comparable = diagnostico_completo and bool(despues)

    precision_antes = _promedio([c.precision_global for c in antes])
    precision_despues = _promedio([c.precision_global for c in despues])
    nivel_antes, rango_antes = _nivel_y_rango(precision_antes)
    nivel_despues, rango_despues = _nivel_y_rango(precision_despues)

    variacion = round(precision_despues - precision_antes, 1) if comparable else None
    if variacion is None:
        tendencia = "sin_comparacion"
    elif variacion >= UMBRAL_CAMBIO:
        tendencia = "mejora"
    elif variacion <= -UMBRAL_CAMBIO:
        tendencia = "baja"
    else:
        tendencia = "estable"
    return {
        "calibradas": total,
        "diagnostico_completo": diagnostico_completo,
        "comparable": comparable,
        "confirmado": comparable and len(despues) >= PARTIDAS_DIAGNOSTICO,
        "precision_antes": precision_antes,
        "nivel_antes": nivel_antes,
        "rango_antes": rango_antes,
        "precision_despues": precision_despues if comparable else None,
        "nivel_despues": nivel_despues if comparable else None,
        "rango_despues": rango_despues if comparable else None,
        "variacion_precision": variacion,
        "variacion_nivel": nivel_despues - nivel_antes if comparable and nivel_antes is not None and nivel_despues is not None else None,
        "tendencia": tendencia,
        "primera_en": antes[0].creado_en if antes else None,
        "ultima_en": calibraciones[-1].creado_en if calibraciones else None,
        "serie": [
            {"numero": total - min(total, PUNTOS_DE_LA_SERIE) + i + 1, "fecha": c.creado_en, "precision": round(c.precision_global, 1), "nivel": c.nivel_partida}
            for i, c in enumerate(calibraciones[-PUNTOS_DE_LA_SERIE:])
        ],
    }


def _calibraciones_por_alumno(db: Session, ids: list[int]) -> dict[int, list[CalibracionORM]]:
    por_alumno: dict[int, list[CalibracionORM]] = {i: [] for i in ids}
    if ids:
        for fila in db.scalars(
            select(CalibracionORM).where(CalibracionORM.usuario_id.in_(ids)).order_by(CalibracionORM.creado_en, CalibracionORM.id)
        ):
            por_alumno[fila.usuario_id].append(fila)
    return por_alumno


def progreso_de_alumnos(db: Session, ids: list[int]) -> dict[int, dict]:
    """`usuario_id -> comparar(...)` para todos los ids, con una sola consulta."""
    return {i: comparar(c) for i, c in _calibraciones_por_alumno(db, ids).items()}


# --- actividad -----------------------------------------------------------------------------------------------------------------


def actividad_de_alumnos(db: Session, ids: list[int]) -> dict[int, dict]:
    """`usuario_id -> {partidas, partidas_7_dias, partidas_30_dias, ultima_partida}` (sin las partidas de demostración)."""
    ahora = _ahora()
    datos = {i: {"partidas": 0, "partidas_7_dias": 0, "partidas_30_dias": 0, "ultima_partida": None} for i in ids}
    if not ids:
        return datos
    for usuario_id, iniciada_en in db.execute(
        select(PartidaORM.usuario_id, PartidaORM.iniciada_en).where(
            PartidaORM.usuario_id.in_(ids), PartidaORM.iniciada_en.is_not(None), PartidaORM.es_demostracion.is_(False)
        )
    ).all():
        fila = datos[usuario_id]
        fila["partidas"] += 1
        fila["partidas_7_dias"] += 1 if iniciada_en >= ahora - timedelta(days=7) else 0
        fila["partidas_30_dias"] += 1 if iniciada_en >= ahora - timedelta(days=30) else 0
        if fila["ultima_partida"] is None or iniciada_en > fila["ultima_partida"]:
            fila["ultima_partida"] = iniciada_en
    return datos


def _dias_desde(momento: datetime | None, ahora: datetime) -> int | None:
    return max((ahora - momento).days, 0) if momento is not None else None


# --- partidas entre personas y Elo --------------------------------------------------------------------------------------------


def _resultados_vacios() -> dict[str, int]:
    return {"ganadas": 0, "empatadas": 0, "perdidas": 0}


def competencia_de_alumnos(db: Session, ids: list[int]) -> dict[int, dict]:
    """`usuario_id -> {elo, partidas_elo, elo_provisional, elo_cambio_30_dias, amistosas, puntuadas, torneos, total, racha_derrotas}`.

    Cuenta las partidas terminadas **entre personas** con al menos `elo.JUGADAS_MINIMAS` jugadas, separadas en `torneos`, `puntuadas` (mueven el
    Elo) y `amistosas` (no lo mueven), cada una con `ganadas`, `empatadas` y `perdidas`. `racha_derrotas` son las derrotas seguidas más recientes.
    El cambio del Elo en 30 días sale de los movimientos que anotó `servicios/social/elo.py`.
    """
    from backend.servicios.social.elo import ELO_INICIAL, JUGADAS_MINIMAS, es_provisional

    datos = {
        i: {
            "elo": ELO_INICIAL,
            "partidas_elo": 0,
            "elo_provisional": True,
            "elo_cambio_30_dias": 0,
            "amistosas": _resultados_vacios(),
            "puntuadas": _resultados_vacios(),
            "torneos": _resultados_vacios(),
            "total": 0,
            "racha_derrotas": 0,
        }
        for i in ids
    }
    if not ids:
        return datos
    for usuario_id, elo, partidas_elo in db.execute(select(UsuarioORM.id, UsuarioORM.elo, UsuarioORM.partidas_elo).where(UsuarioORM.id.in_(ids))).all():
        datos[usuario_id].update(elo=elo, partidas_elo=partidas_elo, elo_provisional=es_provisional(partidas_elo))

    conjunto = set(ids)
    partidas = db.execute(
        select(
            PartidaORM.usuario_blancas_id, PartidaORM.usuario_negras_id, PartidaORM.resultado, PartidaORM.tipo, PartidaORM.puntuada, PartidaORM.jugadas_uci
        )
        .where(
            PartidaORM.tipo_oponente == "humano",
            PartidaORM.resultado.is_not(None),
            or_(PartidaORM.usuario_blancas_id.in_(ids), PartidaORM.usuario_negras_id.in_(ids)),
        )
        .order_by(func.coalesce(PartidaORM.actualizada_en, PartidaORM.fecha), PartidaORM.id)
    ).all()
    for blancas_id, negras_id, resultado, tipo, puntuada, jugadas_uci in partidas:
        if resultado not in ("1-0", "0-1", "1/2-1/2") or len((jugadas_uci or "").split()) < JUGADAS_MINIMAS:
            continue
        categoria = "torneos" if tipo == "torneo" else "puntuadas" if puntuada is not False else "amistosas"
        for usuario_id, gana, pierde in ((blancas_id, resultado == "1-0", resultado == "0-1"), (negras_id, resultado == "0-1", resultado == "1-0")):
            if usuario_id not in conjunto:
                continue
            fila = datos[usuario_id]
            fila[categoria]["ganadas" if gana else "perdidas" if pierde else "empatadas"] += 1
            fila["total"] += 1
            fila["racha_derrotas"] = fila["racha_derrotas"] + 1 if pierde else 0

    corte = _ahora() - timedelta(days=30)
    for usuario_id, antes, despues in db.execute(
        select(EloMovimientoORM.usuario_id, EloMovimientoORM.elo_antes, EloMovimientoORM.elo_despues).where(
            EloMovimientoORM.usuario_id.in_(ids), EloMovimientoORM.creado_en >= corte
        )
    ).all():
        datos[usuario_id]["elo_cambio_30_dias"] += despues - antes
    return datos


PUNTOS_DE_LA_SERIE_ELO = 30
"""Cuántas partidas recientes con compañeros se devuelven para dibujar la evolución del Elo."""

TORNEOS_EN_EL_INFORME = 5
"""Cuántos de los últimos torneos del alumno se cuentan en su informe."""


def serie_de_elo(db: Session, alumno_id: int) -> list[dict]:
    """La evolución del Elo de un alumno: el punto de partida (`numero` 0, sin resultado) y luego el Elo tras cada una de sus últimas partidas puntuadas.

    Vacía si todavía no movió su Elo. Cada punto trae `numero`, `fecha`, `elo` y `resultado` (`ganó`, `empató` o `perdió`).
    """
    filas = list(
        db.scalars(
            select(EloMovimientoORM).where(EloMovimientoORM.usuario_id == alumno_id).order_by(EloMovimientoORM.id.desc()).limit(PUNTOS_DE_LA_SERIE_ELO)
        )
    )[::-1]
    if not filas:
        return []
    serie = [{"numero": 0, "fecha": filas[0].creado_en, "elo": filas[0].elo_antes, "resultado": None}]
    for numero, fila in enumerate(filas, start=1):
        resultado = "ganó" if fila.resultado == 1.0 else "empató" if fila.resultado == 0.5 else "perdió"
        serie.append({"numero": numero, "fecha": fila.creado_en, "elo": fila.elo_despues, "resultado": resultado})
    return serie


def puestos_en_torneos(db: Session, alumno_id: int) -> list[dict]:
    """En qué puesto quedó el alumno en sus últimos torneos (el más nuevo primero), sin nombrar a nadie más: `{nombre, estado, puesto, de, puntos, partidas, victorias, fecha}`."""
    from backend.servicios.social import torneos

    ids = db.scalars(
        select(TorneoJugadorORM.torneo_id).where(TorneoJugadorORM.usuario_id == alumno_id).order_by(TorneoJugadorORM.torneo_id.desc()).limit(TORNEOS_EN_EL_INFORME)
    ).all()
    salida = []
    for torneo_id in ids:
        torneo = db.get(TorneoORM, torneo_id)
        if torneo is None:
            continue
        tabla = torneos.clasificacion(db, torneo)
        propio = next((fila for fila in tabla if fila["id"] == alumno_id), None)
        if propio is None:
            continue
        salida.append(
            {
                "nombre": torneo.nombre,
                "estado": torneo.estado,
                "puesto": propio["puesto"],
                "de": len(tabla),
                "puntos": propio["puntos"],
                "partidas": propio["partidas"],
                "victorias": propio["victorias"],
                "fecha": torneo.creado_en,
            }
        )
    return salida


def estado_de_actividad(dias_sin_jugar: int | None) -> str:
    """El semáforo de constancia: `verde` (jugó en los últimos `DIAS_VERDE` días), `amarillo`, `rojo` (`DIAS_SIN_ACTIVIDAD` o más) o `sin_empezar`."""
    if dias_sin_jugar is None:
        return "sin_empezar"
    if dias_sin_jugar <= DIAS_VERDE:
        return "verde"
    return "amarillo" if dias_sin_jugar < DIAS_SIN_ACTIVIDAD else "rojo"


def motivos_de_atencion(activo: bool, dias_sin_jugar: int | None, tendencia: str, comparable: bool, racha_derrotas: int) -> list[str]:
    """Por qué conviene mirar hoy a un alumno (lista vacía si no hace falta): sin empezar, hace tiempo que no juega, bajó de nivel o perdió varias seguidas."""
    if not activo:
        return []
    motivos = []
    if dias_sin_jugar is None:
        motivos.append("Todavía no empezó a jugar")
    elif dias_sin_jugar >= DIAS_SIN_ACTIVIDAD:
        motivos.append(f"Hace {dias_sin_jugar} días que no juega")
    if comparable and tendencia == "baja":
        motivos.append("Bajó su nivel desde el diagnóstico")
    if racha_derrotas >= DERROTAS_SEGUIDAS_AVISO:
        motivos.append(f"Perdió sus últimas {racha_derrotas} partidas con compañeros")
    return motivos


def plan_de_clase(db: Session, ids: list[int]) -> dict | None:
    """Qué conviene enseñar al grupo: en qué fase de la partida se acierta menos y qué errores se repiten, sumando a todos los alumnos.

    Todo sale de las estadísticas que ya se miden por alumno (`calcular_estadisticas`); las sugerencias son frases fijas según la fase más
    débil y el error más repetido, no una predicción. `None` si todavía no hay partidas analizadas.
    """
    por_fase: dict[str, list[float]] = {fase: [0.0, 0.0] for fase in NOMBRES_DE_FASE}  # fase -> [jugadas, suma de precisión x jugadas]
    errores: Counter = Counter()
    con_datos = 0
    leidos = ids[:ALUMNOS_PARA_EL_PLAN]
    for alumno_id in leidos:
        estadisticas = calcular_estadisticas(db, alumno_id)
        usado = False
        for fase in estadisticas["precision_por_fase"]:
            if fase["fase"] in por_fase and fase["precision"] is not None and fase["jugadas"] > 0:
                por_fase[fase["fase"]][0] += fase["jugadas"]
                por_fase[fase["fase"]][1] += fase["precision"] * fase["jugadas"]
                usado = True
        for error in estadisticas["top_errores"]:
            errores[error["tipo"]] += error["cantidad"]
            usado = True
        con_datos += 1 if usado else 0
    if con_datos == 0:
        return None

    fases = [
        {"fase": fase, "jugadas": int(jugadas), "precision": round(suma / jugadas, 1) if jugadas else None}
        for fase, (jugadas, suma) in por_fase.items()
    ]
    medidas = [f for f in fases if f["precision"] is not None and f["jugadas"] >= MIN_JUGADAS_FASE_GRUPO]
    sugerencias: list[str] = []
    fase_mas_debil = None
    if len(medidas) >= 2:
        mejor, peor = max(medidas, key=lambda f: f["precision"]), min(medidas, key=lambda f: f["precision"])
        if mejor["precision"] - peor["precision"] >= 5:
            fase_mas_debil = peor["fase"]
            sugerencias.append(
                f"En el grupo se acierta menos {NOMBRES_DE_FASE[peor['fase']]} ({peor['precision']:g} %) que {NOMBRES_DE_FASE[mejor['fase']]} ({mejor['precision']:g} %). "
                + SUGERENCIA_POR_FASE[peor["fase"]]
            )
    lista_errores = [{"tipo": tipo, "nombre": NOMBRES_DE_ERROR.get(tipo, tipo), "cantidad": cantidad} for tipo, cantidad in errores.most_common(3)]
    if lista_errores and lista_errores[0]["tipo"] in SUGERENCIA_POR_ERROR:
        principal = lista_errores[0]
        sugerencias.append(f"Lo que más se repite son los {principal['nombre']} ({principal['cantidad']}). " + SUGERENCIA_POR_ERROR[principal["tipo"]])
    return {
        "alumnos_con_datos": con_datos,
        "alumnos_leidos": len(leidos),
        "alumnos_totales": len(ids),
        "fases": fases,
        "errores": lista_errores,
        "fase_mas_debil": fase_mas_debil,
        "sugerencias": sugerencias,
    }


# --- el informe de un alumno -------------------------------------------------------------------------------------------------


def _curso_docente_e_institucion(db: Session, alumno_id: int) -> tuple[str | None, str | None, str | None]:
    """El curso (el último en el que se inscribió), su docente y la institución de ese docente, si la tiene."""
    fila = db.execute(
        select(CursoORM.nombre, UsuarioORM.nombre, UsuarioORM.institucion_id)
        .join(InscripcionORM, InscripcionORM.curso_id == CursoORM.id)
        .join(UsuarioORM, UsuarioORM.id == CursoORM.facilitador_id)
        .where(InscripcionORM.usuario_id == alumno_id)
        .order_by(InscripcionORM.id.desc())
    ).first()
    if fila is None:
        return None, None, None
    institucion = db.get(InstitucionORM, fila[2]) if fila[2] is not None else None
    return fila[0], fila[1], institucion.nombre if institucion else None


def _fase_mas_fuerte_y_mas_debil(por_fase: list[dict]) -> tuple[dict | None, dict | None]:
    medidas = [f for f in por_fase if f["precision"] is not None and f["jugadas"] >= MIN_JUGADAS_FASE]
    if len(medidas) < 2:
        return None, None
    mejor, peor = max(medidas, key=lambda f: f["precision"]), min(medidas, key=lambda f: f["precision"])
    return (mejor, peor) if mejor["precision"] - peor["precision"] >= 10 else (None, None)


def _veces(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


def _frases_de_competencia(competencia: dict) -> list[str]:
    """Las frases del informe sobre sus partidas con compañeros y su Elo (vacío si no jugó ninguna)."""
    if competencia["total"] == 0:
        return []
    frases = []
    serias = {clave: competencia["puntuadas"][clave] + competencia["torneos"][clave] for clave in ("ganadas", "empatadas", "perdidas")}
    if sum(serias.values()):
        texto = (
            f"Con compañeros, en partidas puntuadas y torneos, ganó {serias['ganadas']}, empató {serias['empatadas']} y perdió {serias['perdidas']}. "
            f"Su Elo es {competencia['elo']}"
        )
        texto += " (provisional: todavía son pocas partidas)" if competencia["elo_provisional"] else ""
        cambio = competencia["elo_cambio_30_dias"]
        if cambio:
            texto += f" y {'subió' if cambio > 0 else 'bajó'} {abs(cambio)} en los últimos 30 días"
        frases.append(texto + ".")
    ultimo_torneo = next((t for t in competencia.get("puestos_en_torneos", []) if t["estado"] == "terminado" and t["partidas"] > 0), None)
    if ultimo_torneo is not None:
        frases.append(f"En el torneo «{ultimo_torneo['nombre']}» quedó en el puesto {ultimo_torneo['puesto']} de {ultimo_torneo['de']}, con {ultimo_torneo['puntos']:g} puntos.")
    amistosas = sum(competencia["amistosas"].values())
    if amistosas:
        frases.append(f"También jugó {_veces(amistosas, 'partida amistosa', 'partidas amistosas')}, que no {'mueve' if amistosas == 1 else 'mueven'} el Elo.")
    if competencia["racha_derrotas"] >= DERROTAS_SEGUIDAS_AVISO:
        frases.append(f"Viene de perder {competencia['racha_derrotas']} partidas seguidas con compañeros: conviene animarlo.")
    return frases


def _lectura(nombre: str, progreso: dict, actividad: dict, estadisticas: dict, competencia: dict | None = None) -> list[str]:
    """Las frases del informe, en lenguaje llano, armadas con reglas fijas a partir de lo que se midió."""
    primero = nombre.split()[0] if nombre.split() else "El alumno"
    frases: list[str] = []
    if not progreso["diagnostico_completo"]:
        faltan = PARTIDAS_DIAGNOSTICO - progreso["calibradas"]
        frases.append(
            f"{primero} todavía está en su diagnóstico: con {PARTIDAS_DIAGNOSTICO} partidas medidas se fija su punto de partida (le "
            f"{'falta' if faltan == 1 else 'faltan'} {faltan})."
        )
    elif not progreso["comparable"]:
        frases.append(
            f"{primero} terminó su diagnóstico: empezó en el nivel {progreso['nivel_antes']} ({progreso['rango_antes']}), con "
            f"{progreso['precision_antes']:g} % de precisión. Con una partida más ya se puede ver cómo evoluciona."
        )
    else:
        cambio = abs(progreso["variacion_precision"])
        inicio = f"{primero} empezó en el nivel {progreso['nivel_antes']} ({progreso['rango_antes']}) con {progreso['precision_antes']:g} % de precisión y hoy está en el nivel {progreso['nivel_despues']} ({progreso['rango_despues']}) con {progreso['precision_despues']:g} %"
        if progreso["tendencia"] == "mejora":
            frases.append(f"{inicio}: mejoró {cambio:g} puntos.")
        elif progreso["tendencia"] == "baja":
            frases.append(f"{inicio}: bajó {cambio:g} puntos. Conviene seguir practicando con su docente para recuperar el ritmo.")
        else:
            frases.append(f"{inicio}: se mantiene estable.")
        if not progreso["confirmado"]:
            frases.append("Es una comparación provisional: se afirma con más partidas.")

    if actividad["partidas"]:
        frases.append(
            f"Jugó {actividad['partidas']} partida{'s' if actividad['partidas'] != 1 else ''}, {actividad['partidas_30_dias']} en los últimos 30 días."
        )
    mejor, peor = _fase_mas_fuerte_y_mas_debil(estadisticas["precision_por_fase"])
    if mejor is not None and peor is not None:
        frases.append(f"Juega mejor {NOMBRES_DE_FASE[mejor['fase']]} ({mejor['precision']:g} % de jugadas acertadas) y tiene más para trabajar en {NOMBRES_DE_FASE[peor['fase']]} ({peor['precision']:g} %).")
    if estadisticas["top_errores"]:
        principal = estadisticas["top_errores"][0]
        frases.append(f"Lo que más se repite en sus partidas son los {NOMBRES_DE_ERROR.get(principal['tipo'], principal['tipo'])} ({principal['cantidad']}).")
    if competencia is not None:
        frases.extend(_frases_de_competencia(competencia))
    return frases


def informe_alumno(db: Session, alumno: UsuarioORM) -> dict:
    """El informe de progreso de un alumno, listo para mostrárselo a su docente o a su responsable.

    Reúne el antes y el después, la evolución partida a partida, la constancia, los resultados, la precisión por fase de la
    partida, los errores más repetidos, las medallas y unas frases de lectura en lenguaje llano.
    """
    ahora = _ahora()
    progreso = progreso_de_alumnos(db, [alumno.id])[alumno.id]
    actividad = actividad_de_alumnos(db, [alumno.id])[alumno.id]
    estadisticas = calcular_estadisticas(db, alumno.id)
    curso, docente, institucion = _curso_docente_e_institucion(db, alumno.id)
    medallas = servicio_medallas.resumen_para_ranking(db, [alumno.id]).get(alumno.id, {"total": 0, "destacadas": []})
    competencia = competencia_de_alumnos(db, [alumno.id])[alumno.id]
    competencia["serie_elo"] = serie_de_elo(db, alumno.id)
    competencia["puestos_en_torneos"] = puestos_en_torneos(db, alumno.id)
    return {
        "alumno": {
            "id": alumno.id,
            "nombre": alumno.nombre,
            "nivel": alumno.nivel_estimado,
            "rango": alumno.rango_estimado,
            "curso": curso,
            "docente": docente,
            "institucion": institucion,
            "tutor_nombre": alumno.tutor_nombre,
            "tutor_telefono": alumno.tutor_telefono,
            "tutor_email": alumno.tutor_email,
        },
        "generado_en": ahora,
        "progreso": progreso,
        "actividad": {**actividad, "dias_sin_jugar": _dias_desde(actividad["ultima_partida"], ahora)},
        "resultados": {
            "ganadas": estadisticas["partidas_ganadas"],
            "perdidas": estadisticas["partidas_perdidas"],
            "tablas": estadisticas["partidas_tablas"],
            "racha_victorias": estadisticas["racha_victoria_actual"],
            "precision_promedio": estadisticas["precision_promedio"],
        },
        "precision_por_fase": estadisticas["precision_por_fase"],
        "errores_frecuentes": [
            {"tipo": e["tipo"], "nombre": NOMBRES_DE_ERROR.get(e["tipo"], e["tipo"]), "cantidad": e["cantidad"]} for e in estadisticas["top_errores"][:3]
        ],
        "medallas": medallas,
        "competencia": competencia,
        "lectura": _lectura(alumno.nombre, progreso, actividad, estadisticas, competencia),
    }


# --- un grupo de alumnos (curso o institución) -------------------------------------------------------------------------------


def _alumnos(db: Session, ids: list[int]) -> list[UsuarioORM]:
    if not ids:
        return []
    return list(db.scalars(select(UsuarioORM).where(UsuarioORM.id.in_(ids)).order_by(UsuarioORM.nombre)))


def progreso_de_grupo(db: Session, ids: list[int], con_plan: bool = False) -> dict:
    """El antes y el después de un grupo de alumnos y su actividad: un resumen y una fila por alumno.

    El resumen cuenta solo a quienes ya se pueden comparar (`comparables`); el porcentaje de los que mejoraron es sobre ellos, no
    sobre todo el grupo, para no mezclar a los que recién empiezan.

    Cada fila trae además su Elo y sus partidas con compañeros (`competencia_de_alumnos`), el semáforo de constancia y los motivos por los que
    conviene mirarlo hoy. Con `con_plan` se agrega el `plan_de_clase` del grupo (lee las estadísticas de cada alumno, hasta `ALUMNOS_PARA_EL_PLAN`).
    """
    alumnos = _alumnos(db, ids)
    ids = [a.id for a in alumnos]
    progresos = progreso_de_alumnos(db, ids)
    actividad = actividad_de_alumnos(db, ids)
    competencias = competencia_de_alumnos(db, ids)
    ahora = _ahora()

    filas = []
    for alumno in alumnos:
        datos, movimiento, comp = progresos[alumno.id], actividad[alumno.id], competencias[alumno.id]
        dias_sin_jugar = _dias_desde(movimiento["ultima_partida"], ahora)
        filas.append(
            {
                "id": alumno.id,
                "nombre": alumno.nombre,
                "activo": alumno.activo,
                "nivel_actual": alumno.nivel_estimado,
                "rango_actual": alumno.rango_estimado,
                "tendencia": datos["tendencia"],
                "comparable": datos["comparable"],
                "confirmado": datos["confirmado"],
                "calibradas": datos["calibradas"],
                "nivel_antes": datos["nivel_antes"],
                "nivel_despues": datos["nivel_despues"],
                "precision_antes": datos["precision_antes"],
                "precision_despues": datos["precision_despues"],
                "variacion_precision": datos["variacion_precision"],
                "partidas": movimiento["partidas"],
                "partidas_30_dias": movimiento["partidas_30_dias"],
                "ultima_partida": movimiento["ultima_partida"],
                "dias_sin_jugar": dias_sin_jugar,
                "estado_actividad": estado_de_actividad(dias_sin_jugar),
                "atencion": motivos_de_atencion(alumno.activo, dias_sin_jugar, datos["tendencia"], datos["comparable"], comp["racha_derrotas"]),
                "elo": comp["elo"],
                "partidas_elo": comp["partidas_elo"],
                "elo_provisional": comp["elo_provisional"],
                "elo_cambio_30_dias": comp["elo_cambio_30_dias"],
                "amistosas": comp["amistosas"],
                "puntuadas": comp["puntuadas"],
                "torneos": comp["torneos"],
                "partidas_companeros": comp["total"],
                "racha_derrotas": comp["racha_derrotas"],
                "es_menor": None if alumno.edad is None else alumno.edad < 18,
                "tutor_nombre": alumno.tutor_nombre,
                "tutor_telefono": alumno.tutor_telefono,
                "tutor_email": alumno.tutor_email,
            }
        )

    comparables = [f for f in filas if f["comparable"]]
    mejoraron = sum(1 for f in comparables if f["tendencia"] == "mejora")
    resumen = {
        "alumnos": len(filas),
        "comparables": len(comparables),
        "sin_diagnostico": sum(1 for f in filas if f["calibradas"] < PARTIDAS_DIAGNOSTICO),
        "mejoraron": mejoraron,
        "estables": sum(1 for f in comparables if f["tendencia"] == "estable"),
        "bajaron": sum(1 for f in comparables if f["tendencia"] == "baja"),
        "porcentaje_mejoraron": round(mejoraron / len(comparables) * 100) if comparables else None,
        "mejora_promedio_precision": _promedio([f["variacion_precision"] for f in comparables]),
        "precision_antes": _promedio([f["precision_antes"] for f in comparables]),
        "precision_despues": _promedio([f["precision_despues"] for f in comparables]),
        "nivel_antes": _promedio([f["nivel_antes"] for f in comparables]),
        "nivel_despues": _promedio([f["nivel_despues"] for f in comparables]),
        "partidas": sum(f["partidas"] for f in filas),
        "partidas_7_dias": sum(actividad[i]["partidas_7_dias"] for i in ids),
        "partidas_30_dias": sum(f["partidas_30_dias"] for f in filas),
        "activos_7_dias": sum(1 for i in ids if actividad[i]["partidas_7_dias"] > 0),
        "activos_30_dias": sum(1 for f in filas if f["partidas_30_dias"] > 0),
        "sin_actividad": sum(1 for f in filas if f["activo"] and (f["dias_sin_jugar"] is None or f["dias_sin_jugar"] >= DIAS_SIN_ACTIVIDAD)),
        "dias_sin_actividad": DIAS_SIN_ACTIVIDAD,
        "necesitan_atencion": sum(1 for f in filas if f["atencion"]),
        "semaforo": {clave: sum(1 for f in filas if f["activo"] and f["estado_actividad"] == clave) for clave in ("verde", "amarillo", "rojo", "sin_empezar")},
        "elo_promedio": _promedio([f["elo"] for f in filas if f["partidas_elo"] > 0]),
        "partidas_companeros": sum(f["partidas_companeros"] for f in filas),
    }
    filas.sort(key=lambda f: (f["variacion_precision"] is None, -(f["variacion_precision"] or 0), f["nombre"].lower()))
    return {"resumen": resumen, "alumnos": filas, "plan_de_clase": plan_de_clase(db, ids) if con_plan else None}


def ids_del_curso(db: Session, curso_id: int) -> list[int]:
    return list(db.scalars(select(InscripcionORM.usuario_id).where(InscripcionORM.curso_id == curso_id)))


def progreso_de_institucion(db: Session, institucion: InstitucionORM) -> dict:
    """El antes y el después de todos los alumnos de una institución (los inscriptos en los cursos de sus docentes)."""
    return {"nombre": institucion.nombre, **progreso_de_grupo(db, [a["id"] for a in instituciones.alumnos_de(db, institucion)], con_plan=True)}


# --- la salud de las instituciones clientes ------------------------------------------------------------------------------------

DIAS_SIN_ALUMNOS = 7
"""Días desde que se creó una institución sin que haya entrado un solo alumno, a partir de los cuales se avisa."""

FRACCION_ACTIVA_MINIMA = 0.25
"""Fracción de los alumnos que tiene que haber jugado en el último mes para no dar aviso."""


def _riesgo(alumnos: int, activos_30: int, dias_sin_actividad: int | None, dias_de_alta: int) -> str | None:
    """Por qué conviene llamar a la institución, o `None` si anda bien."""
    if alumnos == 0:
        return "Todavía no entró ningún alumno" if dias_de_alta >= DIAS_SIN_ALUMNOS else None
    if dias_sin_actividad is None or dias_sin_actividad >= DIAS_SIN_ACTIVIDAD:
        return f"Nadie jugó en los últimos {DIAS_SIN_ACTIVIDAD} días"
    if activos_30 < alumnos * FRACCION_ACTIVA_MINIMA:
        return "Menos de un cuarto de sus alumnos jugó en el último mes"
    return None


def salud_de_instituciones(db: Session) -> list[dict]:
    """Una fila por institución con su actividad y su avance, las que están en riesgo primero.

    Sirve para decidir a quién llamar antes de que no renueve: una institución que paga pero cuyos alumnos no juegan es la que
    va a cancelar.
    """
    ahora = _ahora()
    filas = []
    for institucion in instituciones.listar(db):
        grupo = progreso_de_institucion(db, institucion)
        resumen = grupo["resumen"]
        ultima = max((f["ultima_partida"] for f in grupo["alumnos"] if f["ultima_partida"] is not None), default=None)
        dias_sin = _dias_desde(ultima, ahora)
        motivo = _riesgo(resumen["alumnos"], resumen["activos_30_dias"], dias_sin, _dias_desde(institucion.creado_en, ahora) or 0)
        filas.append(
            {
                "institucion_id": institucion.id,
                "nombre": institucion.nombre,
                "es_demostracion": institucion.es_demostracion,
                "es_prueba": institucion.es_prueba,
                "alumnos": resumen["alumnos"],
                "activos_7_dias": resumen["activos_7_dias"],
                "activos_30_dias": resumen["activos_30_dias"],
                "partidas_30_dias": resumen["partidas_30_dias"],
                "comparables": resumen["comparables"],
                "mejoraron": resumen["mejoraron"],
                "porcentaje_mejoraron": resumen["porcentaje_mejoraron"],
                "ultima_actividad": ultima,
                "dias_sin_actividad": dias_sin,
                "en_riesgo": motivo is not None and not institucion.es_demostracion,
                "motivo_riesgo": None if institucion.es_demostracion else motivo,
            }
        )
    filas.sort(key=lambda f: (not f["en_riesgo"], f["es_demostracion"], f["nombre"].lower()))
    return filas
