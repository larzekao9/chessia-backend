"""Cursos: el facilitador arma sus grupos y el jugador entra con un código.

Las rutas de `/cursos/mio...`, `/cursos/unirse` y `/cursos/salir` son del jugador; las demás, del facilitador
(un facilitador solo ve y toca sus propios cursos).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.curso_esquema import (
    ActualizarDatosDeFamiliaRequest,
    AlumnoReporteResponse,
    AvisoDeCursoRequest,
    AvisoDeCursoResponse,
    ClaveRestablecidaResponse,
    CrearAlumnosRequest,
    CrearAlumnosResponse,
    CuentaCreadaResponse,
    TareaDeCursoRequest,
    TareaDelAlumnoResponse,
    TareaDelDocenteResponse,
    CrearCursoRequest,
    CursoDelJugadorResponse,
    CursoResponse,
    EditarCursoRequest,
    EstudianteDeCursoResponse,
    RankingResponse,
    ReporteCursoResponse,
    UnirseCursoRequest,
)
from backend.modelos.tablas_orm import CursoORM
from backend.rutas.ruta_auth import _cliente_de, _exigir_intentos_disponibles, get_current_facilitador, get_current_user, get_db
from backend.servicios.auth import get_user_by_id
from backend.servicios.auth.servicio_invitaciones import registrar_intento_fallido
from backend.servicios.curso import servicio_cursos as cursos
from backend.servicios.curso import servicio_aula as aula
from backend.servicios.curso import servicio_cuentas as cuentas_de_alumnos
from backend.servicios.curso import servicio_reporte as reportes
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.curso.servicio_cursos import CursoCompleto, ErrorCurso
from backend.servicios.usuario import servicio_autorizacion as autorizacion

router = APIRouter(prefix="/cursos", tags=["cursos"])


def _respuesta_curso(db: Session, curso: CursoORM, estudiantes: int) -> CursoResponse:
    dueno = cursos.facilitador_de_curso(db, curso)
    return CursoResponse(
        id=curso.id,
        nombre=curso.nombre,
        codigo=cursos.formatear_codigo(curso.codigo),
        acepta_ingresos=curso.acepta_ingresos,
        cupo=curso.cupo,
        preset_ensenanza=curso.preset_ensenanza,
        preset_efectivo=cursos.preset_de_curso(db, curso),
        estudiantes=estudiantes,
        creado_en=fecha_a_iso(curso.creado_en),
        facilitador_id=curso.facilitador_id,
        facilitador_nombre=dueno.nombre if dueno else "",
        permite_retos=curso.permite_retos,
        autorizacion_familias=curso.autorizacion_familias,
        autorizacion_familias_en=fecha_a_iso(curso.autorizacion_familias_en) if curso.autorizacion_familias_en else None,
    )


def _es_principal(db: Session, facilitador_id: int) -> bool:
    return cursos.es_principal(get_user_by_id(db, facilitador_id))


def _del_facilitador(db: Session, curso_id: int, facilitador_id: int) -> CursoORM:
    """El curso si es del facilitador; un facilitador principal puede con el de cualquiera. 404 si no."""
    try:
        return cursos.obtener_curso_de_facilitador(db, curso_id, facilitador_id, _es_principal(db, facilitador_id))
    except ErrorCurso as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _exigir_principal(db: Session, facilitador_id: int, que: str) -> None:
    if not _es_principal(db, facilitador_id):
        raise HTTPException(status_code=403, detail=f"Solo un facilitador principal puede {que}")


def _respuesta_del_jugador(db: Session, curso: CursoORM) -> CursoDelJugadorResponse:
    facilitador = cursos.facilitador_de_curso(db, curso)
    return CursoDelJugadorResponse(
        id=curso.id,
        nombre=curso.nombre,
        facilitador_nombre=facilitador.nombre,
        facilitador_avatar_url=facilitador.avatar_url,
        preset_ensenanza=cursos.preset_de_curso(db, curso),
    )


# --- jugador (las rutas fijas van antes que las de `/{curso_id}`) ----------------------------------------------
@router.post("/unirse", response_model=CursoDelJugadorResponse, summary="Entrar a un curso con su código (jugador)")
def unirse(
    data: UnirseCursoRequest,
    solicitud: Request,
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CursoDelJugadorResponse:
    """Inscribe al jugador en el curso del código; si ya estaba en otro, lo cambia. Responde 400 si el código no
    sirve (no existe o el curso cerró los ingresos) y 429 si el cliente ya gastó sus intentos fallidos."""
    cliente = _cliente_de(solicitud)
    _exigir_intentos_disponibles(cliente)
    usuario = get_user_by_id(db, user_id)
    try:
        curso = cursos.unirse_con_codigo(db, usuario, data.codigo)
    except CursoCompleto as error:  # el código era correcto: no cuenta como intento fallido
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ErrorCurso as error:
        registrar_intento_fallido(cliente)
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _respuesta_del_jugador(db, curso)


@router.get("/mio", response_model=CursoDelJugadorResponse | None, summary="El curso del jugador, o null")
def mi_curso(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> CursoDelJugadorResponse | None:
    """El curso en el que está inscrito el usuario autenticado, o `null` si no está en ninguno."""
    curso = cursos.curso_de(db, user_id)
    return _respuesta_del_jugador(db, curso) if curso else None


@router.get("/mio/videos", response_model=dict[str, str], summary="Videos por pieza que me corresponde ver")
def videos_para_mi(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    """Mapa `tipo_pieza -> url`. Los videos **generales** son los del facilitador principal y los ven todos los
    jugadores, con o sin curso; si el facilitador del curso del jugador subió el suyo para una pieza, ese reemplaza al
    general de esa pieza. Una pieza sin video en ninguno de los dos no aparece."""
    return cursos.videos_visibles(db, user_id)


@router.get("/ranking", response_model=RankingResponse, summary="Ranking por nivel (de mi curso o de los jugadores sin curso)")
def ranking(
    limite: int = reportes.LIMITE_POR_DEFECTO,
    curso_id: int | None = None,
    user_id: int = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RankingResponse:
    """Ranking por nivel (el que mide la calibración de cada partida). Un jugador inscrito en un curso ve el de su
    curso; uno sin curso ve el de los jugadores sin curso. Se muestran los primeros `limite` (10 por defecto, máximo 20)
    con nombre y apellido abreviado, más la fila propia si quedó afuera. Un facilitador puede pasar `curso_id` para ver
    el de uno de sus cursos (el principal, el de cualquiera)."""
    usuario = get_user_by_id(db, user_id)
    curso = None
    if curso_id is not None:
        if usuario.rol != "facilitador":
            raise HTTPException(status_code=403, detail="Solo un facilitador puede ver el ranking de un curso ajeno")
        curso = _del_facilitador(db, curso_id, user_id)
    return RankingResponse(**reportes.ranking(db, usuario, limite, curso))


@router.get("/mio/avisos", response_model=list[AvisoDeCursoResponse], summary="Los avisos del docente a mi curso")
def avisos_de_mi_curso(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AvisoDeCursoResponse]:
    """Los últimos avisos que el docente le escribió al curso del jugador (vacío si no está en ninguno). Se leen, no se responden."""
    curso = cursos.curso_de(db, user_id)
    if curso is None:
        return []
    return [AvisoDeCursoResponse(**{**a, "creado_en": fecha_a_iso(a["creado_en"])}) for a in aula.avisos_del_curso(db, curso.id)]


@router.get("/mio/tareas", response_model=list[TareaDelAlumnoResponse], summary="Las tareas de mi curso con mi avance")
def tareas_de_mi_curso(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> list[TareaDelAlumnoResponse]:
    """Las tareas que el docente le puso al curso del jugador, con lo que lleva cada una (se mide solo con lo que jugó)."""
    curso = cursos.curso_de(db, user_id)
    if curso is None:
        return []
    return [TareaDelAlumnoResponse(**{**t, "desde": fecha_a_iso(t["desde"]), "hasta": fecha_a_iso(t["hasta"])}) for t in aula.tareas_para_el_alumno(db, curso.id, user_id)]


@router.post("/salir", status_code=status.HTTP_204_NO_CONTENT, summary="Salir del curso (jugador)")
def salir(user_id: int = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    """El jugador deja su curso. 404 si no estaba en ninguno."""
    if not cursos.salir_del_curso(db, user_id):
        raise HTTPException(status_code=404, detail="No estás inscrito en ningún curso")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- facilitador ----------------------------------------------------------------------------------------------
@router.post("", response_model=CursoResponse, status_code=status.HTTP_201_CREATED, summary="Crear un curso (facilitador)")
def crear_curso(
    data: CrearCursoRequest,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> CursoResponse:
    """Crea un curso con su código. Máximo 20 por facilitador. Un facilitador principal puede mandar
    `facilitador_id` para crearlo a nombre de otro facilitador activo (403 si no es principal)."""
    dueno_id = data.facilitador_id if data.facilitador_id is not None else facilitador_id
    if dueno_id != facilitador_id:
        _exigir_principal(db, facilitador_id, "crear cursos para otro facilitador")
    try:
        if dueno_id != facilitador_id:
            cursos.facilitador_asignable(db, dueno_id)
        curso = cursos.crear_curso(db, dueno_id, data.nombre, data.cupo, data.preset_ensenanza)
    except ErrorCurso as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _respuesta_curso(db, curso, 0)


@router.get("", response_model=list[CursoResponse], summary="Mis cursos (facilitador)")
def listar_mis_cursos(
    todos: bool = False,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> list[CursoResponse]:
    """Los cursos del facilitador con su código y cuántos estudiantes tiene cada uno. Con `?todos=true`, un
    facilitador principal ve los de todos los facilitadores (403 si no es principal)."""
    if todos:
        _exigir_principal(db, facilitador_id, "ver los cursos de todos los facilitadores")
    items = cursos.listar_cursos(db, None if todos else facilitador_id)
    return [_respuesta_curso(db, item["curso"], item["estudiantes"]) for item in items]


@router.patch("/{curso_id}", response_model=CursoResponse, summary="Renombrar, abrir/cerrar ingresos o reasignar un curso")
def editar_curso(
    curso_id: int,
    data: EditarCursoRequest,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> CursoResponse:
    """Edición parcial. `facilitador_id` pasa el curso a otro facilitador activo y solo lo puede hacer un
    facilitador principal (403 si no lo es); los estudiantes inscritos siguen en el curso."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    if data.facilitador_id is not None and data.facilitador_id != curso.facilitador_id:
        _exigir_principal(db, facilitador_id, "asignar un curso a otro facilitador")
    try:
        if data.facilitador_id is not None:
            curso = cursos.reasignar_curso(db, curso, data.facilitador_id)  # primero lo que puede fallar
        curso = cursos.actualizar_curso(
            db,
            curso,
            data.nombre,
            data.acepta_ingresos,
            data.cupo,
            data.preset_ensenanza,
            permite_retos=data.permite_retos,
            autorizacion_familias=data.autorizacion_familias,
        )
    except ErrorCurso as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return _respuesta_curso(db, curso, len(cursos.estudiantes_del_curso(db, curso)))


@router.post("/{curso_id}/codigo", response_model=CursoResponse, summary="Generar un código nuevo para el curso")
def regenerar_codigo(
    curso_id: int,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> CursoResponse:
    """El código anterior deja de servir; los estudiantes que ya están inscritos siguen en el curso."""
    curso = cursos.regenerar_codigo(db, _del_facilitador(db, curso_id, facilitador_id))
    return _respuesta_curso(db, curso, len(cursos.estudiantes_del_curso(db, curso)))


@router.delete("/{curso_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar un curso")
def eliminar_curso(
    curso_id: int,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> Response:
    """Borra el curso. Sus estudiantes no se borran: quedan sin curso."""
    cursos.eliminar_curso(db, _del_facilitador(db, curso_id, facilitador_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{curso_id}/reporte", response_model=ReporteCursoResponse, summary="Reporte de participación y progreso del curso")
def reporte(
    curso_id: int,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> ReporteCursoResponse:
    """Indicadores del curso (alumnos activos en los últimos 7 días, partidas por alumno en la semana y precisión de la
    primera semana frente a la última) y una fila por alumno, ordenada por puesto en el ranking."""
    datos = reportes.reporte_curso(db, _del_facilitador(db, curso_id, facilitador_id))
    datos["alumnos"] = [
        AlumnoReporteResponse(**{**fila, "ultima_partida": fecha_a_iso(fila["ultima_partida"]) if fila["ultima_partida"] else None})
        for fila in datos["alumnos"]
    ]
    return ReporteCursoResponse(**datos)


@router.get(
    "/{curso_id}/estudiantes", response_model=list[EstudianteDeCursoResponse], summary="Estudiantes de un curso"
)
def estudiantes(
    curso_id: int,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> list[EstudianteDeCursoResponse]:
    curso = _del_facilitador(db, curso_id, facilitador_id)
    return [
        EstudianteDeCursoResponse(
            id=item["usuario"].id,
            nombre=item["usuario"].nombre,
            email=item["usuario"].email,
            avatar_url=item["usuario"].avatar_url,
            nivel_estimado=item["usuario"].nivel_estimado,
            rango_estimado=item["usuario"].rango_estimado,
            activo=item["usuario"].activo,
            unido_en=fecha_a_iso(item["unido_en"]),
            edad=item["usuario"].edad,
            es_menor=None if item["usuario"].edad is None else item["usuario"].edad < autorizacion.MAYORIA_DE_EDAD,
            tutor_nombre=item["usuario"].tutor_nombre,
            tutor_telefono=item["usuario"].tutor_telefono,
            tutor_email=item["usuario"].tutor_email,
            autorizado=curso.autorizacion_familias or autorizacion.autorizado_por_si_mismo(item["usuario"]),
            cuenta_gestionada=item["usuario"].cuenta_gestionada,
        )
        for item in cursos.estudiantes_del_curso(db, curso)
    ]


@router.patch(
    "/{curso_id}/estudiantes/{usuario_id}/familia",
    response_model=EstudianteDeCursoResponse,
    summary="Cargar la edad y el contacto de la familia de un alumno (docente)",
)
def datos_de_familia_del_alumno(
    curso_id: int,
    usuario_id: int,
    data: ActualizarDatosDeFamiliaRequest,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> EstudianteDeCursoResponse:
    """El docente carga o corrige la edad de un alumno y el nombre y el teléfono de su madre, padre o tutor, para poder avisarles si pasa algo. 404 si el
    alumno no está en este curso. Solo cambia lo que viene."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    inscripto = next((item for item in cursos.estudiantes_del_curso(db, curso) if item["usuario"].id == usuario_id), None)
    if inscripto is None:
        raise HTTPException(status_code=404, detail="Ese alumno no está en este curso")
    alumno = inscripto["usuario"]
    campos = data.model_dump(exclude_unset=True)
    try:
        if "edad" in campos:
            alumno.edad = autorizacion.validar_edad(campos["edad"])
        if {"tutor_nombre", "tutor_telefono", "tutor_email"} & campos.keys():
            autorizacion.guardar_datos_del_tutor(
                alumno, campos.get("tutor_nombre"), campos.get("tutor_telefono"), None, campos_presentes=set(campos), email=campos.get("tutor_email")
            )
    except autorizacion.ErrorAutorizacion as error:
        db.rollback()
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    db.commit()
    return EstudianteDeCursoResponse(
        id=alumno.id,
        nombre=alumno.nombre,
        email=alumno.email,
        avatar_url=alumno.avatar_url,
        nivel_estimado=alumno.nivel_estimado,
        rango_estimado=alumno.rango_estimado,
        activo=alumno.activo,
        unido_en=fecha_a_iso(inscripto["unido_en"]),
        edad=alumno.edad,
        es_menor=None if alumno.edad is None else alumno.edad < autorizacion.MAYORIA_DE_EDAD,
        tutor_nombre=alumno.tutor_nombre,
        tutor_telefono=alumno.tutor_telefono,
        tutor_email=alumno.tutor_email,
        autorizado=curso.autorizacion_familias or autorizacion.autorizado_por_si_mismo(alumno),
        cuenta_gestionada=alumno.cuenta_gestionada,
    )


@router.delete(
    "/{curso_id}/estudiantes/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Sacar a un estudiante del curso"
)
def quitar_estudiante(
    curso_id: int,
    usuario_id: int,
    facilitador_id: int = Depends(get_current_facilitador),
    db: Session = Depends(get_db),
) -> Response:
    """Lo saca del curso sin borrar su cuenta. 404 si no estaba inscrito en él."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    if not cursos.quitar_estudiante(db, curso, usuario_id):
        raise HTTPException(status_code=404, detail="Ese estudiante no está en el curso")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- seguimiento a distancia: avisos, tareas y cuentas (docente) ---------------------------------------------------------------------
@router.get("/{curso_id}/avisos", response_model=list[AvisoDeCursoResponse], summary="Los avisos que le escribí al curso")
def avisos_del_curso(curso_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> list[AvisoDeCursoResponse]:
    curso = _del_facilitador(db, curso_id, facilitador_id)
    return [AvisoDeCursoResponse(**{**a, "creado_en": fecha_a_iso(a["creado_en"])}) for a in aula.avisos_del_curso(db, curso.id)]


@router.post("/{curso_id}/avisos", response_model=AvisoDeCursoResponse, status_code=status.HTTP_201_CREATED, summary="Escribirle un aviso al curso")
def publicar_aviso(
    curso_id: int, data: AvisoDeCursoRequest, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> AvisoDeCursoResponse:
    """Un mensaje corto para los alumnos (la consigna, el enlace de la clase en vivo). Se leen en su pantalla; no hay forma de responder."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    try:
        aviso = aula.publicar_aviso(db, curso.id, facilitador_id, data.texto, data.enlace)
    except aula.ErrorAula as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    autor = get_user_by_id(db, facilitador_id)
    return AvisoDeCursoResponse(id=aviso.id, texto=aviso.texto, enlace=aviso.enlace, creado_en=fecha_a_iso(aviso.creado_en), autor=autor.nombre if autor else None)


@router.delete("/{curso_id}/avisos/{aviso_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Borrar un aviso del curso")
def borrar_aviso(curso_id: int, aviso_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> Response:
    curso = _del_facilitador(db, curso_id, facilitador_id)
    if not aula.borrar_aviso(db, curso.id, aviso_id):
        raise HTTPException(status_code=404, detail="No existe ese aviso")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{curso_id}/tareas", response_model=list[TareaDelDocenteResponse], summary="Las tareas del curso con el avance de cada alumno")
def tareas_del_curso(curso_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> list[TareaDelDocenteResponse]:
    curso = _del_facilitador(db, curso_id, facilitador_id)
    return [TareaDelDocenteResponse(**{**t, "desde": fecha_a_iso(t["desde"]), "hasta": fecha_a_iso(t["hasta"])}) for t in aula.tareas_con_resultados(db, curso.id)]


@router.post("/{curso_id}/tareas", response_model=TareaDelDocenteResponse, status_code=status.HTTP_201_CREATED, summary="Ponerle una tarea al curso")
def crear_tarea(
    curso_id: int, data: TareaDeCursoRequest, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> TareaDelDocenteResponse:
    """Una meta con fecha límite que el sistema mide solo con lo que los alumnos juegan (partidas contra la IA, contra compañeros o una precisión)."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    try:
        tarea = aula.crear_tarea(db, curso.id, facilitador_id, data.titulo, data.tipo, data.meta, data.dias)
    except aula.ErrorAula as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    nueva = next(t for t in aula.tareas_con_resultados(db, curso.id) if t["id"] == tarea.id)
    return TareaDelDocenteResponse(**{**nueva, "desde": fecha_a_iso(nueva["desde"]), "hasta": fecha_a_iso(nueva["hasta"])})


@router.delete("/{curso_id}/tareas/{tarea_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Borrar una tarea del curso")
def borrar_tarea(curso_id: int, tarea_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> Response:
    curso = _del_facilitador(db, curso_id, facilitador_id)
    if not aula.borrar_tarea(db, curso.id, tarea_id):
        raise HTTPException(status_code=404, detail="No existe esa tarea")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{curso_id}/estudiantes/crear",
    response_model=CrearAlumnosResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear las cuentas de los alumnos que no tienen correo (docente)",
)
def crear_cuentas_de_alumnos(
    curso_id: int, data: CrearAlumnosRequest, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> CrearAlumnosResponse:
    """Crea una cuenta por alumno, ya inscripta en el curso, con un usuario y una clave temporal que se ven esta única vez. Todo o nada: si algo no sirve (el cupo, el
    plan de la institución, un teléfono), no se crea ninguna. El alumno entra escribiendo su usuario y la clave, y puede cambiarla desde su perfil."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    try:
        creadas = cuentas_de_alumnos.crear_alumnos(db, curso, [a.model_dump() for a in data.alumnos])
    except ErrorCurso as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    auditar(db, facilitador_id, "curso.crear_cuentas", "curso", curso.id, f"cantidad={len(creadas)}")
    return CrearAlumnosResponse(cuentas=[CuentaCreadaResponse(**c) for c in creadas], dominio=cuentas_de_alumnos.DOMINIO_DE_ALUMNOS)


@router.post(
    "/{curso_id}/estudiantes/{usuario_id}/restablecer-clave",
    response_model=ClaveRestablecidaResponse,
    summary="Restablecer la clave de un alumno (docente)",
)
def restablecer_clave_del_alumno(
    curso_id: int, usuario_id: int, facilitador_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)
) -> ClaveRestablecidaResponse:
    """Restablece una cuenta gestionada por el docente. Las cuentas personales recuperan su clave por correo."""
    curso = _del_facilitador(db, curso_id, facilitador_id)
    inscripto = next((item for item in cursos.estudiantes_del_curso(db, curso) if item["usuario"].id == usuario_id), None)
    if inscripto is None:
        raise HTTPException(status_code=404, detail="Ese alumno no está en este curso")
    alumno = inscripto["usuario"]
    try:
        clave = cuentas_de_alumnos.restablecer_clave(db, alumno)
    except cursos.ErrorCurso as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    auditar(db, facilitador_id, "usuario.restablecer_clave", "usuario", alumno.id)
    return ClaveRestablecidaResponse(usuario=cuentas_de_alumnos.usuario_de_correo(alumno.email) or alumno.email, password=clave)
