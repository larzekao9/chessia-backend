# Propuesta de valor y defensa del emprendimiento

Notas para la Expociencia (modalidad Emprendimiento-Robótica) y para la defensa. Las cifras de precios y costos son los **supuestos del plan de
emprendimiento (CANVAS)**: se validan con la prueba piloto, no son datos medidos. Lo que dicen los competidores se verificó el 25/10/2026 en
sus páginas oficiales; si cambia, hay que volver a mirar.

## 1. La pregunta difícil: «¿y por qué no usan Lichess, que es gratis?»

Hay que reconocer lo que ya existe, porque el tribunal lo va a decir:

- **Lichess Class** (gratis) permite al docente crear cuentas para sus alumnos sin correo, activar el modo niño (chat restringido) y seguir su
  progreso en partidas y problemas. [Fuente](https://lichess.org/page/class).
- **ChessKid** (de Chess.com) ofrece a escuelas grupos, tareas, boletines de progreso, planes de clase con videos y fichas, tablas de posiciones
  entre cursos y escuelas, y descuentos por volumen. [Fuente](https://support.chesskid.com/article/427-coach-school-what-are-the-special-features-available-to-schools-groups).

Entonces **no competimos en cantidad de contenido ni en gestión de cursos en pantalla**: ahí lo gratis ya cumple. Lo que ChessIA ofrece y esos
servicios no son:

| Lo que ofrece ChessIA | Por qué importa a una institución |
|---|---|
| **Un rival físico en las exhibiciones**: el chico juega sobre un tablero real y el brazo robótico (de la universidad) mueve las piezas de Turing. El brazo **no forma parte de la suscripción**: la institución contrata el software | Cambia la clase: pantallas apagadas, un objeto que atrae la atención. Existen tableros con robot de consumo (Square Off, en la Tabla 1 del CANVAS); lo nuestro suma la capa pedagógica y la de la institución |
| **Competir entre compañeros, con justicia y sin chat**: retos dentro del curso, amigos por código y rival de nivel parecido, con reloj y la regla de no abandonar | Los chicos se enganchan jugando entre ellos (no solo contra la máquina) y el docente los ve en vivo; todo en un entorno cerrado |
| **Un rival propio, explicable y medido** (Turing: Elo ≈ 1808 con búsqueda, intervalo 1737 a 1878) con panel de Razonamiento Neuronal | El docente puede mostrar *por qué* el rival eligió esa jugada; no es un motor ajeno con otro nombre |
| **Antes y después por alumno, curso e institución**, con informe imprimible para la familia | Responde lo que el director y los padres preguntan: «¿mi hijo mejoró?». Se mide contra su propio diagnóstico |
| **Acompañamiento local**: alta asistida, capacitación del docente, soporte y cobro en bolivianos | La institución no tiene que armar nada sola; hay alguien a quien llamar |
| **Entorno cerrado** (ver la ficha pública `/#confianza`) | Sin chat entre alumnos, cada institución ve solo lo suyo, baja de cuenta, registro de acciones |
| **Aviso de riesgo de bajas** (salud de los clientes) | Se detecta a tiempo a la institución que paga pero no usa |

Frase para la defensa: *«Lichess y ChessKid enseñan en pantalla; ChessIA lleva el ajedrez a la mesa, con un rival que explica sus jugadas y un
informe que le demuestra a la familia que su hijo mejoró.»*

## 2. Rentabilidad (supuestos del CANVAS)

- Precios: Bs 350 al mes hasta 60 alumnos, Bs 500 al mes hasta 120; trimestral con 10 % de descuento; anual paga 10 de 12 meses.
- Costos fijos: Bs 830 al mes (infraestructura en la nube Bs 351). **Equilibrio con 3 suscripciones**, también con los descuentos anuales.
- El riesgo no es llegar al equilibrio, es **depender de pocas instituciones**: una que se va pesa un tercio. Por eso el anual por adelantado (caja
  segura), el informe de antes y después (razón para renovar) y la salud de los clientes (llamar antes de que se vayan).
- El brazo es de la universidad y se usa en exhibiciones y en la feria; **no está incluido en lo que paga una institución** (la suscripción es solo el
  software). Sirve como gancho para que una institución pida probar el sistema.
- Palancas que no cambian el producto: capacitación docente paga, exhibiciones y torneos como servicio, plan a medida para más de 120 alumnos.
- Lo que falta para que sea creíble y no una proyección: **una institución piloto con el antes y el después medidos** y la encuesta de
  disposición a pagar. Sin eso, los precios siguen siendo una hipótesis.

## 3. Lo que el tribunal puede preguntar

- *¿El modelo depende de Stockfish?* No: cuando es el rival, Turing decide solo. Stockfish es la regla con la que se mide (Elo, acierto,
  errores graves).
- *¿Aprende de los chicos en vivo?* No. Se reentrena en lotes controlados y versionados.
- *¿Qué tan fuerte es?* Elo ≈ 1355 la red sola y ≈ 1808 con búsqueda, **relativo a Stockfish a 0,2 s por jugada**, 120 partidas por medición. No es un Elo
  oficial y no se promete igualar a Stockfish.
- *¿Qué probaron y no funcionó?* Una red de valor: evalúa mejor pero, dentro de la búsqueda, perdió; se descartó con el mismo criterio con que se
  aceptan las mejoras.
- *¿Y los datos de los menores?* Ver la política y la ficha de confianza: autorización de la familia o del colegio, nombres abreviados, sin
  publicidad, baja de cuenta. Una limitación honesta: el tutor usa un servicio externo para redactar sus respuestas.
- *¿Quién paga y cómo?* La institución, por suscripción; hoy el cobro es manual (la plataforma confirma el pago). No hay cobro automático ni
  facturación todavía.

## 4. Qué no está construido (para decirlo antes de que lo pregunten)

Ligas y torneos entre colegios, un rival que se adapta para ganar el 50 % de las partidas, cuentas de niños creadas por el docente sin correo,
importación de alumnos por CSV, informe mensual automático por correo, cobro automático y facturación. Están descritos en `docs/plan_sprints.md`
con la razón de cada recorte.
