"""Torneos relámpago de un curso, por sistema suizo.

El docente arma el torneo (nombre, reloj y cantidad de rondas), los alumnos se inscriben (o el docente los inscribe a todos) y, ronda por ronda, el sistema los
empareja por puntos: 1 por victoria, 0,5 por tablas y 1 por descanso (si son impares, a uno le toca descansar cada ronda, y a nadie dos veces antes que a todos). Quien tiene
los mismos puntos que otro no vuelve a enfrentarlo mientras se pueda evitar, y los colores se reparten para que nadie juegue siempre con blancas.

Cada partida es una sala (`servicio_sala`) de tipo `torneo`: el reloj corre desde que se arma la ronda, así que quien no se presenta pierde por tiempo, y nadie pierde por
ausencia antes de aparecer. Los resultados se leen de las salas cuando alguien consulta el torneo (no hay tareas programadas) y, con ellos, se suman los puntos.
Se puede forzar el cierre de una ronda con partidas sin terminar: esas quedan sin ganador y no suman puntos.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.modelos.tablas_orm import InscripcionORM, TorneoJugadorORM, TorneoORM, TorneoPartidaORM, UsuarioORM
from backend.servicios.sala import servicio_sala
from backend.servicios.social import reglas
from backend.servicios.social.amigos import ErrorSocial, persona

RONDAS_MAXIMAS = 9
JUGADORES_MAXIMOS = 64
PUNTOS_POR_RESULTADO = {"1-0": (1.0, 0.0), "0-1": (0.0, 1.0), "1/2-1/2": (0.5, 0.5), "0-0": (0.0, 0.0)}


# --- armar el torneo -----------------------------------------------------------------------------------------------------------------


def crear(db: Session, curso_id: int, creador_id: int, nombre: str, control_tiempo_ms: int, rondas: int) -> TorneoORM:
    """Arma un torneo para el curso, abierto a inscripciones. Un curso tiene un solo torneo sin terminar a la vez.

    Raises:
        ErrorSocial: si el nombre, el reloj o las rondas no sirven (400) o ya hay un torneo en marcha en el curso (409).
    """
    nombre = " ".join((nombre or "").split())
    if not 3 <= len(nombre) <= 60:
        raise ErrorSocial("El nombre del torneo tiene que tener entre 3 y 60 caracteres")
    if control_tiempo_ms not in servicio_sala.CONTROLES_DE_TIEMPO_MS or control_tiempo_ms == 0:
        raise ErrorSocial("Un torneo relámpago se juega con reloj: elegí 3, 5, 10 o 15 minutos por lado")
    if not 1 <= rondas <= RONDAS_MAXIMAS:
        raise ErrorSocial(f"Las rondas tienen que ser entre 1 y {RONDAS_MAXIMAS}")
    if torneo_en_marcha(db, curso_id) is not None:
        raise ErrorSocial("Este curso ya tiene un torneo sin terminar: cerralo antes de armar otro.", 409)
    torneo = TorneoORM(curso_id=curso_id, nombre=nombre, control_tiempo_ms=control_tiempo_ms, rondas=rondas, creado_por=creador_id)
    db.add(torneo)
    db.commit()
    db.refresh(torneo)
    return torneo


def torneo_en_marcha(db: Session, curso_id: int) -> TorneoORM | None:
    """El torneo del curso que todavía no terminó (inscribiéndose o en juego), o `None`."""
    return db.scalar(select(TorneoORM).where(TorneoORM.curso_id == curso_id, TorneoORM.estado != "terminado").order_by(TorneoORM.id.desc()))


def del_curso(db: Session, curso_id: int, limite: int = 10) -> list[TorneoORM]:
    """Los últimos torneos del curso, el más nuevo primero."""
    return list(db.scalars(select(TorneoORM).where(TorneoORM.curso_id == curso_id).order_by(TorneoORM.id.desc()).limit(limite)))


def _jugadores(db: Session, torneo_id: int) -> list[TorneoJugadorORM]:
    return list(db.scalars(select(TorneoJugadorORM).where(TorneoJugadorORM.torneo_id == torneo_id).order_by(TorneoJugadorORM.id)))


def _inscripto(db: Session, torneo_id: int, usuario_id: int) -> TorneoJugadorORM | None:
    return db.scalar(select(TorneoJugadorORM).where(TorneoJugadorORM.torneo_id == torneo_id, TorneoJugadorORM.usuario_id == usuario_id))


# --- inscripciones ------------------------------------------------------------------------------------------------------------------


def inscribir(db: Session, torneo: TorneoORM, usuario: UsuarioORM) -> None:
    """Inscribe a un alumno del curso. No hace nada si ya estaba.

    Raises:
        ErrorSocial: si no es un jugador del curso (403), si el torneo ya empezó o está lleno (409), o si le falta la autorización de su familia (403).
    """
    pertenece = db.scalar(select(InscripcionORM.id).where(InscripcionORM.usuario_id == usuario.id, InscripcionORM.curso_id == torneo.curso_id))
    if usuario.rol != "jugador" or pertenece is None:
        raise ErrorSocial("Solo los alumnos del curso se inscriben en su torneo.", 403)
    if torneo.estado != "inscripcion":
        raise ErrorSocial("El torneo ya empezó: no se aceptan más inscripciones.", 409)
    if _inscripto(db, torneo.id, usuario.id) is not None:
        return
    if len(_jugadores(db, torneo.id)) >= JUGADORES_MAXIMOS:
        raise ErrorSocial(f"El torneo ya tiene {JUGADORES_MAXIMOS} jugadores.", 409)
    reglas.exigir(db, usuario, ignorar_pausa=True)
    db.add(TorneoJugadorORM(torneo_id=torneo.id, usuario_id=usuario.id))
    db.commit()


def retirar(db: Session, torneo: TorneoORM, usuario_id: int) -> None:
    """Un alumno se baja del torneo mientras todavía se están inscribiendo."""
    if torneo.estado != "inscripcion":
        raise ErrorSocial("El torneo ya empezó: no se puede bajar.", 409)
    fila = _inscripto(db, torneo.id, usuario_id)
    if fila is not None:
        db.delete(fila)
        db.commit()


def inscribir_a_todos(db: Session, torneo: TorneoORM) -> dict:
    """El docente inscribe a todos los alumnos activos del curso que puedan jugar con otros. Devuelve cuántos entraron y quiénes quedaron afuera (y por qué)."""
    alumnos = db.scalars(
        select(UsuarioORM)
        .join(InscripcionORM, InscripcionORM.usuario_id == UsuarioORM.id)
        .where(InscripcionORM.curso_id == torneo.curso_id, UsuarioORM.activo.is_(True), UsuarioORM.rol == "jugador")
        .order_by(UsuarioORM.nombre)
    )
    entraron, omitidos = 0, []
    for alumno in alumnos:
        try:
            ya = _inscripto(db, torneo.id, alumno.id) is not None
            inscribir(db, torneo, alumno)
            entraron += 0 if ya else 1
        except ErrorSocial as error:
            omitidos.append({"nombre": alumno.nombre, "motivo": str(error)})
    return {"inscritos": entraron, "omitidos": omitidos}


# --- emparejar y jugar ---------------------------------------------------------------------------------------------------------------


def emparejar(jugadores: list[dict], ya_jugaron: set[frozenset[int]]) -> tuple[list[tuple[int, int]], int | None]:
    """Reparte a los jugadores en parejas `(blancas, negras)` para una ronda, y quién descansa (`None` si son pares).

    `jugadores`: `{"id", "puntos", "descansos", "blancas"}` (`blancas` = cuántas veces jugó con blancas). Se empareja de arriba hacia abajo por puntos, evitando repetir
    rival cuando se puede; entre dos, juega con blancas el que menos veces las tuvo (y, si empatan, el de más puntos).
    """
    orden = sorted(jugadores, key=lambda j: (-j["puntos"], j["id"]))
    descansa = None
    if len(orden) % 2 == 1:
        elegido = min(orden, key=lambda j: (j["descansos"], j["puntos"], -j["id"]))
        orden.remove(elegido)
        descansa = elegido["id"]
    parejas: list[tuple[int, int]] = []
    while orden:
        a = orden.pop(0)
        b = next((x for x in orden if frozenset((a["id"], x["id"])) not in ya_jugaron), orden[0])
        orden.remove(b)
        a_blancas = (a["blancas"], -a["puntos"]) <= (b["blancas"], -b["puntos"])
        parejas.append((a["id"], b["id"]) if a_blancas else (b["id"], a["id"]))
    return parejas, descansa


def _partidas(db: Session, torneo_id: int, ronda: int | None = None) -> list[TorneoPartidaORM]:
    consulta = select(TorneoPartidaORM).where(TorneoPartidaORM.torneo_id == torneo_id).order_by(TorneoPartidaORM.ronda, TorneoPartidaORM.id)
    if ronda is not None:
        consulta = consulta.where(TorneoPartidaORM.ronda == ronda)
    return list(db.scalars(consulta))


def sincronizar(db: Session, torneo: TorneoORM) -> None:
    """Lee cómo terminaron las salas del torneo y suma los puntos. Si ya se jugaron todas las rondas y no queda nada pendiente, cierra el torneo."""
    if torneo.estado == "terminado":
        return
    jugadores = {j.usuario_id: j for j in _jugadores(db, torneo.id)}
    for partida in _partidas(db, torneo.id):
        if partida.resultado is not None or partida.codigo_sala is None:
            continue
        try:
            sala = servicio_sala.revisar_reloj_y_ausencia(partida.codigo_sala)  # cierra por tiempo o ausencia aunque nadie la esté mirando
        except KeyError:
            resultado = "0-0"  # la sala ya no existe
        else:
            if not (sala.terminada or sala.estado in ("terminada", "abandonada")):
                continue
            resultado = sala.resultado or "0-0"
        _anotar(partida, resultado, jugadores)
    pendientes = any(p.resultado is None for p in _partidas(db, torneo.id, torneo.ronda_actual))
    if torneo.estado == "en_juego" and torneo.ronda_actual >= torneo.rondas and not pendientes:
        torneo.estado = "terminado"
    db.commit()


def _anotar(partida: TorneoPartidaORM, resultado: str, jugadores: dict[int, TorneoJugadorORM]) -> None:
    partida.resultado = resultado
    blancas, negras = PUNTOS_POR_RESULTADO.get(resultado, (0.0, 0.0))
    if partida.blancas_id in jugadores:
        jugadores[partida.blancas_id].puntos += blancas
    if partida.negras_id in jugadores:
        jugadores[partida.negras_id].puntos += negras


def iniciar_ronda(db: Session, torneo: TorneoORM, forzar: bool = False) -> list[TorneoPartidaORM]:
    """Arma la ronda siguiente: empareja a los jugadores y les crea sus salas. Cierra la anterior si estaba completa (o si se fuerza).

    Raises:
        ErrorSocial: si el torneo ya terminó o ya jugó todas sus rondas (409), si hay menos de 2 jugadores (400) o si la ronda anterior tiene partidas sin terminar y no se forzó (409).
    """
    sincronizar(db, torneo)
    if torneo.estado == "terminado" or torneo.ronda_actual >= torneo.rondas:
        raise ErrorSocial("El torneo ya jugó todas sus rondas.", 409)
    sin_terminar = [p for p in _partidas(db, torneo.id, torneo.ronda_actual) if p.resultado is None]
    if sin_terminar and not forzar:
        raise ErrorSocial(f"Faltan terminar {len(sin_terminar)} partidas de la ronda {torneo.ronda_actual}. Esperá a que terminen o forzá el cierre.", 409)
    jugadores = {j.usuario_id: j for j in _jugadores(db, torneo.id)}
    # Comprobar a todos antes de crear una sala evita partidas simultáneas o una ronda creada a medias.
    salas_previas = {p.codigo_sala for p in sin_terminar}
    for usuario_id in jugadores:
        usuario = db.get(UsuarioORM, usuario_id)
        pertenece = db.scalar(select(InscripcionORM.id).where(InscripcionORM.usuario_id == usuario_id, InscripcionORM.curso_id == torneo.curso_id))
        if usuario is None or not usuario.activo or pertenece is None:
            raise ErrorSocial("Hay jugadores inactivos o que salieron del curso. Revisá las inscripciones antes de iniciar otra ronda.", 409)
        reglas.exigir(db, usuario, ignorar_pausa=True)
        activa = servicio_sala.sala_activa_de(usuario_id)
        if activa is not None and activa.codigo_sala not in salas_previas:
            raise ErrorSocial(f"{usuario.nombre} tiene otra partida abierta. Tiene que terminarla o cancelarla antes de iniciar la ronda.", 409)
    for partida in sin_terminar:  # forzado: quedan sin ganador
        if partida.codigo_sala:
            try:
                servicio_sala.cerrar_sin_ganador(partida.codigo_sala)
            except KeyError:
                pass
        _anotar(partida, "0-0", jugadores)
    if len(jugadores) < 2:
        raise ErrorSocial("Hacen falta al menos 2 jugadores inscritos para empezar.")

    previas = _partidas(db, torneo.id)
    ya_jugaron = {frozenset((p.blancas_id, p.negras_id)) for p in previas if p.negras_id is not None}
    con_blancas: dict[int, int] = {}
    for p in previas:
        con_blancas[p.blancas_id] = con_blancas.get(p.blancas_id, 0) + 1
    parejas, descansa = emparejar(
        [{"id": uid, "puntos": j.puntos, "descansos": j.descansos, "blancas": con_blancas.get(uid, 0)} for uid, j in jugadores.items()], ya_jugaron
    )
    ronda = torneo.ronda_actual + 1
    creadas: list[TorneoPartidaORM] = []
    for blancas_id, negras_id in parejas:
        sala = servicio_sala.crear_sala(blancas_id, "blancas", torneo.control_tiempo_ms, tipo=servicio_sala.TIPO_TORNEO)
        servicio_sala.unirse(sala.codigo_sala, negras_id, registrar_presencia=False)
        creadas.append(TorneoPartidaORM(torneo_id=torneo.id, ronda=ronda, blancas_id=blancas_id, negras_id=negras_id, codigo_sala=sala.codigo_sala))
    if descansa is not None:
        descanso = TorneoPartidaORM(torneo_id=torneo.id, ronda=ronda, blancas_id=descansa, negras_id=None, resultado="1-0")
        jugadores[descansa].puntos += 1.0
        jugadores[descansa].descansos += 1
        creadas.append(descanso)
    db.add_all(creadas)
    torneo.ronda_actual, torneo.estado = ronda, "en_juego"
    db.commit()
    return creadas


def cerrar(db: Session, torneo: TorneoORM) -> None:
    """El docente cierra el torneo ahora: las partidas sin terminar quedan sin ganador."""
    sincronizar(db, torneo)
    if torneo.estado == "terminado":
        return
    jugadores = {j.usuario_id: j for j in _jugadores(db, torneo.id)}
    for partida in _partidas(db, torneo.id):
        if partida.resultado is None:
            if partida.codigo_sala:
                try:
                    servicio_sala.cerrar_sin_ganador(partida.codigo_sala)
                except KeyError:
                    pass
            _anotar(partida, "0-0", jugadores)
    torneo.estado = "terminado"
    db.commit()


# --- cómo se ve ------------------------------------------------------------------------------------------------------------------------


def clasificacion(db: Session, torneo: TorneoORM) -> list[dict]:
    """La tabla de posiciones: puntos, victorias y partidas jugadas de cada inscrito (nombre abreviado), de mejor a peor."""
    partidas = [p for p in _partidas(db, torneo.id) if p.resultado is not None]
    filas = []
    for jugador in _jugadores(db, torneo.id):
        usuario = db.get(UsuarioORM, jugador.usuario_id)
        if usuario is None:
            continue
        victorias = sum(1 for p in partidas if (p.blancas_id == usuario.id and p.resultado == "1-0") or (p.negras_id == usuario.id and p.resultado == "0-1"))
        jugadas = sum(1 for p in partidas if usuario.id in (p.blancas_id, p.negras_id) and p.negras_id is not None)
        filas.append({**persona(usuario), "puntos": jugador.puntos, "victorias": victorias, "partidas": jugadas, "descansos": jugador.descansos})
    filas.sort(key=lambda f: (-f["puntos"], -f["victorias"], f["nombre"].lower()))
    for fila in filas:
        fila["puesto"] = 1 + sum(1 for otra in filas if (otra["puntos"], otra["victorias"]) > (fila["puntos"], fila["victorias"]))
    return filas


def _persona_de(db: Session, usuario_id: int | None) -> dict | None:
    usuario = db.get(UsuarioORM, usuario_id) if usuario_id is not None else None
    return persona(usuario) if usuario is not None else None


def partida_pendiente(db: Session, torneo: TorneoORM, usuario_id: int) -> dict | None:
    """La partida de la ronda en juego que `usuario_id` todavía tiene que jugar: `{"codigo", "ronda", "rival"}`, o `None`."""
    for partida in _partidas(db, torneo.id, torneo.ronda_actual):
        if partida.resultado is None and partida.codigo_sala and usuario_id in (partida.blancas_id, partida.negras_id):
            rival_id = partida.negras_id if usuario_id == partida.blancas_id else partida.blancas_id
            return {"codigo": partida.codigo_sala, "ronda": partida.ronda, "rival": _persona_de(db, rival_id)}
    return None


def detalle(db: Session, torneo: TorneoORM, usuario_id: int | None = None) -> dict:
    """Todo lo de un torneo para mostrarlo: datos, tabla de posiciones, partidas de la ronda en juego y, si se indica `usuario_id`, si está inscrito y su partida pendiente."""
    sincronizar(db, torneo)
    db.refresh(torneo)
    ronda = [
        {
            "blancas": _persona_de(db, p.blancas_id),
            "negras": _persona_de(db, p.negras_id),
            "resultado": p.resultado,
            "codigo": p.codigo_sala if p.resultado is None else None,
            "descanso": p.negras_id is None,
        }
        for p in _partidas(db, torneo.id, torneo.ronda_actual)
    ] if torneo.ronda_actual else []
    return {
        "id": torneo.id,
        "curso_id": torneo.curso_id,
        "nombre": torneo.nombre,
        "control_tiempo_ms": torneo.control_tiempo_ms,
        "rondas": torneo.rondas,
        "ronda_actual": torneo.ronda_actual,
        "estado": torneo.estado,
        "inscritos": db.scalar(select(func.count()).select_from(TorneoJugadorORM).where(TorneoJugadorORM.torneo_id == torneo.id)) or 0,
        "inscrito": _inscripto(db, torneo.id, usuario_id) is not None if usuario_id is not None else False,
        "clasificacion": clasificacion(db, torneo),
        "partidas_de_la_ronda": ronda,
        "mi_partida": partida_pendiente(db, torneo, usuario_id) if usuario_id is not None and torneo.estado == "en_juego" else None,
    }


def para_el_jugador(db: Session, usuario: UsuarioORM) -> dict | None:
    """Lo que ve un alumno en sus avisos: el torneo en marcha de su curso (si lo hay), si está inscrito y su partida pendiente."""
    curso_id = db.scalar(select(InscripcionORM.curso_id).where(InscripcionORM.usuario_id == usuario.id))
    torneo = torneo_en_marcha(db, curso_id) if curso_id is not None else None
    if torneo is None:
        return None
    sincronizar(db, torneo)
    db.refresh(torneo)
    if torneo.estado == "terminado":
        return None
    return {
        "id": torneo.id,
        "nombre": torneo.nombre,
        "estado": torneo.estado,
        "ronda": torneo.ronda_actual,
        "rondas": torneo.rondas,
        "control_tiempo_ms": torneo.control_tiempo_ms,
        "inscrito": _inscripto(db, torneo.id, usuario.id) is not None,
        "mi_partida": partida_pendiente(db, torneo, usuario.id) if torneo.estado == "en_juego" else None,
    }
