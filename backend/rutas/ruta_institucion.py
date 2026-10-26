"""Instituciones clientes: la plataforma las crea y les asigna sus docentes; cada docente y administrador ve la suya.

- Solo la plataforma (facilitador principal): listar, crear, editar y asignar o quitar docentes.
- Administrador de la institución: ve la suya con la lista de sus docentes y de sus alumnos (`/instituciones/mia/docentes` y `/mia/alumnos`).
- Cualquier docente con institución: ve la suya, sin la lista de docentes (`/instituciones/mia`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.database import fecha_a_iso
from backend.esquemas.institucion_esquema import (
    AlumnoInstitucionResponse,
    ConvertirInstitucionRequest,
    CrearInstitucionRequest,
    DocenteInstitucionResponse,
    EditarInstitucionRequest,
    InstitucionCreadaResponse,
    InstitucionResponse,
)
from backend.modelos.tablas_orm import InstitucionORM, UsuarioORM
from backend.rutas.ruta_auth import (
    eliminar_par_de_prueba,
    get_current_admin_institucion,
    get_current_facilitador,
    get_current_principal,
    get_db,
)
from backend.servicios.auditoria import registrar as auditar
from backend.servicios.institucion import servicio_instituciones as instituciones
from backend.servicios.institucion.servicio_instituciones import ErrorInstitucion, NombreEnUso
from backend.servicios.auth.servicio_invitaciones import generar_codigo
from backend.servicios.suscripcion import servicio_suscripciones as suscripciones
from backend.servicios.suscripcion.servicio_suscripciones import ErrorSuscripcion

router = APIRouter(prefix="/instituciones", tags=["instituciones"])


def _docentes(db: Session, institucion: InstitucionORM) -> list[DocenteInstitucionResponse]:
    return [
        DocenteInstitucionResponse(
            id=d.id, nombre=d.nombre, email=d.email, es_administrador=instituciones.es_administrador(institucion, d.id)
        )
        for d in instituciones.docentes_de(db, institucion)
    ]


def _respuesta(db: Session, institucion: InstitucionORM, usuario_id: int | None = None, con_docentes: bool = False) -> InstitucionResponse:
    administrador = db.get(UsuarioORM, institucion.administrador_id) if institucion.administrador_id is not None else None
    docentes = _docentes(db, institucion)
    return InstitucionResponse(
        id=institucion.id,
        nombre=institucion.nombre,
        tipo=institucion.tipo,
        administrador_id=institucion.administrador_id,
        administrador_nombre=administrador.nombre if administrador else None,
        cursos=instituciones.contar_cursos(db, institucion.id),
        alumnos=instituciones.contar_alumnos(db, institucion.id),
        cantidad_docentes=len(docentes),
        es_administrador=usuario_id is not None and instituciones.es_administrador(institucion, usuario_id),
        docentes=docentes if con_docentes else None,
        creado_en=fecha_a_iso(institucion.creado_en),
        es_demostracion=institucion.es_demostracion,
        es_prueba=institucion.es_prueba,
        dominio_correo=institucion.dominio_correo,
    )


def _suscripcion_a_iso(datos: dict) -> dict:
    """El resumen de la suscripción con sus fechas como texto, listo para la respuesta."""
    return {c: (fecha_a_iso(v) if v is not None and c in ("institucion_creada_en", "prueba_hasta", "vigente_hasta") else v) for c, v in datos.items()}


def _existente(db: Session, institucion_id: int) -> InstitucionORM:
    institucion = instituciones.obtener(db, institucion_id)
    if institucion is None:
        raise HTTPException(status_code=404, detail="No existe esa institución")
    return institucion


def _error(error: ErrorInstitucion) -> HTTPException:
    return HTTPException(status_code=409 if isinstance(error, NombreEnUso) else 400, detail=str(error))


@router.get("", response_model=list[InstitucionResponse], summary="Todas las instituciones (solo la plataforma)")
def listar_instituciones(_: int = Depends(get_current_principal), db: Session = Depends(get_db)) -> list[InstitucionResponse]:
    return [_respuesta(db, i, con_docentes=True) for i in instituciones.listar(db)]


@router.post("", response_model=InstitucionCreadaResponse, status_code=status.HTTP_201_CREATED, summary="Crear una institución (solo la plataforma)")
def crear_institucion(
    data: CrearInstitucionRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionCreadaResponse:
    """Crea la institución con su suscripción de una vez: el plan, la prueba gratis de `dias_de_prueba` días o, si ya pagó, un pago inicial
    que la deja activa. Su director puede ser un facilitador que ya tiene cuenta (`administrador_id`) o alguien que todavía no se registró:
    con `invitar_administrador` se genera un código (24 h) y quien se registre con él queda como administrador. El código se devuelve
    una sola vez."""
    if data.administrador_id is not None and data.invitar_administrador:
        raise HTTPException(status_code=400, detail="Elegí un administrador que ya tenga cuenta o invitá a uno nuevo, no las dos cosas")
    if data.es_prueba and (data.administrador_id is not None or data.pago_inicial_ciclo is not None):
        raise HTTPException(status_code=400, detail="Una institución de prueba no lleva administrador ya registrado ni pago: quien la evalúa se registra con el código")
    try:
        institucion = instituciones.crear(db, data.nombre, data.tipo, data.administrador_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    if data.es_prueba:
        institucion.es_prueba = True
        db.commit()
    # quien ya pagó no lleva prueba gratis: el período pagado empieza hoy (los días de prueba que queden siempre se suman antes del pago)
    suscripciones.crear_prueba(db, institucion, data.plan, 0 if data.pago_inicial_ciclo is not None else data.dias_de_prueba)
    if data.pago_inicial_ciclo is not None:
        try:
            suscripciones.registrar_pago_recibido(db, institucion, data.plan, data.pago_inicial_ciclo, "Pago inicial", actor_id)
        except ErrorSuscripcion as error:
            raise HTTPException(status_code=error.estado, detail=str(error)) from error
    codigo = fila = None
    if data.invitar_administrador or data.es_prueba:
        fila, codigo = generar_codigo(
            db,
            actor_id,
            data.minutos_del_codigo,
            f"{'Evaluación' if data.es_prueba else 'Administrador'} de {institucion.nombre}",
            institucion_id=institucion.id,
            como_administrador=True,
            es_evaluacion=data.es_prueba,
        )
    auditar(db, actor_id, "institucion.crear", "institucion", institucion.id, f"tipo={institucion.tipo}, plan={data.plan}{', de prueba' if data.es_prueba else ''}")
    base = _respuesta(db, institucion, con_docentes=True)
    return InstitucionCreadaResponse(
        **base.model_dump(),
        suscripcion=_suscripcion_a_iso(suscripciones.resumen(db, institucion)),
        codigo_administrador=codigo,
        codigo_expira_en=fecha_a_iso(fila.expira_en) if fila else None,
    )


@router.post(
    "/{institucion_id}/convertir",
    response_model=InstitucionResponse,
    summary="Convertir una institución de prueba en cliente (solo la plataforma)",
)
def convertir_en_cliente(
    institucion_id: int, data: ConvertirInstitucionRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """El trato se cerró: la institución deja de ser de prueba y su director conserva todo lo que armó. Se borra el jugador de prueba (y con él el
    botón «Ver como jugador»). Con `nombre` y `tipo` se corrigen los datos; con `plan` y `pago_inicial_ciclo` se define la suscripción."""
    institucion = _existente(db, institucion_id)
    try:
        if data.plan is not None:
            suscripcion = suscripciones.obtener_o_crear(db, institucion)
            if suscripciones.PLANES[data.plan].tope_alumnos < instituciones.contar_alumnos(db, institucion.id):
                raise ErrorSuscripcion("Esa institución ya tiene más alumnos de los que cubre ese plan")
            suscripcion.plan = data.plan
        instituciones.convertir_en_cliente(db, institucion, data.nombre, data.tipo)
        if data.pago_inicial_ciclo is not None:
            suscripciones.registrar_pago_recibido(db, institucion, data.plan or suscripciones.obtener_o_crear(db, institucion).plan, data.pago_inicial_ciclo, "Pago al cerrar el trato", actor_id)
    except ErrorSuscripcion as error:
        raise HTTPException(status_code=error.estado, detail=str(error)) from error
    except ErrorInstitucion as error:
        raise _error(error) from error
    if institucion.administrador_id is not None:
        eliminar_par_de_prueba(db, db.get(UsuarioORM, institucion.administrador_id))
    auditar(db, actor_id, "institucion.convertir", "institucion", institucion_id)
    return _respuesta(db, institucion, con_docentes=True)


@router.get("/mia", response_model=InstitucionResponse, summary="La institución del facilitador que consulta")
def mi_institucion(usuario_id: int = Depends(get_current_facilitador), db: Session = Depends(get_db)) -> InstitucionResponse:
    """La institución a la que pertenece el facilitador, con sus números (docentes, cursos y alumnos). 404 si no tiene."""
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return _respuesta(db, institucion, usuario_id)


@router.get(
    "/mia/docentes", response_model=list[DocenteInstitucionResponse], summary="Los docentes de mi institución (solo su administrador)"
)
def docentes_de_mi_institucion(
    usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)
) -> list[DocenteInstitucionResponse]:
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return _docentes(db, institucion)


@router.get(
    "/mia/alumnos", response_model=list[AlumnoInstitucionResponse], summary="Los alumnos de mi institución (solo su administrador)"
)
def alumnos_de_mi_institucion(
    usuario_id: int = Depends(get_current_admin_institucion), db: Session = Depends(get_db)
) -> list[AlumnoInstitucionResponse]:
    """Jugadores inscriptos en los cursos de los docentes de la institución, con su curso y su docente."""
    institucion = instituciones.institucion_de(db, db.get(UsuarioORM, usuario_id))
    if institucion is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no pertenece a ninguna institución")
    return [AlumnoInstitucionResponse(**a) for a in instituciones.alumnos_de(db, institucion)]


@router.patch("/{institucion_id}", response_model=InstitucionResponse, summary="Editar una institución (solo la plataforma)")
def editar_institucion(
    institucion_id: int, data: EditarInstitucionRequest, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """Cambia nombre, tipo y/o administrador (edición parcial; `administrador_id: null` la deja sin administrador)."""
    institucion = _existente(db, institucion_id)
    campos = data.model_dump(exclude_unset=True)
    try:
        instituciones.editar(db, institucion, campos.get("nombre"), campos.get("tipo"))
        if "dominio_correo" in campos:
            instituciones.fijar_dominio_correo(db, institucion, campos["dominio_correo"])
        if "administrador_id" in campos:
            instituciones.designar_administrador(db, institucion, campos["administrador_id"])
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.editar", "institucion", institucion_id, ", ".join(sorted(campos)))
    return _respuesta(db, institucion, con_docentes=True)


@router.put(
    "/{institucion_id}/docentes/{usuario_id}",
    response_model=InstitucionResponse,
    summary="Asignar un docente a la institución (solo la plataforma)",
)
def asignar_docente(
    institucion_id: int, usuario_id: int, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    """Pasa a un facilitador activo a esta institución (si estaba en otra, se mueve)."""
    institucion = _existente(db, institucion_id)
    try:
        instituciones.asignar_docente(db, institucion, usuario_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.docente_asignar", "institucion", institucion_id, f"usuario={usuario_id}")
    return _respuesta(db, institucion, con_docentes=True)


@router.delete(
    "/{institucion_id}/docentes/{usuario_id}",
    response_model=InstitucionResponse,
    summary="Quitar un docente de la institución (solo la plataforma)",
)
def quitar_docente(
    institucion_id: int, usuario_id: int, actor_id: int = Depends(get_current_principal), db: Session = Depends(get_db)
) -> InstitucionResponse:
    institucion = _existente(db, institucion_id)
    try:
        instituciones.quitar_docente(db, institucion, usuario_id)
    except ErrorInstitucion as error:
        raise _error(error) from error
    auditar(db, actor_id, "institucion.docente_quitar", "institucion", institucion_id, f"usuario={usuario_id}")
    return _respuesta(db, institucion, con_docentes=True)
