# Guía de estudio: los cambios de fines de octubre de 2026

> **Esto es para estudiar y defender, NO va en la tesis.** Explica en criollo qué hace cada cosa que se agregó en esta
> tanda, cómo la calcula, dónde está en el código y qué contestar si preguntan. Complementa a
> [`guia_de_estudio_turing.md`](guia_de_estudio_turing.md) (el modelo) y a
> [`turing_limites_y_plan_v7.md`](turing_limites_y_plan_v7.md) (lo que le falta al modelo y cómo se arregla).
>
> **Estado:** todo esto está hecho y compilado, pero **no se probó con la suite completa ni se subió** (ver el
> final: lista de pruebas manuales y comandos). Los números de acá salen del código; si cambia una constante,
> cambia el número.

**Cómo estudiarla:** una sección por día alcanza. Cada sección termina con **«Si te preguntan…»**.

---

## Mapa rápido

| # | Cambio | Para qué sirve | Dónde vive |
|---|---|---|---|
| 1 | Elo entre personas | Medir quién gana más contra gente real | [`elo.py`](../backend/servicios/social/elo.py) |
| 2 | Búsqueda de rival con filtros | Elegir contra quién jugar (por Elo y por nivel) | [`emparejamiento.py`](../backend/servicios/social/emparejamiento.py) |
| 3 | Reloj del diagnóstico | Que las 3 partidas de medición no se alarguen | `SalaControl.jsx` (`RELOJ_DIAGNOSTICO_MIN`) |
| 4 | Desafío progresivo de Turing | Que Turing suba de fuerza si le ganás seguido | [`desafio.py`](../backend/servicios/calibracion/desafio.py) |
| 5 | Apertura variada de Turing | Que no empiece siempre igual | [`estrategia_jugada.py`](../backend/servicios/estrategias/estrategia_jugada.py) |
| 6 | Arrastrar piezas y jugadas anticipadas | Jugar rápido como en Lichess | `useArrastrePiezas.jsx`, `useAnticipadas.js`, `anticipadas.js` |
| 7 | Tablas y reglas | Terminar bien las partidas (repetición, 50 jugadas, acuerdo, tiempo) | [`partida.py`](../backend/modelos/partida.py), [`servicio_sala.py`](../backend/servicios/sala/servicio_sala.py) |
| 8 | Qué sabe el modelo de las reglas | Responder con honestidad | [`turing_limites_y_plan_v7.md`](turing_limites_y_plan_v7.md) |
| 9 | Partida amistosa o puntuada | Practicar sin miedo a perder puntos | `puntuada` en la partida, [`elo.py`](../backend/servicios/social/elo.py) |
| 10 | Tablero del curso (docente) | Ver el progreso de todos a la vez y mostrarlo a las familias | [`servicio_progreso.py`](../backend/servicios/curso/servicio_progreso.py), `AntesDespuesGrupo.jsx`, `InformeAlumno.jsx` |

---

## 1. Elo entre personas

### La idea
El sistema ya medía un **nivel** (0 a 20): qué tan bien juega alguien comparado con Stockfish. El **Elo** es otra
cosa: mide **resultados contra otras personas**. Si ganás a alguien más fuerte que vos, subís mucho; si perdés
contra alguien más débil, bajás mucho.

| | Nivel (0–20) | Elo (desde 1000) |
|---|---|---|
| Se mide con | La precisión de tus jugadas contra Stockfish | Ganar, empatar o perder contra personas |
| Con quién juegas | Contra Turing o Stockfish | **Solo** contra otras personas |
| Sirve para | Adaptar a Turing a tu nivel y mostrar tu progreso | Ranking y elegir rivales |

### La cuenta (es la fórmula estándar de Elo)
1. **Qué se espera de vos:** `esperado = 1 / (1 + 10^((Elo_rival − Elo_tuyo) / 400))`. Da un número entre 0 y 1: la parte de los puntos que
   deberías sacar. Con el mismo Elo, 0,5.
2. **Tu Elo nuevo:** `nuevo = Elo_tuyo + K × (resultado − esperado)`, donde `resultado` vale 1 si ganás, 0,5 si empatás, 0 si perdés.
3. **K** (cuánto se mueve): **40** mientras llevás menos de 30 partidas (para ubicarte rápido) y **20** después.

### Ejemplos que conviene saber de memoria
- **1000 contra 1000** (K = 40): ganar da **+20** (1020), perder **−20** (980), empatar **0**.
- **1000 contra 1200:** el de 1000 espera sacar 0,24. Si gana: **+30** (1030). Si empata: **+10**. Si pierde: **−10**. El de 1200 hace lo inverso:
  perder contra el de 1000 le cuesta **−30**, empatar **−10**.
- Por eso las tablas **mueven puntos** cuando los Elo son distintos: ver la sección 7 (por qué no se dejan ofrecer al principio).

### Reglas de cuándo cuenta (todas en [`elo.py`](../backend/servicios/social/elo.py))
- Todos **empiezan en 1000**; nunca baja de **100**.
- Es **provisional** hasta las **20 partidas** (se muestra «provisional» en pantalla).
- Cuenta cualquier partida **entre dos personas** que terminó con resultado: reto, búsqueda de rival, enlace o torneo del curso (por mate, tiempo, ausencia, abandono o acuerdo).
- **No cuenta:** contra Turing o Stockfish, partidas canceladas o sin ganador, ni las de **menos de 4 jugadas** (para que abandonar en la primera jugada no regale ni quite puntos).
- **Anti-trampa:** un mismo par de jugadores suma como máximo **5 partidas por día**; las demás se juegan, pero no mueven el Elo.
- **Una sola vez por sala:** la tabla `elo_movimiento` tiene una restricción única (jugador, sala); si las dos pantallas piden anotarla a la vez, la segunda se ignora.

### Dónde se ve
- Insignia en la Sala de Control al lado del perfil, tarjeta «Mi Elo» en el Perfil (con historial), y en las listas de amigos, retos, torneos y partidas de alumnos.
- Al terminar una partida entre personas: «Tu Elo: 1000 → 1020 (+20)».
- API: `GET /social/mi-elo`, y el campo `elo` en la respuesta de la sala.

### Cambios en la base de datos
`usuario.elo` (por defecto 1000), `usuario.partidas_elo` (por defecto 0) y la tabla nueva `elo_movimiento`. Están en `tablas_orm.py`, en `_COLUMNAS_AGREGADAS` de
`database.py` (para que las bases ya creadas reciban las columnas al arrancar) y en `docs/base_de_datos/` (el script `generar_esquema.py` regenera los SQL y el diccionario).

**Si te preguntan «¿por qué Elo y nivel, no es lo mismo?»:** «No. El nivel dice qué tan bien jugás comparado con un motor; el Elo dice cuánto le ganás a otras personas. Uno adapta a Turing, el otro ordena a los jugadores.»
**Si te preguntan «¿por qué empieza en 1000 y no en 0?»:** «Es la convención de plataformas como Lichess o chess.com para novatos; con 20 partidas deja de ser provisional.»
**Si te preguntan «¿se puede hacer trampa?»:** «Está limitado: mínimo de jugadas, 5 partidas por pareja por día, y tablas pactadas solo después de 10 jugadas por lado.»

---

## 2. Búsqueda de rival con filtros

### Cómo funcionaba y cómo funciona
El emparejamiento es una **cola en memoria**. Dos jugadores que buscan a la vez se enfrentan si sus niveles están lo bastante cerca:
- **Sin filtros:** empieza aceptando **1 nivel** de diferencia y suma **1 nivel cada 15 s**, hasta **5**. La búsqueda se rinde a los **90 s**.
- **Con filtros** (nuevo): el jugador puede pedir un rango de **Elo** (`elo_min`/`elo_max`, hasta 3000) y/o de **nivel** (`nivel_min`/`nivel_max`). Ejemplos: «nivel 10 que quiere probar con 12 a 15»,
  «1000 de Elo contra 1500 a 2000».
- **Los dos tienen que aceptarse:** cada uno cumple *lo que él pidió*. Con filtros no se afloja con el tiempo: quien los puso sabe lo que quiere (si no aparece nadie, termina a los 90 s).
- Si un mínimo es mayor que su máximo, responde 400.

Pantalla: tarjeta «Buscar un rival» en Jugar, con el componente [`FiltrosDeBusqueda.jsx`](../frontend/src/componentes/FiltrosDeBusqueda.jsx).

**Si te preguntan «¿qué pasa si nadie cumple el filtro?»:** «Se espera hasta 90 s y se avisa que no apareció nadie; el filtro no se afloja solo porque es una elección del jugador.»

---

## 3. Reloj del diagnóstico

- El **diagnóstico** son las **3 primeras partidas** de cada jugador, contra Stockfish **nivel 8**. Con ellas se calcula el primer nivel (promedio de las últimas 3 calibraciones).
- Pedido del campeón: ponerles reloj para que no se alarguen. Quedó en **10 minutos por lado** (`RELOJ_DIAGNOSTICO_MIN`), fijo durante el diagnóstico.
- Una partida cuenta para calibrar solo si tiene **al menos 5 jugadas del jugador** (con menos, la precisión no dice nada).
- Regla de la práctica: en una partida con reloj, quien lleva **más de 20 segundos sin dar señal de vida** pierde (`SEGUNDOS_AUSENCIA_CON_RELOJ`); si se van los dos, la sala se cierra sin ganador.
  La señal es la consulta de la pantalla (cada 1 s con la pestaña visible, cada 3 s oculta).

**Si te preguntan «¿cómo se calcula el nivel?»:** «Se compara cada jugada del alumno con la que haría Stockfish y se saca una precisión; el promedio de las últimas 3 partidas válidas se traduce a un nivel de 0 a 20 (por ejemplo, ~66 % de precisión es nivel 10 y ~74 % es nivel 12).»

---

## 4. Desafío progresivo de Turing

### El problema
Si Turing juega **siempre a tu nivel exacto**, ganás casi la mitad de las veces y no tenés motivo para mejorar. El campeón lo explicó así: para progresar hay que jugar *un poco por encima* de uno mismo.

### Cómo funciona
- Turing juega al **nivel del jugador + un desafío** de **0 a 3 niveles**.
- El desafío sale de las **últimas 5 partidas válidas contra Turing** (terminadas, con al menos 5 jugadas tuyas): `desafío = victorias − derrotas`, recortado entre 0 y 3. Las tablas no suman ni restan.
- **Tu nivel medido no cambia nunca por esto.** El desafío solo cambia con qué fuerza juega Turing. El techo sigue siendo 18.
- Se calcula **al vuelo** desde tus partidas (no hay columna que mantener): [`desafio.py`](../backend/servicios/calibracion/desafio.py). Llega al cliente en el perfil (`desafio_turing`) y en `GET /auth/nivel`.

| Últimas 5 contra Turing | Victorias − derrotas | Desafío | Con nivel 10, Turing juega |
|---|---|---|---|
| G P T P P | −1 | 0 | 10 |
| G G T P T | +1 | 1 | 11 |
| G G G P T | +2 | 2 | 12 |
| G G G G G | +5 | 3 (tope) | 13 |
| Nivel 17 y desafío 3 | — | 3 | **18** (techo de Turing) |

### En pantalla (Sala de Control)
- Texto: «Turing: nivel 12 (tu nivel 10 + 2 de desafío por tus victorias recientes)».
- Botón **«Desafío activado / apagado»** (se recuerda en el navegador). Apagado, Turing juega siempre a tu nivel.
- Si movés el selector de nivel **a mano** (o retomás una partida), manda lo que elegiste y no se suma desafío hasta que tu perfil se recalibre.
- Solo aplica contra Turing (no contra Stockfish) y fuera del diagnóstico.

### Por qué tiene sentido pedagógico
Es la idea de la **zona de desarrollo próximo** (Vygotsky): lo que aprendés mejor está un poco más allá de lo que ya hacés solo. El desafío sube si ganás (te falta exigencia) y baja si perdés (te pasaste).

**Si te preguntan «¿por qué no subir el nivel directamente si ganás?»:** «Porque el nivel es una *medición* de cómo jugás, no una recompensa. Si ganar lo subiera, el nivel dejaría de decir la verdad. El desafío ajusta al rival sin falsear la medición.»
**Si te preguntan «¿por qué esa regla (victorias − derrotas)?»:** «Es simple y explicable en una frase. Se podría suavizar o ponderar por fuerza del rival; lo dejamos así para poder defenderlo y medirlo.»

---

## 5. Apertura variada de Turing

- **Problema:** en modo maestro Turing es **determinista** (misma posición → misma jugada), así que todas las partidas empezaban igual.
- **Solución:** en las primeras **5 jugadas de cada bando** elige entre las **3 mejores candidatas de su propia red**, con una temperatura baja (la del «nivel de muestreo 16», ≈ 1,2) y la penalidad por pieza colgada. Como la red aprendió de partidas humanas, sus candidatas son aperturas
  reales: siciliana, Petrov, española, francesa… Desde la jugada 6 sigue con su juego de siempre.
- **Sigue decidiendo el modelo** (regla 1 del proyecto): no hay libro de aperturas externo ni Stockfish.
- Para **medir su fuerza** con jugadas siempre iguales se apaga con la variable de entorno `AJEDREZ_TURING_APERTURA_VARIADA=0`.

**Si te preguntan «¿Turing "improvisa"?»:** «Elige entre sus 3 mejores jugadas con un poco de azar controlado solo al principio, para que las partidas no sean idénticas.»

---

## 6. Arrastrar piezas y jugadas anticipadas

### Arrastrar
- Se arrastra con el mouse o el dedo (eventos *pointer*). Un movimiento de más de **5 píxeles** cuenta como arrastre; menos, como clic normal. Al soltar sobre una casilla, equivale a hacer clic en el destino.
- En el celular el tablero no scrollea mientras se arrastra (`touch-action: none`).

### Jugadas anticipadas («premoves»)
- Mientras el rival piensa, podés dejar armadas hasta **7 jugadas** (tope confirmado por el campeón); se hacen **una por turno propio**, apenas te toca.
- Al anticipar **no se sabe qué va a jugar el rival**, así que solo se mira cómo *se mueve* la pieza (los deslizantes ignoran lo que se interponga; el peón puede «capturar» en diagonal una casilla vacía; el rey puede ir dos casillas del origen para el enroque).
- Cuando te toca, **el servidor comprueba si es legal de verdad**. Si no lo es, **se descartan todas**.
- Se cancelan con **clic derecho**, con el botón «Cancelar» o con **Escape** (en el tablero entre personas). Cambiar de partida o terminarla también las borra.
- Funciona en los dos tableros: Sala de Control (contra Turing o Stockfish) y partida entre personas.

**Si te preguntan «¿no se puede hacer trampa anticipando jugadas ilegales?»:** «No: la validación real es del servidor al ejecutarla; la anticipación solo ahorra tiempo de clic.»

---

## 7. Tablas y reglas del ajedrez

### Qué termina una partida hoy
| Causa | Resultado | Cómo se aplica |
|---|---|---|
| Jaque mate | gana el que mató | automático |
| Rey ahogado | tablas | automático |
| Material insuficiente (rey contra rey, etc.) | tablas | automático |
| **Triple repetición** de la posición | tablas | **automático (nuevo)** |
| **50 jugadas** sin captura ni peón | tablas | **automático (nuevo)** |
| 75 jugadas sin progreso / quíntuple repetición | tablas | automático (ya existía) |
| Acuerdo de los dos jugadores | tablas | ofrecer + aceptar |
| Abandono | gana el otro | botón |
| Se acaba el reloj | pierde quien se quedó sin tiempo… | …**salvo** que el rival no tenga material para dar mate: **tablas (nuevo)** |
| Ausencia / inactividad | ver sección 3 | automático |

Lo «automático nuevo» está en [`partida.py`](../backend/modelos/partida.py) (`fin_por_reglas` y `resultado_por_reglas`). Es como chess.com: la triple repetición y las 50 jugadas se aplican solas; en la
regla oficial hay que reclamarlas, pero así no quedan partidas eternas.

### Tablas por acuerdo (entre personas)
- Uno **ofrece**; el otro **acepta o rechaza**. Si acepta, la partida termina `1/2-1/2` con motivo `acuerdo`.
- La oferta **caduca apenas alguien mueve**. Después de ofrecer, hay que esperar **4 jugadas** para volver a hacerlo. Los espectadores no pueden.
- **Nuevo:** no se puede ofrecer hasta que **cada uno lleve 10 jugadas** (`JUGADAS_MINIMAS_PARA_OFRECER_TABLAS = 20` medias jugadas). Sin esto, dos amigos podrían pactar tablas al instante entre Elo distintos para mover puntos sin jugar.
- No se restringe *quién* ofrece. Lo habitual es que ofrezca el que está peor, pero decidir quién está peor exigiría evaluar con Stockfish en cada oferta, y la regla oficial deja ofrecer a cualquiera. **El que decide siempre es el rival.**

### Captura al paso, enroque y coronación
Las valida el motor de reglas (python-chess), no el modelo. La captura al paso solo existe justo después del avance doble del peón rival. El brazo y la visión también las contemplan
(`planificador_movimiento.py`, `seguimiento_jugadas.py`). En pantalla el humano elige la pieza de coronación. En la partida **física** con el brazo, la respuesta del rival (Turing o Stockfish)
se fuerza a dama (`coronar_solo_a_dama`), porque el brazo solo tiene una dama de reserva por bando: es una limitación del hardware, no de las reglas.

**Si te preguntan «¿están todas las reglas?»:** «Sí, las del juego las implementa python-chess. Las que son de *plataforma* (triple repetición automática, tablas pactadas con mínimo de jugadas, tiempo contra material insuficiente) las definimos nosotros.»

---

## 8. Qué sabe el modelo de las reglas (versión corta)

- **Nunca juega ilegal:** la red puntúa las 4096 combinaciones origen-destino y se queda solo con las **jugadas legales** que da python-chess.
- **Aprendió enroques, capturas al paso y coronaciones** porque estaban en las partidas de Lichess con las que se entrenó.
- **Pero la red solo ve las piezas** (8×8×12): no ve derechos de enroque, la casilla de captura al paso, el reloj de 50 jugadas ni las repeticiones, y no distingue coronar a dama de coronar a otra pieza.
- La **búsqueda** de Turing (nivel maestro) sí usa el tablero completo y trata repetición, material insuficiente y 50 jugadas como tablas.
- El detalle, la evidencia en el código y el plan para arreglarlo (v7) están en [`turing_limites_y_plan_v7.md`](turing_limites_y_plan_v7.md).

**Si te preguntan «¿el modelo entiende todas las reglas?»:** «Juega siempre legal y aprendió las jugadas especiales de ejemplos reales. No recibe como entrada algunos datos (derechos de enroque, repetición); eso está medido como límite y el plan para la versión 7 los agrega.»

---

## 9. Partida amistosa o puntuada

- Al **retar**, **buscar rival** o **crear una partida con enlace** se elige el **tipo**: **Puntuada** (mueve el Elo de los dos) o **Amistosa** (se juega igual, pero no mueve el Elo). Por defecto es puntuada.
- Es la misma idea que Lichess: *casual* y *rated*. Quita el incentivo de pactar tablas para no perder puntos: quien quiere practicar sin riesgo juega amistosa.
- **Búsqueda de rival:** una búsqueda amistosa solo se empareja con otra amistosa (si no, uno querría puntuar y el otro no).
- **Torneos:** siempre puntúan.
- La partida lleva su marca (`partida.puntuada`, columna nueva con valor `true` por defecto, así las partidas viejas siguen siendo puntuadas). La pantalla muestra una etiqueta «Amistosa» y, al terminar, «Fue una partida amistosa: no mueve el Elo». Quien recibe un reto amistoso lo ve en el aviso.
- La **revancha** hereda el tipo de la partida anterior.

**Si te preguntan «¿para qué amistosas?»:** «Para que los chicos practiquen sin miedo a perder puntos y para que el ranking quede solo con partidas serias.»

---

## 10. Tablero del curso (lo que ve el docente)

### El problema
Con muchos alumnos, abrir uno por uno no alcanza. El docente necesita ver **a todos a la vez** y saber **a quién mirar hoy**.

### Qué hay (en el reporte del curso y en el de la institución)
Arriba, **cuatro tarjetas**: *Para mirar hoy* (cuántos alumnos necesitan atención), *Al día esta semana* (semáforo de constancia), *Elo promedio* y *Partidas con compañeros*.

Después, tres bloques:
1. **Para mirar hoy:** lista de alumnos con el motivo — nunca jugó, hace 14 días o más que no juega, bajó de nivel desde el diagnóstico, o perdió 3 partidas seguidas con compañeros (puede estar desanimado). Cada uno con su botón «Ver informe».
2. **Para la próxima clase** (en un curso y en el reporte de la institución; en una institución grande lee los primeros 80 alumnos y lo avisa en pantalla): suma las estadísticas de todos y dice en qué **fase de la partida** se acierta menos (apertura, medio juego o final) y cuál es el **error que más se repite**, con una sugerencia fija (por ejemplo «dedicar la próxima clase al medio juego…»). Son frases con reglas, no un modelo de lenguaje, y se aclara que son sugerencias para planificar.
3. **Tres pestañas:**
   - **Aprendizaje:** el antes y después de siempre (nivel, precisión, cambio).
   - **Competencia:** ranking por **Elo** con el cambio de los últimos 30 días y los resultados (G·E·P) en **puntuadas**, **torneos** y **amistosas** por separado.
   - **Constancia:** semáforo por alumno — verde (jugó en los últimos 7 días), amarillo (8 a 13 días), rojo (14 o más) o sin empezar —, partidas del mes y partidas con compañeros.

En el celular las tablas pasan a tarjetas (no hay scroll horizontal).

### El informe de cada alumno (para la familia)
Se agregó el bloque **«Jugando con compañeros»**: su Elo (con «provisional» si son pocas partidas y el cambio en 30 días), lo que ganó/empató/perdió en puntuadas y torneos, y las amistosas aparte. Trae además
un **gráfico de cómo fue cambiando su Elo** (hasta las últimas 30 partidas: círculo lleno = ganó, gris = empató, hueco = perdió) y la lista de sus **últimos 5 torneos con su puesto** («Puesto 2 de 8 · 3,5 puntos»; si el torneo sigue en juego dice «Va 1 de 6»), sin nombrar a nadie más. Las frases de «En pocas palabras» suman, por ejemplo: *«Con compañeros, en partidas puntuadas y torneos, ganó 4, empató 0 y perdió 2. Su Elo es 1068 (provisional…) y subió 68 en los últimos 30 días.»* También entra en el mensaje corto para WhatsApp.

**Decisión de privacidad:** el informe para la familia muestra solo el progreso **de ese alumno** (contra sí mismo); no lista a otros chicos ni un ranking con nombres. El ranking por Elo es una vista del **docente**, y la pantalla le recuerda que no conviene compartirlo con el grupo si no quiere compararlos.

### De dónde salen los números
- Elo y su cambio: tabla `elo_movimiento` (solo partidas que movieron el Elo).
- Resultados con compañeros: las partidas terminadas entre personas con al menos 4 jugadas, clasificadas por tipo (torneo, puntuada, amistosa).
- Constancia: la fecha de la última partida de cada alumno.
- Plan de clase: la precisión por fase y los errores de cada alumno, sumados (se leen hasta 80 alumnos).
- Gráfico del Elo: las filas de `elo_movimiento` del alumno, en orden. Puesto en torneos: la misma tabla de posiciones que ve el aula (`torneos.clasificacion`).

**Si te preguntan «¿cómo sé qué enseñar?»:** «El sistema suma las partidas analizadas de todo el curso y señala la fase donde más se falla y el error que más se repite; sugiere una clase para trabajarlo.»
**Si te preguntan «¿por qué no muestran el ranking a los padres?»:** «Compararlos con otros chicos desmotiva y expone a menores; a la familia se le muestra cuánto mejoró su hijo contra sí mismo.»

---

## Archivos que se tocaron

**Backend**
- Elo: `servicios/social/elo.py` (nuevo), `modelos/tablas_orm.py`, `database.py`, `servicios/sala/servicio_sala.py`, `rutas/ruta_social.py`, `rutas/ruta_sala.py`, `rutas/ruta_auth.py`, `esquemas/{social_esquema,sala,auth_esquema}.py`.
- Filtros: `servicios/social/{emparejamiento,amigos,supervision}.py`.
- Desafío: `servicios/calibracion/{desafio.py (nuevo),servicio_calibracion.py,__init__.py}`, `esquemas/calibracion_esquema.py`.
- Apertura variada: `servicios/estrategias/estrategia_jugada.py`.
- Tablas y reglas: `modelos/partida.py`, `repositorios/repositorio_partida.py`, `servicios/partida/servicio_partida.py`, `servicios/sala/{servicio_sala,analisis_partida_sala}.py`.
- Amistosa o puntuada: `modelos/{partida,tablas_orm}.py`, `database.py`, `servicios/social/{elo,retos,emparejamiento}.py`, `servicios/sala/servicio_sala.py`, `rutas/{ruta_sala,ruta_social}.py`, `esquemas/{sala,social_esquema}.py`.
- Tablero del curso: `servicios/curso/servicio_progreso.py`, `rutas/ruta_progreso.py`, `esquemas/progreso_esquema.py`.
- Base de datos: `docs/base_de_datos/{generar_esquema.py,base_completa.sql,base_supabase_produccion.sql,DICCIONARIO_DE_DATOS.md}`.

**Frontend**
- Elo y filtros: `elo.js`, `componentes/{FiltrosDeBusqueda,InsigniaElo}.jsx`, `paginas/Perfil/TarjetaMiElo.jsx`, `paginas/Jugar/*`, `paginas/SalaControl/SalaControl.jsx`, `api/backend.js`.
- Arrastre y anticipadas: `useArrastrePiezas.jsx`, `useAnticipadas.js`, `anticipadas.js`, `componentes/{TableroJugable,ResumenAnticipadas}.jsx`.
- Desafío: `nivelJugador.js`, `paginas/SalaControl/SalaControl.jsx`.
- Amistosa o puntuada: `paginas/Jugar/{Jugar,PartidaEntrePersonas}.jsx`, `componentes/ModalRetoRecibido.jsx`, `api/backend.js`.
- Tablero del curso: `paginas/Progreso/{AntesDespuesGrupo,InformeAlumno,GraficoElo}.jsx`, `paginas/Progreso/comunes.js`.

**Tests nuevos o ajustados**
- `backend/rutas/test_ruta_elo.py` (los 5 primeros pasaron; se agregaron 2 de partidas amistosas), `backend/servicios/calibracion/test_desafio.py`, `backend/modelos/test_partida_reglas.py`, `backend/servicios/curso/test_progreso_competencia.py`, y `backend/rutas/test_ruta_aula.py` (las pruebas de tablas ahora juegan 21 jugadas antes de ofrecer).

---

## Preguntas probables y respuesta corta

1. **¿Cómo se calcula el Elo?** Fórmula estándar con K = 40 (menos de 30 partidas) o 20, empezando en 1000; solo cuentan partidas entre personas de 4 jugadas o más.
2. **¿Para qué el desafío si el nivel ya se adapta?** Porque adaptarse siempre al nivel exacto estanca; el desafío sube con las victorias y baja con las derrotas, sin falsear la medición.
3. **¿Turing hace trampa o consulta a Stockfish?** No: decide con su red y su búsqueda; Stockfish solo se usa para medirlo y para etiquetar durante el entrenamiento.
4. **¿Qué pasa si dos amigos se ponen de acuerdo para empatar?** No pueden hasta las 10 jugadas por lado, y además el Elo limita a 5 partidas por pareja por día.
5. **¿El modelo sabe enrocar y capturar al paso?** Las jugó en sus datos de entrenamiento y el motor de reglas garantiza que sean legales; la red no recibe como entrada los derechos de enroque (límite declarado).
6. **¿Por qué la triple repetición es automática?** Para no dejar partidas eternas y porque así lo hacen plataformas como chess.com; la regla oficial la deja como reclamo.
7. **¿Qué es lo que no hicimos?** Reentrenar el modelo con más información de entrada, personalidades de juego y un modelo «humano» por franja de Elo (ver `turing_limites_y_plan_v7.md`).

## Cosas que NO hay que decir
- Que el modelo «entiende todas las reglas»: juega legal gracias al motor de reglas, y la red no ve algunos datos.
- Que el Elo reemplaza al nivel: son dos medidas distintas.
- Que el desafío «sube tu nivel»: sube la fuerza de Turing; tu nivel sigue midiéndose por precisión.
- Que «Maestro 18» es una fuerza medida: es la etiqueta de los datos de entrenamiento (ver la hoja de ruta de Turing).

---

## Para probar antes de subir

**Comando de pruebas** (los de mayor riesgo, porque se tocó el núcleo de las reglas):

```
pytest backend/rutas/test_ruta_aula.py backend/rutas/test_ruta_sala.py backend/rutas/test_ruta_tiempo_agotado.py backend/rutas/test_ruta_elo.py backend/servicios/sala backend/modelos/test_partida_reglas.py backend/servicios/calibracion backend/servicios/curso backend/rutas/test_ruta_progreso.py backend/test_database.py
```

Los cambios de base de datos (`partida.puntuada`, y en Elo `usuario.elo`, `usuario.partidas_elo` y la tabla `elo_movimiento`) hacen que `backend/test_database.py` compruebe que los SQL y el diccionario están al día (ya se regeneraron con `python docs/base_de_datos/generar_esquema.py`).

**A mano, en la pantalla**
1. Dos usuarios: jugar una partida corta con reto → al terminar, el Elo cambia (+20/−20 si empezaron en 1000) y aparece «provisional».
2. Intentar «Ofrecer tablas» en la jugada 3: botón deshabilitado con el aviso; a las 10 jugadas por lado, se habilita.
3. Sala de Control contra Turing: ganarle 2 o 3 veces seguidas y ver que aparece «+N de desafío»; apagar el botón y comprobar que vuelve a tu nivel.
4. Anticipar 3 jugadas mientras Turing piensa, y comprobar que se juegan solas y que una ilegal las borra todas.
5. Forzar una triple repetición (mover el caballo ida y vuelta) y comprobar que termina en tablas.
6. En el celular (360 px): filtros de búsqueda, botón del desafío y arrastre de piezas sin scroll horizontal.
7. Retar a alguien eligiendo **Amistosa**: la partida muestra la etiqueta, al terminar no cambia el Elo y quien recibe el reto lo ve en el aviso.
8. Como docente, abrir el reporte de un curso con alumnos que jugaron entre sí: las tarjetas, «Para mirar hoy», «Para la próxima clase» y las tres pestañas (en celular y en escritorio); abrir el informe de un alumno y ver «Jugando con compañeros».
