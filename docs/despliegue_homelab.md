# Actualizar el servidor conectado por Tailscale

Instalación comprobada el 28 de octubre de 2026. Complementa la [guía de Docker](despliegue_nube.md).

## Acceso e instalación actual

- Equipo: `home-lab`, dirección Tailscale `100.99.124.109`.
- SSH: `ssh hebert@100.99.124.109`, con la llave ya autorizada del equipo. Las llaves privadas no se copian al repositorio.
- Aplicación: `/home/hebert/chessia`.
- URL publicada por Tailscale Funnel: <https://home-lab.tail3b5ebb.ts.net>.
- Funnel dirige HTTPS a `http://127.0.0.1:8090`; Docker dirige ese puerto al 8000 del contenedor web.
- Compose utiliza **ambos** archivos: `deploy/docker-compose.yml` y `deploy/docker-compose.homelab.yml`.
- Contenedores: `ajedrez_web` y `ajedrez_db`.

La carpeta del servidor no tiene `.git`: contiene una copia de los archivos. Por eso no se actualiza con `git pull`. La configuración propia está en `deploy/.env` y `deploy/docker-compose.homelab.yml`; esos archivos se conservan en el servidor y no deben reemplazarse con una copia local ni imprimirse en los registros.

## Preparar y respaldar

1. Ejecutar las pruebas relevantes y compilar el frontend. Publicar primero el commit de `frontend/` y después el de la raíz, que referencia ese commit del submódulo.
2. Crear paquetes con `git archive` de los dos commits publicados. El archivo del frontend debe llevar el prefijo `frontend/`. Esto incluye solamente archivos versionados, sin sesiones, secretos, bases locales, `node_modules`, datasets ni checkpoints.
3. En el servidor, crear un directorio de entrega bajo `/home/hebert/chessia-despliegues/`, con fecha y commit, y permisos privados. Guardar allí los paquetes y sus SHA-256, los dos hashes de Git, la imagen anterior y los respaldos.
4. Antes de modificar la aplicación, guardar la base con `docker exec ajedrez_db pg_dump -U ajedrez -d ajedrez -Fc` en un archivo privado del servidor. El comando no necesita mostrar los registros de los usuarios. Confirmar que el respaldo se puede listar con `pg_restore --list`.
5. Etiquetar la imagen actual de `ajedrez_web` como `chessia-web:respaldo-FECHA` y respaldar los archivos de código que se van a reemplazar. Mantener aparte los archivos privados de configuración. No podar imágenes ni eliminar volúmenes durante una actualización.

## Construir y activar

Extraer los paquetes verificados en `/home/hebert/chessia`, conservando los archivos privados y los checkpoints del servidor. El `.dockerignore` de la raíz evita enviar esos archivos al constructor de Docker. La web anterior sigue atendiendo mientras se construye la imagen nueva.

Desde `/home/hebert/chessia`:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.homelab.yml build web
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.homelab.yml up -d --no-deps --no-build web
```

Solo se reemplaza el servicio web. La base y los volúmenes de videos, avatares y medallas conservan su instalación. Al arrancar, el backend agrega las columnas faltantes mediante `backend/database.py`; el cambio de apariencia añade `usuario.tema` con valor inicial `dia`.

## Verificar y recuperar

- Confirmar que `ajedrez_web` quede `healthy`, que `http://127.0.0.1:8090/health` y la URL HTTPS respondan, y que el HTML sirva los recursos de la compilación nueva.
- Comprobar que exista la columna `usuario.tema` y que los conteos de usuarios y partidas sean los mismos que antes de actualizar. Revisar los registros de arranque sin imprimir variables de entorno ni credenciales.
- Probar la respuesta de perfil y el selector de apariencia sin modificar cuentas reales: puede utilizarse una credencial temporal en memoria durante una comprobación controlada de solo lectura.
- Conservar en la entrega un manifiesto con los commits, la imagen activa, los respaldos y el resultado de las comprobaciones.
- Si la nueva web falla, volver a etiquetar la imagen de respaldo como `deploy-web:latest` y repetir `up -d --no-deps --no-build web`; restaurar también el respaldo del código. Una columna nueva como `tema` es compatible con la versión anterior y no exige restaurar toda la base. Un respaldo de base solo se restaura si hay un problema de datos comprobado, porque restaurarlo puede perder actividad posterior al respaldo.

No se modifica el túnel, la configuración de otros servicios del servidor ni el hardware del laboratorio para actualizar la web.
