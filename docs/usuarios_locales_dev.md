# Usuarios de la base de datos local (desarrollo)

No hace falta crear nada a mano en pgAdmin ni en Docker. `backend/database.py::sembrar_usuarios_iniciales`
crea estos 4 usuarios automáticamente la primera vez que el backend arranca contra una base vacía
(los crea solo si no existen todavía, así que correrlo de nuevo no duplica nada). Alcanza con:

1. Tener Postgres corriendo (local con pgAdmin, o `docker compose -f deploy/docker-compose.yml up`).
2. Copiar el `.env` que te paso por WhatsApp a la raíz del repo (tiene `DATABASE_URL` apuntando a
   tu Postgres).
3. Levantar el backend una vez (`uvicorn backend.main:app --reload`) — los usuarios aparecen solos.

## Usuarios que se crean automáticamente

| Email | Contraseña | Rol | Nombre |
|---|---|---|---|
| `facilitador@test.com` | `admin123` | facilitador | Facilitador Árbitro |
| `jugador@test.com` | `test123456` | jugador | Jugador Aspirante |
| `jugador@chessia.test` | `test123456` | jugador | Jugador ChessIA |
| `suarezburgoshebert@gmail.com` | `admin123` | facilitador | Hebert Suarez Burgos |

Son credenciales de desarrollo, ya están en el código fuente tal cual (`database.py`, tracked en
git) — no son un secreto nuevo, documentarlas acá no expone nada que no estuviera ya en el repo.

## Cómo funciona el rol facilitador (por si hace falta agregar otro correo)

Dos mecanismos, en `backend/servicios/auth/servicio_auth.py`:

- **Código de invitación** (la forma normal): un facilitador lo genera en Gestión de Usuarios →
  "Invitar a un nuevo facilitador" y se lo da a la persona nueva, que lo escribe al registrarse como
  facilitador, con correo o con Google. Sirve una sola vez y vence a los 15 minutos por defecto. Ya no
  existe el PIN fijo `admin123` ni hay correos autorizados de fábrica.
- **Lista blanca de correos** (`CORREOS_FACILITADORES` en `.env`, acepta patrones como
  `*@miuniversidad.edu`): esos correos se registran como facilitador sin código. Sirve para crear el
  primer facilitador de una instalación nueva; las cuentas ya creadas no se ven afectadas. Si alguien
  se registra por primera vez con Google y elige "Facilitador", también necesita código (o estar en
  esta lista): sin eso el sistema le pide el código en vez de crearla como jugador.
- Las cuentas de demo de abajo ya existen en la base, así que siguen entrando normalmente. Para
  crear una cuenta de facilitador nueva en desarrollo, usá un código o agregá el correo a
  `CORREOS_FACILITADORES`.

## Login con Google — falta un Client ID real

`GOOGLE_CLIENT_ID` en `.env` hoy es un valor de relleno (`...dummyclientid...`). Para que el botón
de Google funcione de verdad hace falta crear un OAuth Client ID real en Google Cloud Console
(APIs & Services → Credentials → Create OAuth Client ID → Web application), con la URL del
frontend como "Authorized JavaScript origin", y pegar ese ID en el `.env` real (el que se comparte
por WhatsApp, no este archivo).

## Otras cuentas que puede haber en la base (no automáticas, no hace falta replicarlas)

Durante las pruebas de esta sesión quedaron cuentas de prueba sueltas (`qa-jugador2@test.com`,
cuentas `qa_panel_*@test.com`, y una cuenta real de Google usada para probar el login,
`pedro.gonzalez@gmail.com`) — son datos de prueba, no seed automático, no hace falta recrearlas en
otra máquina.
