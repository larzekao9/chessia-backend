"""Para diagnosticar un backend que se pone lento o parece colgarse, y para que el registro de la consola no se llene de consultas repetidas.

- `MedidorDePeticiones` (middleware): mide cuánto tarda cada pedido en EMPEZAR a responder y avisa en el registro cuando pasa de `PETICION_LENTA_S`, y cuando hay demasiados pedidos
  esperando a la vez (con cuáles son). Sirve para ver, la próxima vez que se trabe, qué pedido lo frenó. No toca las respuestas.
- `FiltroAccesoSondeos`: saca del registro de accesos de uvicorn las consultas de estado que las pantallas repiten varias veces por segundo y salen bien (200). Escribir cientos de
  líneas por minuto en la consola de Windows la hace lenta, y si alguien selecciona texto en ella (modo «Edición rápida») el proceso se frena hasta apretar Enter.
- `ajustar_hilos`: sube el máximo de hilos que FastAPI usa para las rutas síncronas (40 por defecto). Si varias quedan esperando (una cámara que tarda en abrir, el brazo moviéndose), las
  demás se encolan y todo parece colgado.
"""
from __future__ import annotations

import logging
import os
import time
from collections import Counter

logger = logging.getLogger("ajedrez.medicion")

PETICION_LENTA_S = float(os.environ.get("PETICION_LENTA_SEGUNDOS", "2"))
"""Tiempo (s) hasta empezar a responder a partir del cual un pedido se registra como lento."""
PETICIONES_SIMULTANEAS_AVISO = int(os.environ.get("PETICIONES_SIMULTANEAS_AVISO", "25"))
"""Cuántos pedidos esperando respuesta a la vez hacen que se avise (lo normal son unos pocos)."""
SEGUNDOS_ENTRE_AVISOS = 5.0
HILOS_MAXIMOS = int(os.environ.get("HILOS_MAXIMOS", "100"))
"""Máximo de hilos para las rutas síncronas (por defecto de anyio: 40)."""

RUTAS_SONDEADAS = (
    "/partida-fisica/estado",
    "/brazo/estado",
    "/entrenamiento/estado",
    "/vision/calibracion/estado",
    "/vision/camaras/estado",
)
"""Consultas de estado que la pantalla repite sin parar: si salen bien, no se anotan en el registro de accesos."""


class FiltroAccesoSondeos(logging.Filter):
    """Descarta del registro de accesos las consultas de `RUTAS_SONDEADAS` que respondieron 200; todo lo demás (y cualquier error) se sigue anotando."""

    def filter(self, record: logging.LogRecord) -> bool:
        mensaje = record.getMessage()
        return not (" 200 " in mensaje and any(f'"GET {ruta}' in mensaje for ruta in RUTAS_SONDEADAS))


class MedidorDePeticiones:
    """Middleware ASGI puro (sirve también para las respuestas en flujo, como el video): cuenta los pedidos que esperan su primera respuesta y registra los lentos."""

    def __init__(self, app, umbral_s: float | None = None, simultaneas: int | None = None) -> None:
        self.app = app
        self.umbral_s = PETICION_LENTA_S if umbral_s is None else umbral_s
        self.simultaneas = PETICIONES_SIMULTANEAS_AVISO if simultaneas is None else simultaneas
        self.esperando: Counter[str] = Counter()
        self._ultimo_aviso = float("-inf")

    def _avisar_si_hay_demasiados(self) -> None:
        total = sum(self.esperando.values())
        ahora = time.monotonic()
        if total >= self.simultaneas and ahora - self._ultimo_aviso >= SEGUNDOS_ENTRE_AVISOS:
            self._ultimo_aviso = ahora
            principales = ", ".join(f"{clave} ×{cantidad}" for clave, cantidad in self.esperando.most_common(5))
            logger.warning("Hay %d pedidos esperando respuesta a la vez; los que más: %s", total, principales)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        clave = f"{scope.get('method', '?')} {scope.get('path', '?')}"
        inicio = time.perf_counter()
        self.esperando[clave] += 1
        contado = True
        self._avisar_si_hay_demasiados()

        def dejar_de_esperar() -> None:
            nonlocal contado
            if contado:
                contado = False
                self.esperando[clave] -= 1
                if self.esperando[clave] <= 0:
                    del self.esperando[clave]

        async def enviar(mensaje) -> None:
            if mensaje["type"] == "http.response.start" and contado:
                demora = time.perf_counter() - inicio
                dejar_de_esperar()
                if demora >= self.umbral_s:
                    logger.warning("Pedido lento: %s tardó %.1f s en empezar a responder (esperando ahora: %d)", clave, demora, sum(self.esperando.values()))
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            dejar_de_esperar()


async def ajustar_hilos(maximo: int | None = None) -> int:
    """Sube a `maximo` (por defecto `HILOS_MAXIMOS`) los hilos que FastAPI usa para las rutas síncronas, sin bajarlos nunca. Se llama dentro del ciclo de eventos (al arrancar)."""
    import anyio.to_thread

    limitador = anyio.to_thread.current_default_thread_limiter()
    limitador.total_tokens = max(int(limitador.total_tokens), HILOS_MAXIMOS if maximo is None else maximo)
    return int(limitador.total_tokens)
