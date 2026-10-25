"""Cabeceras de seguridad HTTP y orígenes permitidos (CORS) de la API.

Las cabeceras se agregan con un middleware ASGI puro (no `BaseHTTPMiddleware`): éste último complica las respuestas que no terminan,
como el video en vivo `/vision/flujo`.
"""
from __future__ import annotations

import os

# La política de contenido va en modo «solo informar»: el navegador anota en su consola lo que bloquearía, sin romper nada. Cuando la
# consola esté limpia en todas las pantallas se pasa a `Content-Security-Policy` (ver docs/plan_sprints.md).
POLITICA_DE_CONTENIDO = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'wasm-unsafe-eval' https://accounts.google.com https://apis.google.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' data: blob: https:",
        "media-src 'self' blob:",
        "connect-src 'self' https://accounts.google.com",
        "frame-src https://accounts.google.com",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'self'",
    ]
)

CABECERAS_FIJAS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(), payment=(), usb=()"),
    (b"content-security-policy-report-only", POLITICA_DE_CONTENIDO.encode("latin-1")),
]
HSTS = (b"strict-transport-security", b"max-age=15552000")  # 180 días


class CabecerasSeguridad:
    """Agrega las cabeceras de seguridad a toda respuesta HTTP. HSTS solo si el pedido llegó por HTTPS (directo o detrás de un proxy)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        por_https = scope.get("scheme") == "https" or any(
            nombre == b"x-forwarded-proto" and valor.split(b",")[0].strip() == b"https" for nombre, valor in scope.get("headers", [])
        )

        async def enviar(mensaje) -> None:
            if mensaje["type"] == "http.response.start":
                existentes = {nombre.lower() for nombre, _ in mensaje.get("headers", [])}
                agregadas = [h for h in CABECERAS_FIJAS + ([HSTS] if por_https else []) if h[0] not in existentes]
                mensaje = {**mensaje, "headers": [*mensaje.get("headers", []), *agregadas]}
            await send(mensaje)

        await self.app(scope, receive, enviar)


def origenes_permitidos() -> list[str]:
    """Orígenes que pueden llamar a la API desde un navegador: los de `CORS_ORIGINS` (separados por coma) o `*` si no está definida.

    La web propia se sirve desde el mismo origen que la API y la app móvil no usa CORS, así que en producción conviene definirla con
    los dominios propios."""
    definidos = [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    return definidos or ["*"]
