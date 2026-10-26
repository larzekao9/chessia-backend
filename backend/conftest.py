"""Configuración global de pytest para el backend."""
import os

# Establece entorno de pruebas antes de importar cualquier módulo del backend
os.environ["PYTEST_RUNNING"] = "1"
# Evita que los tests usen la base de datos real (PostgreSQL)
os.environ.pop("DATABASE_URL", None)
# En los tests cualquier correo @test.com puede registrarse como facilitador sin código (la app real no tiene
# ningún correo autorizado de fábrica: se configura con CORREOS_FACILITADORES). Los tests del código de
# invitación lo quitan con monkeypatch.
os.environ["CORREOS_FACILITADORES"] = "*@test.com"
os.environ["PERMITIR_LOGIN_GOOGLE_DEMO"] = "true"  # Solo las pruebas usan tokens ficticios de Google.

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reiniciar_intentos_de_codigos():
    """Cada test arranca con el contador de códigos fallidos en cero (es global al proceso)."""
    from backend.servicios.auth.servicio_invitaciones import reiniciar_intentos

    reiniciar_intentos()
    yield
    reiniciar_intentos()
