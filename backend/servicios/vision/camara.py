"""Captura una foto del tablero desde una cámara fija conectada a esta
máquina (HU1, RF06).

Solo toma la foto — pasarla por `reconocer_tablero` (en `reconocimiento.py`)
y mostrarla en una pantalla es responsabilidad de HU6, no de acá.

La fuente de la cámara se puede fijar por variable de entorno `CAMARA_FUENTE`
(mismo patrón que `DATABASE_URL` en `backend/database.py` — nada hardcodeado
en el código), para no depender de la webcam integrada de la laptop, que no
se puede reorientar hacia el tablero y suele tener peor calidad que un
celular:

- Sin configurar: cámara 0, la webcam por defecto de la máquina.
- Un número (ej. `CAMARA_FUENTE=1`): otro índice de cámara local — el caso
  de un celular conectado como cámara "virtual" vía USB con DroidCam o Iriun
  Webcam, que aparece como una webcam más para Windows.
- Una URL (ej. `CAMARA_FUENTE=http://192.168.1.23:8080/video`): un stream de
  red, el caso de la app "IP Webcam" (Android) — sirve tanto conectando el
  celular y la laptop a la misma red WiFi como compartiendo el propio
  hotspot del celular a la laptop (más estable si el WiFi de la facultad es
  lento, porque no depende de esa red para nada).

Cámara secundaria (opcional): otro ángulo del mismo tablero. Se elige desde la app o con `CAMARA_FUENTE_2` y `CAMARA_ROTACION_2`;
cada cámara tiene su fuente, su giro y su calibración (ver `CAMARAS`). Las funciones de este módulo reciben `camara="principal"|"secundaria"`.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path

# Antes de importar OpenCV (ver `backend/main.py`): sin esto, algunas webcams USB tardan 15 a 20 s en abrir con Media Foundation.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)

CAMARAS = ("principal", "secundaria")
"""Las cámaras fijas que puede usar la app. `principal` es obligatoria; `secundaria` es opcional (otro ángulo del mismo tablero) y
solo cuenta si se le eligió una fuente. Cada una tiene su propia fuente, giro y calibración de esquinas."""

# Una cámara solo la puede abrir un proceso a la vez: la vista en vivo, el estado y el reconocimiento llegan en paralelo
# y, sin esto, el segundo `VideoCapture` falla con "no se pudo abrir" (503) mientras el primero la tiene tomada.
_CERROJO_CAMARA = threading.Lock()

_CODIGOS_ROTACION = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

ARCHIVO_PREFERENCIA = Path(
    os.environ.get("CAMARA_PREFERENCIA_ARCHIVO", str(Path(__file__).resolve().parents[3] / "calibracion_local" / "camara.json"))
)
"""La fuente y el giro de cada cámara elegidos desde la app: se recuerdan entre reinicios (archivo local de cada instalación, no se
sube a git). Tienen prioridad sobre `CAMARA_FUENTE`/`CAMARA_ROTACION` (principal) y `CAMARA_FUENTE_2`/`CAMARA_ROTACION_2` (secundaria)
del `.env`, que quedan como valores por defecto. Formato: `{"camaras": {"principal": {"fuente": "0", "rotacion": 0}, ...}}`."""
_preferencia: dict | None = None


def validar_camara(camara: str) -> str:
    if camara not in CAMARAS:
        raise ValueError(f"La cámara debe ser una de {', '.join(CAMARAS)}")
    return camara


CLAVE_AJUSTES = "ajustes"
"""Entrada de `ARCHIVO_PREFERENCIA` con los ajustes que no son de una cámara en particular (hoy, si se usa la secundaria)."""


def _leer_preferencia() -> dict:
    """Preferencias guardadas `{camara: {fuente, rotacion}}`; entiende también el formato viejo (una sola cámara, claves sueltas)."""
    global _preferencia
    if _preferencia is None:
        try:
            datos = json.loads(ARCHIVO_PREFERENCIA.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            datos = {}
        if "camaras" in datos:
            _preferencia = {nombre: dict(valores) for nombre, valores in datos["camaras"].items()}
        else:
            _preferencia = {"principal": {k: v for k, v in datos.items() if k in ("fuente", "rotacion")}} if datos else {}
    return _preferencia


def _guardar_preferencia(camara: str = "principal", **cambios) -> None:
    """Actualiza la preferencia de `camara`; un valor `None` borra esa clave."""
    global _preferencia
    datos = {nombre: dict(valores) for nombre, valores in _leer_preferencia().items()}
    actual = datos.setdefault(camara, {})
    for clave, valor in cambios.items():
        if valor is None:
            actual.pop(clave, None)
        else:
            actual[clave] = valor
    _preferencia = datos
    try:
        ARCHIVO_PREFERENCIA.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO_PREFERENCIA.write_text(json.dumps({"camaras": datos}), encoding="utf-8")
    except OSError:
        pass  # sin disco: la elección vale hasta reiniciar


def _variable(nombre: str, camara: str) -> str:
    return os.environ.get(nombre if camara == "principal" else f"{nombre}_2", "").strip()


def _fuente_por_defecto(camara: str = "principal") -> int | str | None:
    """La fuente de `camara`: la elegida desde la app o, si no, la del `.env` (un índice de cámara `0` o una URL de stream).

    La principal siempre tiene fuente (0 si no hay nada); la secundaria devuelve `None` mientras no se la configure.
    """
    guardada = _leer_preferencia().get(camara, {}).get("fuente")
    valor = str(guardada).strip() if guardada is not None else _variable("CAMARA_FUENTE", camara)
    if not valor:
        return 0 if camara == "principal" else None
    return int(valor) if valor.isdigit() else valor


def camaras_configuradas() -> list[str]:
    """Las cámaras con fuente elegida: siempre la principal, y la secundaria si se configuró."""
    return [c for c in CAMARAS if _fuente_por_defecto(c) is not None]


def usar_secundaria() -> bool:
    """Si la cámara secundaria participa (captura de fotos, reconocimiento y vistas en vivo). Apagado por defecto: se trabaja solo con la principal.

    La elección se recuerda entre reinicios; `CAMARA_USAR_SECUNDARIA=1` en el entorno la enciende cuando no hay una elegida desde la app. La
    configuración y la calibración de la secundaria no se tocan: apagarla solo la deja fuera hasta que se vuelva a encender.
    """
    guardada = _leer_preferencia().get(CLAVE_AJUSTES, {}).get("usar_secundaria")
    if guardada is not None:
        return bool(guardada)
    return os.environ.get("CAMARA_USAR_SECUNDARIA", "").strip().lower() in ("1", "true", "si", "sí")


def fijar_uso_secundaria(activa: bool) -> bool:
    """Enciende o apaga la cámara secundaria (se recuerda entre reinicios) y devuelve el valor guardado."""
    _guardar_preferencia(CLAVE_AJUSTES, usar_secundaria=bool(activa))
    return bool(activa)


def camaras_en_uso() -> list[str]:
    """Las cámaras que participan ahora: la principal y, solo si está configurada y encendida con `fijar_uso_secundaria`, la secundaria."""
    return [c for c in camaras_configuradas() if c == "principal" or usar_secundaria()]


def rotacion_actual(camara: str = "principal") -> int:
    """Giro (0, 90, 180 o 270 grados, horario) que se aplica a TODA imagen de `camara`, para que el tablero quede como esperan la
    calibración y los modelos aunque la cámara (por ejemplo un iPhone) esté montada girada. Cambiarlo invalida su calibración."""
    valor = _leer_preferencia().get(camara, {}).get("rotacion", _variable("CAMARA_ROTACION", camara) or 0)
    return int(valor) % 360


def fijar_rotacion(grados: int, camara: str = "principal") -> int:
    """Gira la imagen de `camara` en 0, 90, 180 o 270 grados (horario) y lo recuerda entre reinicios."""
    validar_camara(camara)
    if grados not in (0, 90, 180, 270):
        raise ValueError("La rotación debe ser 0, 90, 180 o 270 grados")
    _guardar_preferencia(camara, rotacion=grados)
    return grados


def _rotar(imagen: np.ndarray, camara: str = "principal") -> np.ndarray:
    grados = rotacion_actual(camara)
    return cv2.rotate(imagen, _CODIGOS_ROTACION[grados]) if grados else imagen


def _api_por_defecto() -> int | None:
    """Backend de captura de Windows según `CAMARA_API` (`MSMF` o `DSHOW`); `None` deja que OpenCV elija.

    Con varias cámaras (reales y virtuales como Iriun o DroidCam) cada backend las numera distinto: el índice 5 de MSMF no es el
    5 de DirectShow. Por eso, cuando la cámara se eligió por número, conviene fijar también el backend con el que se midió.
    """
    nombre = os.environ.get("CAMARA_API", "").strip().upper()
    return {"MSMF": cv2.CAP_MSMF, "DSHOW": cv2.CAP_DSHOW}.get(nombre)


def capturar_foto_tablero(fuente: int | str | None = None, camara: str = "principal") -> np.ndarray:
    """Toma una foto desde la cámara/fuente indicada (ya con el giro de esa cámara).

    Args:
        fuente: índice de cámara local (int) o URL de un stream de red (str). Si no se pasa, la de `camara`.
        camara: `"principal"` o `"secundaria"`.

    Returns:
        La imagen capturada, en BGR (el formato de `cv2.imread`).

    Raises:
        RuntimeError: si no se pudo abrir la cámara/fuente o capturar un frame, o si la cámara no está configurada.
    """
    validar_camara(camara)
    fuente_resuelta = fuente if fuente is not None else _fuente_por_defecto(camara)
    if fuente_resuelta is None:
        raise RuntimeError(f"La cámara {camara} no está configurada")
    with _CERROJO_CAMARA:
        return _captura_continua(fuente_resuelta, camara).ultimo_frame()


SEGUNDOS_INACTIVA = 15.0
"""Sin pedidos de fotos durante este tiempo, la cámara se suelta (otra app puede usarla) y se reabre al siguiente pedido."""

SEGUNDOS_MAXIMOS_PARA_ABRIR = 15.0
"""Lo más que se espera a que una cámara abra. Una webcam USB trabada (por ejemplo, tras cortarle el programa a la fuerza) puede quedarse
abriendo sin terminar nunca, y como se abre con el cerrojo de la cámara tomado, dejaba colgado TODO lo que usa cámara: la vista en vivo,
la búsqueda de cámaras y la calibración. Pasado este tiempo se avisa con un error claro en vez de esperar."""


def _abrir_con_limite(fuente: int | str) -> cv2.VideoCapture:
    """`cv2.VideoCapture(fuente)` con el backend de la app (`CAMARA_API`) y un tiempo máximo de espera (`SEGUNDOS_MAXIMOS_PARA_ABRIR`).

    Raises:
        RuntimeError: si la cámara no terminó de abrir a tiempo. Si abre más tarde, se suelta sola para no dejarla tomada.
    """
    api = _api_por_defecto() if isinstance(fuente, int) else None
    estado: dict = {"abandonado": False}

    def abrir() -> None:
        camara = cv2.VideoCapture(fuente, api) if api is not None else cv2.VideoCapture(fuente)
        if estado["abandonado"]:
            camara.release()
        else:
            estado["camara"] = camara

    hilo = threading.Thread(target=abrir, daemon=True, name=f"abrir-camara-{fuente}")
    hilo.start()
    hilo.join(timeout=SEGUNDOS_MAXIMOS_PARA_ABRIR)
    if hilo.is_alive() or "camara" not in estado:
        estado["abandonado"] = True
        raise RuntimeError(
            f"La cámara {fuente!r} no responde (más de {SEGUNDOS_MAXIMOS_PARA_ABRIR:.0f} s para abrir). "
            "Desenchufala y volvé a enchufarla, o cerrá el programa que la esté usando."
        )
    return estado["camara"]


class _CapturaContinua:
    """Mantiene la cámara abierta leyendo frames en un hilo, para que pedir una foto sea instantáneo y siempre actual.

    Abrir la cámara y esperar su autoexposición en cada foto (lo que se hacía antes) tardaba ~2 s; además una cámara que se
    queda abierta sin leer acumula frames viejos en el búfer (más atraso en streams de red). Aquí el hilo lee siempre y la foto
    es el último frame leído. El hilo termina solo tras `SEGUNDOS_INACTIVA` sin pedidos.
    """

    def __init__(self, fuente: int | str, camara: str = "principal") -> None:
        self.fuente = fuente
        self.camara_id = camara
        self._frame: np.ndarray | None = None
        self._error: str | None = None
        self._vivo = True
        self._ultimo_pedido = time.monotonic()
        self._listo = threading.Event()
        self._condicion = threading.Condition()
        self._numero = 0
        self._camara = _abrir_con_limite(fuente)
        if not self._camara.isOpened():
            self._camara.release()
            raise RuntimeError(f"No se pudo abrir la cámara/fuente {fuente!r}")
        self._camara.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self._camara.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self._camara.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._hilo = threading.Thread(target=self._leer, daemon=True, name=f"captura-continua-{camara}")
        self._hilo.start()

    def detener(self) -> None:
        """Suelta la cámara ya (no espera a la inactividad); sirve para abrir otra fuente."""
        self._ultimo_pedido = float("-inf")
        self._hilo.join(timeout=3.0)

    @property
    def vivo(self) -> bool:
        return self._vivo

    def _leer(self) -> None:
        fallos = 0
        leidos = 0
        try:
            while time.monotonic() - self._ultimo_pedido < SEGUNDOS_INACTIVA:
                exito, imagen = self._camara.read()
                if not exito:
                    fallos += 1
                    if fallos > 30:
                        self._error = f"No se pudo capturar una foto de la fuente {self.fuente!r}"
                        break
                    time.sleep(0.1)
                    continue
                fallos = 0
                leidos += 1
                imagen = _rotar(imagen, self.camara_id)
                with self._condicion:
                    self._frame = imagen
                    self._numero += 1
                    self._condicion.notify_all()
                # Una webcam necesita unos frames para ajustar exposición y balance de blancos: los primeros salen oscuros.
                if leidos >= (12 if isinstance(self.fuente, int) else 1):
                    self._listo.set()
        finally:
            self._vivo = False
            self._camara.release()
            self._listo.set()
            with self._condicion:
                self._condicion.notify_all()

    def esperar_frame(self, despues_de: int, timeout: float = 5.0) -> tuple[int, np.ndarray] | None:
        """Bloquea hasta que haya un frame posterior al número `despues_de`; `None` si no llega o la cámara se cerró."""
        self._ultimo_pedido = time.monotonic()
        with self._condicion:
            self._condicion.wait_for(lambda: self._numero > despues_de or not self._vivo, timeout=timeout)
            if self._numero <= despues_de or self._frame is None:
                return None
            return self._numero, self._frame.copy()

    def ultimo_frame(self) -> np.ndarray:
        self._ultimo_pedido = time.monotonic()
        if not self._listo.wait(timeout=10.0) or self._frame is None:
            raise RuntimeError(self._error or f"No se pudo capturar una foto de la fuente {self.fuente!r}")
        return self._frame.copy()


_capturas: dict[str, _CapturaContinua] = {}


def _captura_continua(fuente: int | str, camara: str = "principal") -> _CapturaContinua:
    """La captura en curso de `camara` para `fuente`; abre una nueva si no hay, murió o cambió la fuente.

    Dos cámaras no pueden usar la misma fuente a la vez (la segunda no podría abrirla).
    """
    actual = _capturas.get(camara)
    if actual is not None and (not actual.vivo or actual.fuente != fuente):
        actual.detener()
        _capturas.pop(camara, None)
        actual = None
    if actual is None:
        otra = next((c for n, c in _capturas.items() if n != camara and c.vivo and c.fuente == fuente), None)
        if otra is not None:
            raise RuntimeError(f"La fuente {fuente!r} ya la usa la cámara {otra.camara_id}: elegí una distinta")
        actual = _capturas[camara] = _CapturaContinua(fuente, camara)
    return actual


def _capturar_sin_cerrojo(fuente: int | str | None, camara: str = "principal") -> np.ndarray:
    """Cuerpo de `capturar_foto_tablero` sin captura continua, para comprobar que una fuente abre (con el cerrojo ya tomado)."""
    fuente_resuelta = fuente if fuente is not None else _fuente_por_defecto(camara)
    camara_cv = _abrir_con_limite(fuente_resuelta)
    try:
        if not camara_cv.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara/fuente {fuente_resuelta!r}")
        # Sin esto, algunas cámaras virtuales (ej. Iriun Webcam) devuelven un frame con un formato/tiling raro en vez de la
        # imagen completa — pedir la resolución explícitamente antes de leer lo evita.
        camara_cv.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        camara_cv.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        # Una webcam USB necesita unos frames para ajustar exposición y balance de blancos: los primeros salen oscuros.
        ultima = None
        for _ in range(12 if isinstance(fuente_resuelta, int) else 1):
            exito, imagen = camara_cv.read()
            if exito:
                ultima = imagen
        if ultima is not None:
            return ultima
        for _intento in range(3):
            exito, imagen = camara_cv.read()
            if exito:
                return imagen
            time.sleep(0.3)
        raise RuntimeError(f"No se pudo capturar una foto de la fuente {fuente_resuelta!r}")
    finally:
        camara_cv.release()


def flujo_de_frames(fuente: int | str | None = None, camara: str = "principal"):
    """Generador de frames BGR EN VIVO de `camara`: cada uno es el más reciente (se saltea los que no dio tiempo a procesar).

    Termina si la cámara deja de entregar imágenes. Lanza `RuntimeError` al inicio si no se puede abrir.
    """
    validar_camara(camara)
    fuente_resuelta = fuente if fuente is not None else _fuente_por_defecto(camara)
    if fuente_resuelta is None:
        raise RuntimeError(f"La cámara {camara} no está configurada")
    with _CERROJO_CAMARA:
        captura = _captura_continua(fuente_resuelta, camara)
        captura.ultimo_frame()  # espera el primer frame (y la exposición) con el cerrojo, una sola vez
    ultimo = 0
    while True:
        entrega = captura.esperar_frame(ultimo)
        if entrega is None:
            return
        ultimo, frame = entrega
        yield frame


def soltar_camara(camara: str | None = None) -> None:
    """Cierra la captura continua de `camara` (o de todas) para que otra fuente pueda abrirla; se reabre sola al pedir una foto."""
    for nombre in ([camara] if camara else list(_capturas)):
        captura = _capturas.pop(nombre, None)
        if captura is not None:
            captura.detener()


def fuente_actual(camara: str = "principal") -> int | str | None:
    return _fuente_por_defecto(camara)


def seleccionar_fuente(fuente: int | str, camara: str = "principal") -> int | str:
    """Cambia la fuente de `camara` al instante y la recuerda entre reinicios (`ARCHIVO_PREFERENCIA`). No toca el `.env`.

    Raises:
        ValueError: si la otra cámara ya usa esa fuente.
        RuntimeError: si la fuente no se puede abrir (se deja la anterior).
    """
    validar_camara(camara)
    for otra in CAMARAS:
        if otra != camara and _fuente_por_defecto(otra) is not None and str(_fuente_por_defecto(otra)) == str(fuente):
            raise ValueError(f"La cámara {otra} ya usa esa fuente: elegí una distinta para cada una")
    with _CERROJO_CAMARA:
        soltar_camara(camara)
        previa = _leer_preferencia().get(camara, {}).get("fuente")
        _guardar_preferencia(camara, fuente=str(fuente))
        try:
            _capturar_sin_cerrojo(_fuente_por_defecto(camara), camara)  # comprueba que abre y entrega imagen
        except RuntimeError:
            _guardar_preferencia(camara, fuente=previa)
            raise
    return _fuente_por_defecto(camara)


def quitar_camara_secundaria() -> None:
    """Deja de usar la cámara secundaria (vuelve a trabajar solo con la principal)."""
    with _CERROJO_CAMARA:
        soltar_camara("secundaria")
        _guardar_preferencia("secundaria", fuente=None)
        os.environ.pop("CAMARA_FUENTE_2", None)


CUADROS_PARA_MINIATURA = 6
ANCHO_MINIATURA = 240
SEGUNDOS_MAXIMOS_POR_CAMARA = 6.0
"""Lo más que se espera a probar UNA cámara al buscarlas. Una cámara virtual (Iriun, DroidCam) o un driver trabado puede no contestar nunca,
y como la búsqueda tiene tomado el cerrojo de la cámara, una sola colgada dejaba trabada también la vista en vivo."""


def _probar_indice(indice: int, resultado: dict) -> None:
    """Abre la cámara `indice` (con el mismo backend que usa la app, `CAMARA_API`) y lee un cuadro; deja `ancho`/`alto` en `resultado` si lo logra."""
    api = _api_por_defecto()
    camara = cv2.VideoCapture(indice, api) if api is not None else cv2.VideoCapture(indice)
    try:
        if not camara.isOpened():
            return
        # Una webcam necesita unos cuadros para ajustar la exposición: los primeros salen oscuros. Se leen unos pocos y se queda el último.
        ultima = None
        for _ in range(CUADROS_PARA_MINIATURA):
            exito, imagen = camara.read()
            if exito:
                ultima = imagen
        if ultima is None:
            return
        resultado["ancho"], resultado["alto"] = int(ultima.shape[1]), int(ultima.shape[0])
        alto_mini = max(1, round(ultima.shape[0] * ANCHO_MINIATURA / ultima.shape[1]))
        exito, jpg = cv2.imencode(".jpg", cv2.resize(ultima, (ANCHO_MINIATURA, alto_mini)), [cv2.IMWRITE_JPEG_QUALITY, 70])
        if exito:
            resultado["miniatura"] = "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode("ascii")
    finally:
        camara.release()


def listar_camaras(maximo: int = 6) -> list[dict]:
    """Prueba los índices de cámara 0..`maximo`-1 (webcam, la del Mac, el iPhone por Continuidad…): cuáles abren, a qué resolución y cómo se ven.

    Es rápido a propósito: todas las cámaras se prueban A LA VEZ (cada una en su hilo) y se espera hasta `SEGUNDOS_MAXIMOS_POR_CAMARA` en total, así que
    tarda lo que tarde la más lenta y no la suma de todas. La que no contesta a tiempo se da por no disponible, en vez de dejar colgada la búsqueda
    (y con ella la vista en vivo). Cada cámara disponible trae su `miniatura` (un JPEG chico como data URL), para reconocerla sin pedir una foto por cámara."""
    medidas = [dict() for _ in range(maximo)]
    with _CERROJO_CAMARA:
        soltar_camara()
        hilos = [threading.Thread(target=_probar_indice, args=(i, medidas[i]), daemon=True, name=f"probar-camara-{i}") for i in range(maximo)]
        for hilo in hilos:
            hilo.start()
        limite = time.monotonic() + SEGUNDOS_MAXIMOS_POR_CAMARA
        for indice, hilo in enumerate(hilos):
            hilo.join(timeout=max(0.0, limite - time.monotonic()))
            if hilo.is_alive():
                logger.warning("La cámara %s no contestó en %.0f s: se la da por no disponible", indice, SEGUNDOS_MAXIMOS_POR_CAMARA)
    return [
        {"indice": i, "disponible": "ancho" in m, "ancho": m.get("ancho"), "alto": m.get("alto"), "miniatura": m.get("miniatura")}
        for i, m in enumerate(medidas)
    ]


SEGUNDOS_MAXIMOS_POR_MINIATURA = 20.0
"""Lo más que se espera a la foto de una cámara al mostrar las miniaturas. Una cámara virtual (la de un programa que no está abierto) puede
abrir y no entregar nunca una imagen: sin este límite, esa foto dejaba tomado el cerrojo y la pantalla se quedaba en «Mirando la cámara N…»."""


def foto_de_indice(indice: int) -> np.ndarray:
    """Un cuadro de la cámara `indice` (para reconocer cuál es cuál al elegir); no cambia la cámara en uso.

    Raises:
        RuntimeError: si no se pudo abrir, no entregó imagen o no contestó en `SEGUNDOS_MAXIMOS_POR_MINIATURA`."""
    with _CERROJO_CAMARA:
        soltar_camara()
        resultado: dict = {}

        def tomar() -> None:
            try:
                resultado["imagen"] = _capturar_sin_cerrojo(indice)
            except Exception as error:  # noqa: BLE001 — se vuelve a lanzar en el hilo que llama
                resultado["error"] = error

        hilo = threading.Thread(target=tomar, daemon=True, name=f"miniatura-camara-{indice}")
        hilo.start()
        hilo.join(timeout=SEGUNDOS_MAXIMOS_POR_MINIATURA)
        if hilo.is_alive():
            raise RuntimeError(f"La cámara {indice} no entregó una imagen en {SEGUNDOS_MAXIMOS_POR_MINIATURA:.0f} s")
        if "error" in resultado:
            raise resultado["error"]
        return resultado["imagen"]
