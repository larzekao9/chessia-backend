"""Respuestas del progreso de los alumnos: el informe de uno, el antes y el después de un grupo y la salud de las instituciones."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PuntoSerieResponse(BaseModel):
    numero: int
    fecha: str
    precision: float
    nivel: int


class ProgresoAlumnoResponse(BaseModel):
    """El antes (diagnóstico) y el después de un alumno; los campos `*_despues` son nulos mientras no haya con qué comparar."""

    calibradas: int
    diagnostico_completo: bool
    comparable: bool
    confirmado: bool
    precision_antes: float | None
    nivel_antes: int | None
    rango_antes: str | None
    precision_despues: float | None
    nivel_despues: int | None
    rango_despues: str | None
    variacion_precision: float | None
    variacion_nivel: int | None
    tendencia: str
    primera_en: str | None
    ultima_en: str | None
    serie: list[PuntoSerieResponse]


class DatosAlumnoInformeResponse(BaseModel):
    id: int
    nombre: str
    nivel: int | None
    rango: str | None
    curso: str | None
    docente: str | None
    institucion: str | None
    tutor_nombre: str | None = None
    tutor_telefono: str | None = None
    tutor_email: str | None = None


class ActividadAlumnoResponse(BaseModel):
    partidas: int
    partidas_7_dias: int
    partidas_30_dias: int
    ultima_partida: str | None
    dias_sin_jugar: int | None


class ResultadosAlumnoResponse(BaseModel):
    ganadas: int
    perdidas: int
    tablas: int
    racha_victorias: int
    precision_promedio: float | None


class FasePrecisionResponse(BaseModel):
    fase: str
    jugadas: int
    precision: float | None


class ErrorFrecuenteResponse(BaseModel):
    tipo: str
    nombre: str
    cantidad: int


class InformeAlumnoResponse(BaseModel):
    """Todo lo que se le muestra al docente o al responsable de un alumno; las frases de `lectura` salen de reglas fijas."""

    alumno: DatosAlumnoInformeResponse
    generado_en: str
    progreso: ProgresoAlumnoResponse
    actividad: ActividadAlumnoResponse
    resultados: ResultadosAlumnoResponse
    precision_por_fase: list[FasePrecisionResponse]
    errores_frecuentes: list[ErrorFrecuenteResponse]
    medallas: dict[str, Any]
    lectura: list[str]


class FilaGrupoResponse(BaseModel):
    id: int
    nombre: str
    activo: bool
    nivel_actual: int | None
    rango_actual: str | None
    tendencia: str
    comparable: bool
    confirmado: bool
    calibradas: int
    nivel_antes: int | None
    nivel_despues: int | None
    precision_antes: float | None
    precision_despues: float | None
    variacion_precision: float | None
    partidas: int
    partidas_30_dias: int
    ultima_partida: str | None
    dias_sin_jugar: int | None
    es_menor: bool | None = None
    tutor_nombre: str | None = None
    tutor_telefono: str | None = None
    tutor_email: str | None = None
    """El contacto de la familia, para que el docente le avise si el alumno dejó de jugar (solo lo ven el docente, el administrador de la institución y la plataforma)."""


class ResumenGrupoResponse(BaseModel):
    alumnos: int
    comparables: int
    sin_diagnostico: int
    mejoraron: int
    estables: int
    bajaron: int
    porcentaje_mejoraron: int | None
    mejora_promedio_precision: float | None
    precision_antes: float | None
    precision_despues: float | None
    nivel_antes: float | None
    nivel_despues: float | None
    partidas: int
    partidas_7_dias: int
    partidas_30_dias: int
    activos_7_dias: int
    activos_30_dias: int
    sin_actividad: int
    dias_sin_actividad: int


class ProgresoGrupoResponse(BaseModel):
    """El antes y el después de un curso o de una institución: un resumen y una fila por alumno."""

    nombre: str
    resumen: ResumenGrupoResponse
    alumnos: list[FilaGrupoResponse]


class SaludInstitucionResponse(BaseModel):
    """Qué tan viva está una institución cliente: si sus alumnos juegan y si mejoran. `en_riesgo` avisa cuándo conviene llamarla."""

    institucion_id: int
    nombre: str
    es_demostracion: bool
    es_prueba: bool
    alumnos: int
    activos_7_dias: int
    activos_30_dias: int
    partidas_30_dias: int
    comparables: int
    mejoraron: int
    porcentaje_mejoraron: int | None
    ultima_actividad: str | None
    dias_sin_actividad: int | None
    en_riesgo: bool
    motivo_riesgo: str | None
