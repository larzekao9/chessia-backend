"""Endpoints de salas (humano vs humano) — prefijo `/sala`.

Todos exigen `Authorization: Bearer` y `get_current_user` (devuelve `int` = user_id).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.esquemas.sala import (
    AnalisisSalaResponse,
    CrearSalaRequest,
    JugadasLegalesSalaResponse,
    MoverSalaRequest,
    SalaActivaResponse,
    SalaResponse,
    UnirseSalaResponse,
)
from backend.rutas.ruta_auth import get_current_user, get_db
from backend.servicios.curso.servicio_reporte import nombre_publico
from backend.servicios.social.elo import cambios_de_sala, es_provisional
from backend.servicios.sala import presencia_sala
from backend.servicios.sala.analisis_partida_sala import motivo_fin
from backend.servicios.sala.servicio_sala import (
    JUGADAS_MINIMAS_PARA_OFRECER_TABLAS,
    oferta_de_tablas,
    ofrecer_o_aceptar_tablas,
    rechazar_tablas,
    reloj_corre,
    restantes_ms,
)
from backend.servicios.sala import (
    analisis_sala,
    abandonar,
    cancelar_sala,
    crear_sala,
    jugadas_legales_sala,
    limpiar_salas_inactivas,
    mover_humano,
    obtener_estado,
    registrar_presencia,
    sala_activa_de,
    unirse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sala", tags=["sala"])


def _limpiar_tolerante() -> None:
    """Barre las salas vencidas; si falla, lo loguea y deja seguir la request."""
    try:
        limpiar_salas_inactivas()
    except Exception:
        logger.exception("Falló la limpieza de salas inactivas")


def _exigir_que_pueda_jugar_con_otros(db: Session, usuario_id: int) -> None:
    """403 si el jugador no puede jugar con otras personas ahora (su docente pausó los retos o le falta la autorización de su familia). No toca a los docentes."""
    from backend.modelos.tablas_orm import UsuarioORM
    from backend.servicios.social import reglas
    from backend.servicios.social.amigos import ErrorSocial

    usuario = db.get(UsuarioORM, usuario_id)
    if usuario is None:
        return
    try:
        reglas.exigir(db, usuario)
    except ErrorSocial as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error


def _jugador_de_sala(db: Session, partida, usuario_id: int | None) -> dict | None:
    """Los datos de quien ocupa un asiento: nombre (completo y público), avatar, nivel y, con reloj en curso, hace cuánto no da señal de vida."""
    from backend.modelos.tablas_orm import UsuarioORM

    usuario = db.get(UsuarioORM, usuario_id) if usuario_id is not None else None
    if usuario is None:
        return None
    sin_senal = None
    if partida.estado == "en_curso" and partida.control_tiempo_ms:
        sin_senal = presencia_sala.segundos_sin_senal(partida.id, usuario.id)
    return {
        "id": usuario.id,
        "nombre": usuario.nombre,
        "avatar_url": usuario.avatar_url,
        "nombre_publico": nombre_publico(usuario.nombre),
        "nivel": usuario.nivel_estimado,
        "rango": usuario.rango_estimado,
        "elo": usuario.elo,
        "elo_provisional": es_provisional(usuario.partidas_elo),
        "ausente_s": int(sin_senal) if sin_senal is not None else None,
    }


def _partida_a_sala_response(db: Session, partida, usuario_id: int) -> SalaResponse:
    """Convierte una Partida de dominio a SalaResponse con el rol del usuario."""
    from backend.servicios.sala.servicio_sala import _rol_desde_asiento, _calcular_version

    rol = _rol_desde_asiento(partida, usuario_id)
    turno = "blancas" if partida.tablero.turn == 1 else "negras"  # chess.WHITE = 1

    # Jugadores
    blancas = _jugador_de_sala(db, partida, partida.usuario_blancas_id)
    negras = _jugador_de_sala(db, partida, partida.usuario_negras_id)
    tiempo_blancas, tiempo_negras = restantes_ms(partida) if partida.control_tiempo_ms else (None, None)

    return SalaResponse(
        codigo=partida.codigo_sala or "",
        partida_id=partida.id,
        estado=partida.estado,
        fen=partida.fen,
        turno=turno,
        jugadas=partida.jugadas_san,
        ultima_jugada_uci=partida.tablero.move_stack[-1].uci() if partida.tablero.move_stack else None,
        terminada=partida.terminada or partida.estado in ("terminada", "abandonada"),
        resultado=partida.resultado,
        motivo_fin=motivo_fin(partida),
        blancas=blancas,
        negras=negras,
        rol=rol,
        version=_calcular_version(partida),
        control_tiempo_ms=partida.control_tiempo_ms,
        tiempo_blancas_ms=tiempo_blancas,
        tiempo_negras_ms=tiempo_negras,
        reloj_corre=reloj_corre(partida),
        tablas_ofrecidas_por=oferta_de_tablas(partida),
        medias_jugadas_para_tablas=JUGADAS_MINIMAS_PARA_OFRECER_TABLAS,
        puntuada=partida.puntuada,
        elo=cambios_de_sala(db, partida.codigo_sala) if partida.codigo_sala and (partida.terminada or partida.estado in ("terminada", "abandonada")) else None,
    )


@router.get("/activa", response_model=SalaActivaResponse | None)
def sala_activa(
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """La sala `esperando`/`en_curso` más reciente del usuario, o `null` si no hay.

    Declarada ANTES de `/{codigo}` para no ser capturada por esa ruta.
    Limpia salas inactivas antes de buscar (igual que `GET /partida/en-curso`).
    """
    _limpiar_tolerante()
    partida = sala_activa_de(usuario_id)
    if not partida:
        return None
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("", response_model=SalaResponse, status_code=status.HTTP_201_CREATED)
def crear_sala_endpoint(
    datos: CrearSalaRequest,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea una sala `esperando` con código único.

    Body: `{color: "blancas"|"negras"|"aleatorio", control_tiempo_ms: 0|180000|300000|600000|900000}` (default "aleatorio" y sin reloj).
    Devuelve SalaResponse con `rol="blancas"|"negras"` según el color elegido. 400 si el control de tiempo no es uno de los permitidos.
    """
    _exigir_que_pueda_jugar_con_otros(db, usuario_id)
    try:
        partida = crear_sala(usuario_id, datos.color, datos.control_tiempo_ms, puntuada=datos.puntuada)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("/{codigo}/unirse", response_model=UnirseSalaResponse)
def unirse_sala(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Une al usuario a la sala.

    - Si hay asiento libre y quien llama no es el creador → ocupa el asiento y la sala pasa a `en_curso`.
    - Si ya está sentado → idempotente.
    - Si la sala está llena → devuelve `rol="espectador"` (no es error).
    - 404 si el código no existe.
    """
    codigo = codigo.strip().upper()
    _exigir_que_pueda_jugar_con_otros(db, usuario_id)
    try:
        partida = unirse(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")

    from backend.servicios.sala.servicio_sala import _rol_desde_asiento
    rol = _rol_desde_asiento(partida, usuario_id)
    return UnirseSalaResponse(codigo=partida.codigo_sala, partida_id=partida.id, rol=rol)


@router.get("/{codigo}", response_model=SalaResponse)
def obtener_sala(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Estado actual de la sala (para polling). Incluye `rol` de quien consulta."""
    codigo = codigo.strip().upper()
    _limpiar_tolerante()
    try:
        partida = obtener_estado(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("/{codigo}/presencia", response_model=SalaResponse)
def presencia_sala_endpoint(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Señal de vida de quien está sentado en la sala. `GET /sala/{codigo}` y mover ya cuentan como señal; esto es para una pantalla que
    consulta poco. En una sala con reloj, más de 20 segundos sin señal hacen perder la partida. Devuelve el estado actual."""
    codigo = codigo.strip().upper()
    try:
        partida = registrar_presencia(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("/{codigo}/tablas", response_model=SalaResponse)
def ofrecer_tablas(codigo: str, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ofrece tablas al rival o, si él ya ofreció, las acepta (la partida termina empatada). La oferta caduca apenas se juega una jugada, y después de ofrecer hay
    que esperar 4 jugadas para volver a hacerlo. No se puede ofrecer antes de que cada uno lleve 10 jugadas. 403 si es espectador; 400 si la partida no está
    en curso, ya ofreció o es muy pronto."""
    codigo = codigo.strip().upper()
    try:
        partida = ofrecer_o_aceptar_tablas(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("/{codigo}/tablas/rechazar", response_model=SalaResponse)
def rechazar_oferta_de_tablas(codigo: str, usuario_id: int = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rechaza la oferta de tablas del rival; la partida sigue. 400 si no hay oferta que rechazar."""
    codigo = codigo.strip().upper()
    try:
        partida = rechazar_tablas(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _partida_a_sala_response(db, partida, usuario_id)


@router.get("/{codigo}/jugadas-legales", response_model=JugadasLegalesSalaResponse)
def jugadas_legales_sala_endpoint(
    codigo: str,
    casilla: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Casillas destino legales para la pieza en `casilla`."""
    codigo = codigo.strip().upper()
    try:
        casillas = jugadas_legales_sala(codigo, casilla)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JugadasLegalesSalaResponse(casillas=casillas)


@router.post("/{codigo}/mover", response_model=SalaResponse)
def mover_en_sala(
    codigo: str,
    datos: MoverSalaRequest,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aplica la jugada del humano en la sala.

    - Solo el jugador al que le toca.
    - 403 si espectador intenta mover o mueve fuera de turno ("No es tu turno").
    - 400 jugada ilegal / sala `esperando` / partida ya terminada.
    """
    codigo = codigo.strip().upper()
    try:
        partida = mover_humano(codigo, usuario_id, datos.jugada)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _partida_a_sala_response(db, partida, usuario_id)


@router.post("/{codigo}/abandonar", response_model=SalaResponse)
def abandonar_sala(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Abandona la sala: gana el otro jugador.

    - Solo un jugador de la sala (no espectador).
    - 403 si espectador.
    - 400 si ya terminó.
    """
    codigo = codigo.strip().upper()
    try:
        partida = abandonar(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _partida_a_sala_response(db, partida, usuario_id)


@router.delete("/{codigo}", status_code=status.HTTP_204_NO_CONTENT)
def cancelar_sala_endpoint(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
) -> Response:
    """Cancela (elimina) una sala `esperando`: botón "Cancelar sala" de Inicio.

    - 404 código inexistente; 403 si quien llama no es el creador;
      400 si la sala ya no está `esperando` (ya tiene rival o terminó).
    """
    codigo = codigo.strip().upper()
    try:
        cancelar_sala(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{codigo}/analisis", response_model=AnalisisSalaResponse)
def analisis_sala_endpoint(
    codigo: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Análisis general con Stockfish de los dos bandos (solo si la sala terminó).

    Contrato de PLAN_MULTIJUGADOR.md §6.3. 404 código inexistente; 400 si no terminó.
    No persiste evaluaciones: no afecta estadísticas ni calibración de nadie.
    """
    from backend.modelos.tablas_orm import UsuarioORM

    codigo = codigo.strip().upper()
    usuario = db.get(UsuarioORM, usuario_id)
    rango = (usuario.rango_estimado if usuario else None) or "Intermedio"
    try:
        data = analisis_sala(codigo, rango=rango)
        partida = obtener_estado(codigo, usuario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="No existe esa sala")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for clave, asiento in (("blancas", partida.usuario_blancas_id), ("negras", partida.usuario_negras_id)):
        jugador = db.get(UsuarioORM, asiento) if asiento is not None else None
        data[clave]["nombre"] = jugador.nombre if jugador else None
    data["codigo"] = codigo
    return data
