"""Endpoints HTTP para partidas jugables contra la estrategia de jugada activa."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.esquemas.analisis_red_esquema import AnalisisRedResponse
from backend.esquemas.calibracion_esquema import CalibracionResponse
from backend.esquemas.partida_esquema import (
    DetectarJugadaRequest,
    JugadaDetectadaResponse,
    ActualizarPermisosPartidaRequest,
    AnalisisCompletoResponse,
    CrearPartidaRequest,
    EstadoPartidaResponse,
    JugadasLegalesResponse,
    MoverRequest,
    ResultadoMovimientoResponse,
    ResumenPartidaResponse,
    ActualizarRelojRequest,
    TiempoAgotadoRequest,
)
from backend.modelos.partida import Partida
from backend.rutas.ruta_auth import (
    get_current_facilitador,
    get_current_user,
    get_current_user_opcional,
    get_db,
)
from backend.servicios.auth import get_user_by_id, get_users_by_ids
from backend.servicios.curso.servicio_cursos import es_principal
from backend.servicios.calibracion import (
    calibracion_descartada,
    registrar_calibracion,
    respuesta_no_registrada,
)
from backend.servicios.partida.servicio_partida import (
    actualizar_permisos,
    analisis_completo,
    crear_partida,
    jugadas_legales_desde,
    limpiar_partidas_inactivas,
    listar_partidas,
    mover,
    detectar_jugada_en_tablero,
    mover_desde_foto,
    obtener_partida,
    obtener_partida_en_demostracion,
    partida_en_curso_de,
    actualizar_reloj,
    registrar_presencia,
    terminar_por_tiempo,
)
from backend.servicios.aprendizaje.analisis_red import analizar_partida_con_red
from backend.servicios.retroalimentacion.servicio_retroalimentacion import RANGO_POR_DEFECTO

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/partida", tags=["partida"])


def _a_estado(partida: Partida, usuario_nombre: str | None = None) -> EstadoPartidaResponse:
    return EstadoPartidaResponse(
        id=partida.id,
        tipo=partida.tipo,
        tipo_oponente=partida.tipo_oponente,
        nivel=partida.nivel,
        creada_en=partida.creada_en,
        fen=partida.fen,
        fen_inicial=partida.fen_inicial,
        terminada=partida.terminada,
        resultado=partida.resultado,
        jugadas=partida.jugadas_san,
        permite_simulacion_3d=partida.permite_simulacion_3d,
        permite_camara=partida.permite_camara,
        es_demostracion=partida.es_demostracion,
        usa_brazo=partida.usa_brazo,
        usuario_id=partida.usuario_id,
        usuario_nombre=usuario_nombre,
        estado=partida.estado,
        iniciada_en=partida.iniciada_en,
        actualizada_en=partida.actualizada_en,
        jugadas_jugador=partida.jugadas_jugador,
        control_tiempo_ms=partida.control_tiempo_ms,
        tiempo_blancas_ms=partida.restante_blancas_ms if partida.control_tiempo_ms else None,
        tiempo_negras_ms=partida.restante_negras_ms if partida.control_tiempo_ms else None,
        tiempos_jugadas_ms=partida.tiempos_jugadas_ms,
    )


def _a_resumen(partida: Partida, usuario_nombre: str | None = None) -> ResumenPartidaResponse:
    return ResumenPartidaResponse(
        id=partida.id,
        tipo=partida.tipo,
        tipo_oponente=partida.tipo_oponente,
        nivel=partida.nivel,
        creada_en=partida.creada_en,
        fen=partida.fen,
        terminada=partida.terminada,
        resultado=partida.resultado,
        cantidad_jugadas=len(partida.jugadas_san),
        es_demostracion=partida.es_demostracion,
        usuario_id=partida.usuario_id,
        usuario_nombre=usuario_nombre,
        estado=partida.estado,
        jugadas_jugador=partida.jugadas_jugador,
    )


def _resolver_nombre_dueno(db: Session, usuario_id: int | None) -> str | None:
    """Busca el nombre del dueño de una partida, o `None` si no tiene dueño
    o el usuario ya no existe — mismo criterio en todos los endpoints que
    exponen `usuario_nombre` (`estado`, `demostracion_activa`)."""
    if usuario_id is None:
        return None
    usuario = get_user_by_id(db, usuario_id)
    return usuario.nombre if usuario else None


@router.post("", response_model=EstadoPartidaResponse)
def crear(
    request: CrearPartidaRequest,
    usuario_id: int = Depends(get_current_user),
) -> EstadoPartidaResponse:
    """Requiere `Authorization: Bearer <token>` (HU10) — la partida queda asociada
    al usuario del token, para poder filtrarla después en `/usuario/estadisticas`
    y `/usuario/historial-partidas`."""
    try:
        partida = crear_partida(
            nivel=request.nivel,
            tipo_oponente=request.tipo_oponente,
            fen_inicial=request.fen_inicial,
            usuario_id=usuario_id,
            control_tiempo_ms=request.control_tiempo_ms,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _a_estado(partida)


@router.get("", response_model=list[ResumenPartidaResponse])
def listar(
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ResumenPartidaResponse]:
    """Registro de partidas jugadas mientras este proceso sigue corriendo.

    No sobrevive un reinicio del backend (`RepositorioPartidasEnMemoria`, ver
    sección 4.3 y 7 de PLAN_IMPLEMENTACION_COMPLETO.md) — pero es un registro
    real, no datos de ejemplo.

    Requiere `Authorization: Bearer <token>`. El facilitador ve todas las
    partidas (Registro de Partidas, Monitoreo); cualquier otro usuario solo ve
    las suyas. Antes era público y exponía el ID y el nombre de cada
    estudiante, lo que permitía tomar cualquier partida.

    Resuelve `usuario_nombre` con una sola consulta para todos los dueños
    distintos de la lista (`get_users_by_ids`), no una por partida — no hay
    paginación todavía en este endpoint, así que evitar el N+1 acá importa.

    Excluye las partidas sin ninguna jugada del jugador (`jugadas_jugador ==
    0`): son las que la Sala de Control crea sola al abrir la pantalla y
    nadie llegó a jugar — no aportan nada al registro y antes lo inflaban
    (ver `ciclo_vida.py`, que además las va limpiando de la base).
    """
    usuario = get_user_by_id(db, usuario_id)
    es_facilitador = usuario is not None and usuario.rol == "facilitador"
    partidas = [
        partida
        for partida in listar_partidas()
        if partida.jugadas_jugador >= 1 and (es_facilitador or partida.usuario_id == usuario_id)
    ]
    ids_duenos = {partida.usuario_id for partida in partidas if partida.usuario_id is not None}
    usuarios_por_id = get_users_by_ids(db, ids_duenos)
    return [
        _a_resumen(
            partida,
            usuario_nombre=(
                usuarios_por_id[partida.usuario_id].nombre
                if partida.usuario_id in usuarios_por_id
                else None
            ),
        )
        for partida in partidas
    ]


@router.get("/demostracion-activa", response_model=EstadoPartidaResponse | None)
def demostracion_activa(
    _: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse | None:
    """Partida que el facilitador está transmitiendo en vivo a la clase ahora
    mismo, si hay alguna — cualquier jugador autenticado la puede consultar
    (no hace falta ser facilitador) para saber si hay una demostración a la
    que sumarse en modo solo lectura, sin que el facilitador tenga que
    compartir el id de la partida a mano.

    Declarada antes que `GET /partida/{partida_id}` a propósito: FastAPI
    prueba las rutas en el orden en que se registran, y si `{partida_id}`
    fuera la primera en calzar con un solo segmento después de `/partida/`,
    se comería este path fijo tratando "demostracion-activa" como si fuera
    un id de partida.

    Devuelve `200` con el cuerpo en `null` cuando no hay ninguna partida en
    demostración — mismo criterio que el resto del proyecto usa para "no hay
    dato todavía" (ej. `GET /usuario/historial-partidas`, que devuelve una
    lista vacía en vez de 404): un `204` sin cuerpo obligaría al frontend a
    ramificar por status code en vez de simplemente mirar el JSON, y esto no
    es realmente un error como para ser un 404.
    """
    partida = obtener_partida_en_demostracion()
    if partida is None:
        return None
    return _a_estado(partida, usuario_nombre=_resolver_nombre_dueno(db, partida.usuario_id))


@router.get("/en-curso", response_model=EstadoPartidaResponse | None)
def en_curso(
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse | None:
    """Partida `en_curso` que el usuario autenticado puede retomar, o `null`
    si no tiene ninguna (HU "retomar" — Sala de Control ofrece "Retomar" en
    vez de crear una partida nueva cuando esto no da `null`).

    Antes de responder, barre las partidas `en_curso` inactivas de todo el
    sistema (`limpiar_partidas_inactivas`) — tolera que ese barrido falle sin
    romper la respuesta de este endpoint, igual que en el arranque del
    backend (`main.py`).

    Declarada antes que `GET /partida/{partida_id}` a propósito, mismo
    motivo que `GET /partida/demostracion-activa`: si no, FastAPI trataría
    `"en-curso"` como si fuera un id de partida.
    """
    try:
        limpiar_partidas_inactivas()
    except Exception:
        logger.exception("No se pudo limpiar partidas inactivas antes de GET /partida/en-curso")
    partida = partida_en_curso_de(usuario_id)
    if partida is None:
        return None
    return _a_estado(partida, usuario_nombre=_resolver_nombre_dueno(db, partida.usuario_id))


@router.get("/{partida_id}", response_model=EstadoPartidaResponse)
def estado(
    partida_id: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse:
    """Requiere `Authorization: Bearer <token>` (HU10) — 403 si la partida es de otro
    usuario, salvo que quien pregunta sea facilitador: la Sala de Control necesita poder
    abrir la partida de cualquier jugador para supervisarla y tocar sus permisos
    (`PATCH /partida/{id}/permisos`, que ya no chequea dueño, solo rol) — ver
    `RegistroPartidas` en el frontend, que lista todas las partidas sin filtrar por
    usuario y depende de este endpoint para poder abrir cualquiera de ellas.

    Tercera excepción al dueño/facilitador: si `partida.es_demostracion` es
    `True`, cualquier usuario autenticado puede leerla — es la partida que el
    facilitador eligió transmitir en vivo a toda la clase, de solo lectura
    para quien no es su dueño (este chequeo no toca `POST .../mover`, que
    sigue sin autorización propia — no le da a nadie más permiso de mover
    fichas)."""
    try:
        partida = obtener_partida(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if partida.usuario_id is not None and partida.usuario_id != usuario_id and not partida.es_demostracion:
        usuario = get_user_by_id(db, usuario_id)
        if not usuario or usuario.rol != "facilitador":
            raise HTTPException(status_code=403, detail="La partida pertenece a otro usuario")
    usuario_nombre = _resolver_nombre_dueno(db, partida.usuario_id)
    return _a_estado(partida, usuario_nombre=usuario_nombre)


@router.patch("/{partida_id}/permisos", response_model=EstadoPartidaResponse)
def actualizar_permisos_partida(
    partida_id: str,
    request: ActualizarPermisosPartidaRequest,
    usuario_id_facilitador: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse:
    """Prende o apaga, para esta partida, la simulación 3D, la cámara del
    tablero físico, la demostración en vivo (`es_demostracion`) y/o la
    ejecución de la jugada de respuesta en el brazo (`usa_brazo`) — las
    primeras dos son funciones educativas apagadas para el jugador por
    defecto; la tercera transmite la partida a toda la clase; la cuarta
    activa HU9 (ver `servicio_brazo.ejecutar_respuesta_en_brazo`). Requiere
    rol facilitador (403 si no lo es).

    `es_demostracion=true` además exige que la partida sea del propio
    facilitador que hace el PATCH (400 si no — ver
    `servicio_partida.actualizar_permisos`); los otros dos campos siguen sin
    ese chequeo, aplican sobre cualquier partida que el facilitador esté
    supervisando.

    El E-STOP del brazo no tiene toggle acá: es un control de seguridad,
    queda hardcodeado solo-facilitador en el frontend, no una función
    educativa que se pueda prender/apagar por partida.

    `usa_brazo` mueve el brazo físico: solo lo puede tocar la plataforma (un facilitador principal); un docente o el
    administrador de una institución reciben 403 si lo mandan.
    """
    campos = request.model_dump(exclude_unset=True)
    if "usa_brazo" in campos and not es_principal(get_user_by_id(db, usuario_id_facilitador)):
        raise HTTPException(status_code=403, detail="Mover el brazo es solo para los administradores de la plataforma")
    try:
        partida = actualizar_permisos(partida_id, campos, usuario_id_facilitador)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _a_estado(partida)


@router.get("/{partida_id}/jugadas-legales", response_model=JugadasLegalesResponse)
def jugadas_legales(partida_id: str, casilla: str) -> JugadasLegalesResponse:
    try:
        casillas = jugadas_legales_desde(partida_id, casilla)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return JugadasLegalesResponse(casillas=casillas)


def _exigir_dueno(partida_id: str, usuario_id: int) -> None:
    """404 si la partida no existe; 403 si no es del usuario del token.

    Una partida sin dueño también da 403: no hay nadie autorizado a jugarla, y
    antes cualquiera con el ID podía mover la partida de otro.
    """
    try:
        partida = obtener_partida(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if partida.usuario_id != usuario_id:
        raise HTTPException(status_code=403, detail="Solo el dueño de la partida puede moverla")
    _rechazar_si_es_sala(partida)


def _rechazar_si_es_sala(partida) -> None:
    """400 si la partida es una sala humano vs humano: se maneja solo por `/sala/{codigo}/...`.

    Evita que el creador mueva, cierre por tiempo o analice por `/partida/{id}` la partida de ambos.
    """
    if partida.tipo_oponente == "humano":
        raise HTTPException(
            status_code=400,
            detail=f"Esta partida es una sala: usá los endpoints de /sala/{partida.codigo_sala}",
        )


@router.post("/{partida_id}/mover", response_model=ResultadoMovimientoResponse)
def mover_partida(
    partida_id: str,
    request: MoverRequest,
    usuario_id: int = Depends(get_current_user),
) -> ResultadoMovimientoResponse:
    """Requiere `Authorization: Bearer <token>` y que la partida sea del usuario
    del token (403 si es de otro, o no tiene dueño)."""
    _exigir_dueno(partida_id, usuario_id)
    try:
        resultado = mover(
            partida_id,
            request.jugada,
            tiempo_jugada_ms=request.tiempo_jugada_ms,
            reloj_blancas_ms=request.reloj_blancas_ms,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ResultadoMovimientoResponse(**resultado)


@router.put("/{partida_id}/reloj", response_model=EstadoPartidaResponse)
def actualizar_reloj_partida(
    partida_id: str,
    request: ActualizarRelojRequest,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse:
    """Guarda lo que le queda a cada lado en el reloj (la pantalla lo llama cada pocos segundos y al
    cerrar la pestaña), o cambia el control de tiempo antes de la primera jugada.

    Requiere ser el dueño de la partida (403 si no). 400 si ya terminó, si no tiene reloj o si se quiere
    cambiar el control con la partida empezada."""
    _exigir_dueno(partida_id, usuario_id)
    try:
        partida = actualizar_reloj(
            partida_id,
            blancas_ms=request.blancas_ms,
            negras_ms=request.negras_ms,
            control_tiempo_ms=request.control_tiempo_ms,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _a_estado(partida, usuario_nombre=_resolver_nombre_dueno(db, partida.usuario_id))


@router.post("/{partida_id}/presencia", response_model=EstadoPartidaResponse)
def presencia_partida(
    partida_id: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse:
    """Señal de vida: la pantalla avisa que el jugador sigue en esta partida con reloj (cada pocos segundos, mientras
    la partida está a la vista).

    Una partida con reloj no se puede dejar: si pasan más de 20 segundos sin esta señal se da por perdida
    (`estado == "abandonada"`, derrota del jugador) o se borra si casi no se jugó. La respuesta es el estado de la
    partida, así la pantalla se entera enseguida si ya se cerró. Sin reloj, o ya terminada, no hace nada.

    Requiere ser el dueño de la partida (403 si no); 404 si no existe o se borró por abandono."""
    _exigir_dueno(partida_id, usuario_id)
    try:
        partida = registrar_presencia(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _a_estado(partida, usuario_nombre=_resolver_nombre_dueno(db, partida.usuario_id))


@router.post("/{partida_id}/tiempo-agotado", response_model=EstadoPartidaResponse)
def tiempo_agotado(
    partida_id: str,
    request: TiempoAgotadoRequest,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EstadoPartidaResponse:
    """La pantalla informa que a un lado se le acabó el reloj: la partida termina y gana el otro.

    Requiere ser el dueño de la partida (403 si no). 400 si ya terminó, si todavía no hubo jugadas
    o si `lado` es inválido. Equivale a abandonar, solo que queda registrada como derrota."""
    _exigir_dueno(partida_id, usuario_id)
    try:
        partida = terminar_por_tiempo(partida_id, request.lado)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _a_estado(partida, usuario_nombre=_resolver_nombre_dueno(db, partida.usuario_id))


@router.post("/{partida_id}/mover-desde-foto", response_model=ResultadoMovimientoResponse)
def mover_partida_desde_foto(
    partida_id: str,
    cuerpo: DetectarJugadaRequest | None = None,
    usuario_id: int = Depends(get_current_user),
) -> ResultadoMovimientoResponse:
    """Detecta la jugada hecha en el tablero físico (cámara fija) y la aplica (RF11).

    Exige token y que la partida sea del usuario del token: captura la cámara
    fija y aplica una jugada real.
    """
    _exigir_dueno(partida_id, usuario_id)
    try:
        resultado = mover_desde_foto(partida_id, cuerpo.camaras if cuerpo else None)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, FileNotFoundError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ResultadoMovimientoResponse(**resultado)


@router.post("/{partida_id}/detectar-jugada", response_model=JugadaDetectadaResponse)
def detectar_jugada_partida(
    partida_id: str,
    cuerpo: DetectarJugadaRequest | None = None,
    usuario_id: int = Depends(get_current_user),
) -> JugadaDetectadaResponse:
    """Qué jugada se ve en el tablero físico y qué tan segura es, SIN aplicarla.

    Si `estado` es `dudosa`, `alternativas` trae las más probables para que el jugador confirme una (con `POST /partida/{id}/mover`).
    `camaras` elige con cuáles leer (`principal`, `secundaria` o ambas).
    """
    _exigir_dueno(partida_id, usuario_id)
    try:
        resultado = detectar_jugada_en_tablero(partida_id, cuerpo.camaras if cuerpo else None)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (RuntimeError, FileNotFoundError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return JugadaDetectadaResponse(**resultado)


@router.get("/{partida_id}/analisis-red", response_model=AnalisisRedResponse)
def analisis_red_partida(
    partida_id: str,
    _facilitador: int = Depends(get_current_facilitador),
) -> AnalisisRedResponse:
    """Compara, jugada por jugada, lo que elegiría la red con lo que se jugó (HU6 ampliada).

    Solo facilitador: puede mirar cualquier partida (de jugadores o suya). Recorre la partida
    desde su posición inicial y pregunta a la red en cada una. La red decide sola; esto solo mide.
    404 si la partida no existe; 503 si falta el checkpoint de la red.
    """
    try:
        partida = obtener_partida(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _rechazar_si_es_sala(partida)
    try:
        analisis = analizar_partida_con_red(
            fen_inicial=partida.fen_inicial,
            jugadas_san=partida.jugadas_san,
            tipo_oponente=partida.tipo_oponente,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return AnalisisRedResponse(partida_id=partida_id, **analisis)


@router.post("/{partida_id}/calibrar", response_model=CalibracionResponse)
def calibrar_partida(
    partida_id: str,
    usuario_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CalibracionResponse:
    """Calibra el nivel del dueño de la partida con lo que jugó en ella (RF20).

    Pensado para llamarse al terminar una partida, sin depender de que alguien
    abra el análisis. Requiere `Authorization: Bearer <token>`; 404 si la
    partida no existe y 403 si es de otro usuario, salvo que quien pide sea
    facilitador (calibra al dueño real, igual que en `analisis-completo`).

    Si la partida no terminó responde 200 con `registrada=false` y
    `motivo="no_terminada"`, sin analizar nada; si se dio por perdida por
    abandono (`estado == "abandonada"`) responde `motivo="abandonada"`: no mide
    el nivel ni cuenta para el diagnóstico. Si ya se sabe que no se puede
    o no hace falta calibrar (`sin_dueno`, `dueno_no_jugador`, `ya_registrada`)
    también responde eso sin correr Stockfish. **Partidas `humano` (salas) no
    calibran** (`motivo="no_aplica"`). Si no, analiza la partida y delega en
    `registrar_calibracion`, que es idempotente por partida: llamarlo dos veces
    no duplica la calibración ni cambia el nivel de nuevo.
    """
    try:
        partida = obtener_partida(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    if partida.usuario_id is not None and partida.usuario_id != usuario_id:
        solicitante = get_user_by_id(db, usuario_id)
        if not solicitante or solicitante.rol != "facilitador":
            raise HTTPException(status_code=403, detail="La partida pertenece a otro usuario")

    # Excluir partidas humano vs humano (salas) de la calibración
    if partida.tipo_oponente == "humano":
        return CalibracionResponse(**respuesta_no_registrada(db, None, "no_aplica"))

    dueno = get_user_by_id(db, partida.usuario_id) if partida.usuario_id is not None else None
    if partida.estado == "abandonada":
        # Una partida que se dejó (dada por perdida) no mide el nivel ni cuenta para el diagnóstico.
        return CalibracionResponse(**respuesta_no_registrada(db, dueno, "abandonada"))
    if not partida.terminada:
        return CalibracionResponse(**respuesta_no_registrada(db, dueno, "no_terminada"))
    descartada = calibracion_descartada(db, dueno, partida_id)
    if descartada is not None:
        return CalibracionResponse(**descartada)

    resultado = analisis_completo(partida_id, rango=dueno.rango_estimado or RANGO_POR_DEFECTO)
    return CalibracionResponse(**registrar_calibracion(db, dueno, partida_id, resultado["resumen"]))


@router.get("/{partida_id}/analisis-completo", response_model=AnalisisCompletoResponse)
def analisis_completo_partida(
    partida_id: str,
    usuario_id: int | None = Depends(get_current_user_opcional),
    db: Session = Depends(get_db),
) -> AnalisisCompletoResponse:
    """Analiza con Stockfish cada jugada de la partida, para la vista de aprendizaje (HU5/HU6).

    RF20 (texto): si la request trae `Authorization: Bearer <token>` válido,
    adapta el texto de `explicacion` y `consejo_tutor` al `rango_estimado` del
    usuario autenticado. Sin token, o si el usuario todavía no tiene rango
    diagnosticado (`rango_estimado` es `None`), usa `"Intermedio"` — no
    requiere login, para no romper el uso sin sesión de este endpoint.

    RF20 (calibración automática): si la partida analizada ya está `terminada`
    y tiene un dueño jugador, de paso registra su calibración con
    `registrar_calibracion` — la misma función que usa
    `POST /partida/{id}/calibrar`. Cada partida calibra una sola vez: abrir
    de nuevo el análisis no cambia el nivel. El resultado va en `calibracion`
    (con `registrada=false` y un `motivo` cuando no se registró). El dueño de la
    partida no necesita ser quien pide el análisis (ej. un facilitador
    revisándola desde la Sala de Control también calibra al dueño real).

    **Partidas `humano` (salas)**: 400, sin ejecutar Stockfish ni persistir nada; el análisis
    de una sala se pide en `GET /sala/{codigo}/analisis`.
    """
    rango = RANGO_POR_DEFECTO
    if usuario_id is not None:
        usuario = get_user_by_id(db, usuario_id)
        if usuario and usuario.rango_estimado:
            rango = usuario.rango_estimado
    try:
        partida = obtener_partida(partida_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if partida.tipo_oponente == "humano":
        raise HTTPException(
            status_code=400,
            detail=f"El análisis de una sala se pide en /sala/{partida.codigo_sala}/analisis",
        )
    try:
        resultado = analisis_completo(partida_id, rango=rango)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    dueno = get_user_by_id(db, partida.usuario_id) if partida.usuario_id is not None else None

    if partida.estado == "abandonada":
        # Dada por perdida por abandono: se puede revisar, pero no mide el nivel ni cuenta para el diagnóstico.
        calibracion = respuesta_no_registrada(db, dueno, "abandonada")
    elif partida.terminada:
        calibracion = registrar_calibracion(db, dueno, partida_id, resultado["resumen"])
    else:
        calibracion = respuesta_no_registrada(db, dueno, "no_terminada")

    return AnalisisCompletoResponse(**resultado, calibracion=CalibracionResponse(**calibracion))
