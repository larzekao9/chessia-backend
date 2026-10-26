"""Panel de control del brazo (`/brazo/control`): conexión, telemetría, parámetros, configuraciones y mover una pieza. Solo facilitador."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.rutas.ruta_auth import get_current_principal
from backend.servicios.brazo import control_brazo
from backend.servicios.brazo.control_brazo import ErrorControl

router = APIRouter(prefix="/brazo/control", tags=["brazo-control"], dependencies=[Depends(get_current_principal)])


def _llamar(funcion, *args, **kwargs):
    try:
        return funcion(*args, **kwargs)
    except ErrorControl as error:
        raise HTTPException(status_code=error.codigo, detail=error.mensaje) from error


class ConectarRequest(BaseModel):
    host: str = Field("192.168.5.1", max_length=100)
    puerto: int = Field(29999, ge=1, le=65535)
    emulador: bool = False


class ParametrosRequest(BaseModel):
    valores: dict
    guardar: bool = False


class PinzaRequest(BaseModel):
    accion: str
    ancho_mm: float | None = None


class ArticulacionesRequest(BaseModel):
    angulos: list[float] = Field(min_length=6, max_length=6)


class RelativoRequest(BaseModel):
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    velocidad_mm_s: int = Field(20, ge=1, le=100)


class ConfiguracionRequest(BaseModel):
    nombre: str
    descripcion: str = ""


class RutinaRequest(BaseModel):
    pasos: list[dict] = Field(min_length=1, max_length=200)


class EjecutarRutinaRequest(BaseModel):
    factor: float = Field(1.0, ge=0.25, le=2.0)
    confirmar: bool = False


class CalibrarTableroRequest(BaseModel):
    puntos: dict[str, list[float]]
    """Las poses `(x, y, z, rx, ry, rz)` de `a1`, `h1` y `a8` (y `h8`, opcional), tomadas con la punta de la pinza en el centro de cada casilla."""
    angulos_cero: list[float] = Field(min_length=6, max_length=6)
    """Los 6 ángulos de la posición cero (dónde espera el brazo, fuera del encuadre de la cámara)."""


class ProbarAperturaRequest(BaseModel):
    casilla: str = Field(max_length=2)
    tipo_pieza: str = Field("p", max_length=1)


class ProbarLugarRequest(BaseModel):
    lugar: str = Field(max_length=30)
    """`cementerio:K` (K de 0 a 29), `reserva:dama_negra` o `reserva:dama_blanca`."""


class MoverPiezaRequest(BaseModel):
    origen: str = Field(max_length=2)
    destino: str = Field("", max_length=2)
    tipo_pieza: str = Field("p", max_length=1)
    captura: bool = False
    """El destino tiene una pieza: se retira primero al cementerio."""
    tipo_capturada: str = Field("p", max_length=1)
    al_cementerio: bool = False
    """Manda la pieza del origen al cementerio (sin destino)."""
    casilla_cementerio: int = Field(0, ge=0, le=29)


@router.get("/estado")
def estado() -> dict:
    return control_brazo.estado()


@router.post("/conectar")
def conectar(cuerpo: ConectarRequest) -> dict:
    return _llamar(control_brazo.conectar, cuerpo.host, cuerpo.puerto, cuerpo.emulador)


@router.post("/desconectar")
def desconectar() -> dict:
    return _llamar(control_brazo.desconectar)


@router.post("/habilitar")
def habilitar() -> dict:
    return _llamar(control_brazo.habilitar)


@router.post("/deshabilitar")
def deshabilitar() -> dict:
    return _llamar(control_brazo.deshabilitar)


@router.post("/limpiar-error")
def limpiar_error() -> dict:
    return _llamar(control_brazo.limpiar_error)


@router.post("/parar")
def parar() -> dict:
    return _llamar(control_brazo.parar)


@router.get("/diagnostico")
def diagnostico() -> list[dict]:
    """Los últimos comandos enviados al controlador, con su resultado (para entender un fallo de la pinza o del brazo)."""
    return _llamar(control_brazo.diagnostico)


@router.get("/calibracion/tablero")
def tablero_calibrado() -> dict | None:
    """La calibración del tablero que usa el brazo: tamaño de las casillas, esquinas y la sugerencia de cementerio y reservas (`null` si no hay)."""
    return _llamar(control_brazo.tablero_calibrado)


@router.post("/calibracion/tablero")
def calibrar_tablero(cuerpo: CalibrarTableroRequest) -> dict:
    """Guarda y aplica la calibración del tablero con las poses tomadas del brazo real. 409 si es el emulador o hay un movimiento en curso, 422 si los datos no sirven."""
    return _llamar(control_brazo.calibrar_tablero, cuerpo.puntos, cuerpo.angulos_cero)


@router.get("/parametros")
def parametros() -> dict:
    return _llamar(control_brazo.parametros)


@router.put("/parametros")
def actualizar_parametros(cuerpo: ParametrosRequest) -> dict:
    return _llamar(control_brazo.actualizar_parametros, cuerpo.valores, cuerpo.guardar)


@router.post("/pinza/inicializar")
def inicializar_pinza() -> dict:
    return _llamar(control_brazo.inicializar_pinza)


@router.post("/pinza")
def mover_pinza(cuerpo: PinzaRequest) -> dict:
    return _llamar(control_brazo.mover_pinza, cuerpo.accion, cuerpo.ancho_mm)


@router.get("/configuraciones")
def listar_configuraciones() -> list[dict]:
    return _llamar(control_brazo.listar_configuraciones)


@router.post("/configuraciones")
def guardar_configuracion(cuerpo: ConfiguracionRequest) -> list[dict]:
    return _llamar(control_brazo.guardar_configuracion, cuerpo.nombre, cuerpo.descripcion)


@router.post("/configuraciones/{nombre}/aplicar")
def aplicar_configuracion(nombre: str) -> dict:
    return _llamar(control_brazo.aplicar_configuracion, nombre)


@router.delete("/configuraciones/{nombre}")
def borrar_configuracion(nombre: str) -> list[dict]:
    return _llamar(control_brazo.borrar_configuracion, nombre)


@router.post("/mover-pieza/ensayo")
def ensayo(cuerpo: MoverPiezaRequest) -> list[dict]:
    return _llamar(control_brazo.plan_de_movimiento, cuerpo.origen, cuerpo.destino, cuerpo.tipo_pieza, cuerpo.captura, cuerpo.tipo_capturada, cuerpo.al_cementerio, cuerpo.casilla_cementerio)


@router.post("/mover-pieza")
def mover_pieza(cuerpo: MoverPiezaRequest) -> dict:
    return _llamar(control_brazo.mover_pieza, cuerpo.origen, cuerpo.destino, cuerpo.tipo_pieza, cuerpo.captura, cuerpo.tipo_capturada, cuerpo.al_cementerio, cuerpo.casilla_cementerio)


@router.post("/mover-articulaciones")
def mover_articulaciones(cuerpo: ArticulacionesRequest) -> dict:
    return _llamar(control_brazo.mover_articulaciones, cuerpo.angulos)


@router.post("/mover-relativo")
def mover_relativo(cuerpo: RelativoRequest) -> dict:
    return _llamar(control_brazo.mover_relativo, cuerpo.dx, cuerpo.dy, cuerpo.dz, cuerpo.velocidad_mm_s)


@router.post("/centrar/bajar")
def centrar_bajar(cuerpo: ProbarAperturaRequest) -> dict:
    """Baja despacio sobre la pieza de `casilla`, con la pinza abierta, y se queda ahí para centrarla (con «mover-relativo») y guardar el corrimiento."""
    return _llamar(control_brazo.centrar_bajar, cuerpo.casilla, cuerpo.tipo_pieza)


@router.post("/centrar/guardar")
def centrar_guardar() -> dict:
    """Guarda cuánto se corrió la pinza respecto de la casilla calibrada; desde ahí todo el tablero lleva ese corrimiento."""
    return _llamar(control_brazo.centrar_guardar)


@router.post("/centrar/subir")
def centrar_subir() -> dict:
    """Sube de la pieza y vuelve a la posición de descanso."""
    return _llamar(control_brazo.centrar_subir)


@router.post("/probar-apertura")
def probar_apertura(cuerpo: ProbarAperturaRequest) -> dict:
    """Baja con la pinza abierta (sin cerrarla) sobre la pieza de `casilla` para ver si la apertura de ese tipo de pieza la rodea bien."""
    return _llamar(control_brazo.probar_apertura, cuerpo.casilla, cuerpo.tipo_pieza)


@router.post("/probar-lugar")
def probar_lugar(cuerpo: ProbarLugarRequest) -> dict:
    """Lleva la punta, sin pieza, sobre un lugar del cementerio o una reserva y baja hasta unos mm de donde soltaría (para revisarlos antes de jugar)."""
    return _llamar(control_brazo.probar_lugar, cuerpo.lugar)


@router.post("/posicion-inicial")
def posicion_inicial() -> dict:
    return _llamar(control_brazo.ir_a_posicion_inicial)


@router.get("/rutinas")
def listar_rutinas() -> list[dict]:
    """Las rutinas guardadas con su cantidad de pasos y si ya están validadas en el emulador (requisito para el brazo real)."""
    return control_brazo.listar_rutinas()


@router.get("/rutinas/{nombre}")
def obtener_rutina(nombre: str) -> dict:
    return _llamar(control_brazo.obtener_rutina, nombre)


@router.put("/rutinas/{nombre}")
def guardar_rutina(nombre: str, cuerpo: RutinaRequest) -> dict:
    """Valida y guarda la rutina (la reemplaza si existe). Los pasos: `articulaciones`, `pinza`, `esperar` o `rutina` (llama a otra)."""
    return _llamar(control_brazo.guardar_rutina, nombre, cuerpo.pasos)


@router.delete("/rutinas/{nombre}")
def borrar_rutina(nombre: str) -> dict:
    _llamar(control_brazo.borrar_rutina, nombre)
    return {"borrada": nombre}


@router.post("/rutinas/{nombre}/ejecutar")
def ejecutar_rutina(nombre: str, cuerpo: EjecutarRutinaRequest) -> dict:
    """Corre la rutina. En el emulador sin más; en el brazo real solo si ya está validada y con `confirmar: true`."""
    return _llamar(control_brazo.ejecutar_rutina, nombre, cuerpo.factor, cuerpo.confirmar)


@router.get("/limites")
def limites_geometricos() -> dict:
    """Límites de las articulaciones y distancias de seguridad que usan las validaciones de pose (el front las aplica igual al mover el esqueleto)."""
    return control_brazo.limites()


@router.get("/verificar-geometria")
def verificar_geometria() -> dict:
    """Con el brazo real conectado: compara la pose del controlador con la del modelo para los mismos ángulos (prueba que el esqueleto es confiable)."""
    return _llamar(control_brazo.verificar_geometria)
