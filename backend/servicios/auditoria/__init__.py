"""Registro de auditoría de las acciones de los administradores."""
from backend.servicios.auditoria.servicio_auditoria import depurar_antiguos, listar_eventos, registrar

__all__ = ["depurar_antiguos", "listar_eventos", "registrar"]
