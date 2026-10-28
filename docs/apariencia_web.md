# Apariencia de la web

La interfaz tiene modo Día, Noche y Automático. Día es el valor inicial de las cuentas nuevas y de las anteriores a esta función. Automático sigue la preferencia del dispositivo y responde si cambia mientras la web está abierta.

El botón de sol/luna de la cabecera alterna rápidamente entre día y noche. En **Mi Perfil → Apariencia** se elige cualquiera de los tres modos. También se puede cambiar antes de iniciar sesión; la preferencia del visitante es independiente de la de cada cuenta.

## Guardado

`usuario.tema` admite `dia`, `noche` y `automatico`. `PATCH /auth/me` guarda la selección y las respuestas de perfil e inicio de sesión la devuelven. La migración automática de `backend/database.py` agrega la columna al arrancar sobre una base anterior.

La web conserva una copia local identificada por usuario para mostrar la apariencia desde el primer dibujo. Al entrar a una cuenta, su preferencia del servidor tiene prioridad. El cambio se muestra inmediatamente; si falla el guardado, aparece un aviso con Reintentar. Una petición que termina después de cambiar de cuenta no modifica la nueva sesión.

Solo elegir un modo hace una petición de guardado. El modo automático y los dibujos de los gráficos no hacen sondeos al backend.

## Cobertura visual

Los colores de día y noche se definen en `frontend/src/index.css`. Las pestañas de jugador, docente, administrador de institución y plataforma usan esos mismos colores. Login y registro, perfil, aulas, partidas, análisis, tablas, formularios, avisos y modales siguen la elección global.

Los gráficos SVG usan variables CSS. Las escenas Three.js actualizan sus colores y materiales al cambiar la apariencia sin volver a cargar los modelos. El simulador cambia fondo, suelo, cuadrícula y casillas; las vistas neuronales ajustan también la mezcla de partículas para mantenerlas visibles sobre un fondo claro.

Las imágenes de cámara, videos, códigos QR, logos y el arte de las medallas ganadas mantienen sus colores propios. Sus contenedores, títulos, estados y controles sí cambian de apariencia. Las medallas bloqueadas adaptan también su escudo gris, candado y barra de avance a la superficie elegida.

## Verificación

Desde `frontend/`:

```powershell
npm.cmd run test:apariencia
npm.cmd run lint
npm.cmd run build
```

Las pruebas de colores calculan el contraste de textos, botones, avisos y leyendas contra las superficies de ambos modos. El mínimo de las combinaciones comprobadas es 4,5:1.

Pruebas de cuenta y actualización de bases anteriores, desde la raíz:

```powershell
python -m pytest backend/rutas/test_ruta_auth.py backend/test_database.py -q
```

Para comprobar la interfaz con Chrome o Edge instalado, iniciar Vite en un puerto de ensayo:

```powershell
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

En otra terminal, desde la raíz:

```powershell
node scripts/verificar_apariencia.mjs
```

El guion utiliza un perfil temporal de navegador y respuestas de API simuladas. Comprueba cambios de modo, recarga, seguimiento del dispositivo, errores de guardado, separación entre cuentas y vistas de jugador/facilitador. Verifica anchos de 360, 768 y 1920 píxeles y deja capturas en `.tmp-apariencia/`, que no se versiona. No conecta con el brazo ni abre una cámara.

Incluye partidas cargadas contra Turing y Stockfish en el análisis de ambos perfiles, además del repaso del jugador. Comprueba que los nombres se dibujen con espacio visible y un contraste mínimo de 4,5:1, tanto en filas seleccionadas como sin seleccionar, en día y noche. También carga videos y medallas para revisar sus títulos, estados y detalles.

Las paletas de colores seleccionables quedan para una etapa posterior.
