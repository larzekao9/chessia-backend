# Turing: límites con las reglas, plan de reentrenamiento (v7) y personalidades

> **Para qué sirve este documento.** (1) Declara con evidencia qué datos de las reglas del ajedrez NO recibe la red de Turing; sale directo
> a la sección *Limitaciones y trabajo futuro* de la tesis. (2) Es el plan concreto para volver a entrenar y cerrar esas brechas. (3) Responde si
> Turing puede tener «carácter» (atacante, sólido…) y ser más humano. Pensado para pulir el sistema antes de la feria de la facultad (fines de noviembre de 2026).
>
> Las mediciones del modelo están en [`turing_hoja_de_ruta.md`](turing_hoja_de_ruta.md); la explicación en criollo, en
> [`guia_de_estudio_turing.md`](guia_de_estudio_turing.md); lo que se agregó a la plataforma en octubre, en
> [`guia_de_estudio_cambios_octubre_2026.md`](guia_de_estudio_cambios_octubre_2026.md).

---

## 1. Cómo decide hoy Turing (lo que hay que tener claro)

1. **Entrada de la red:** un tensor de **8×8×12**: seis tipos de pieza propios y seis del rival, siempre desde el punto de vista de quien mueve
   (`board_to_tensor`, [`training/data_pipeline.py`](../training/data_pipeline.py)). **Solo hay piezas en casillas.**
2. **Salida de la red:** 4096 puntajes, uno por cada par *casilla de origen → casilla de destino* (`jugada_a_etiqueta`: `origen*64 + destino`).
3. **Legalidad:** la red puede darle puntaje a jugadas imposibles, por eso nunca se toma el máximo global: se miran **solo las jugadas legales** que da
   python-chess (`predecir_jugada`, [`inferencia.py`](../backend/servicios/aprendizaje/inferencia.py)). **Turing no puede jugar una jugada ilegal.**
4. **Nivel maestro (18):** además aplica un filtro táctico (no regalar piezas) y una **búsqueda ligera** de variantes (negamax con poda alfa-beta y búsqueda de capturas,
   [`busqueda_turing.py`](../backend/servicios/aprendizaje/busqueda_turing.py)). Por debajo de 18 muestrea entre las mejores candidatas de la red con una temperatura
   (3,0 en los niveles 0–5, bajando hasta 1,0 en el 17).
5. **Regla 1 del proyecto:** al jugar decide el modelo; Stockfish solo mide y etiqueta durante el entrenamiento.

---

## 2. Brechas con las reglas (evidencia en el código)

| Dato o regla | ¿La **red** lo ve? | ¿Qué lo cubre hoy? | Consecuencia |
|---|---|---|---|
| Posición de las piezas y a quién le toca | Sí | — | — |
| **Derechos de enroque** (torre o rey que ya se movieron) | **No** | python-chess solo ofrece el enroque si sigue permitido; la red lo aprendió de ejemplos | No puede «planear» conservar o perder el derecho; a veces le da puntaje a un enroque que ya no se puede (se descarta) |
| **Captura al paso** (casilla disponible solo un turno) | **No** | python-chess solo la ofrece justo después del avance doble | No sabe que existe esa oportunidad pasajera: puede no jugarla cuando conviene |
| **Contador de 50 jugadas** | **No** | La **búsqueda** lo trata como tablas (≥ 100 medias jugadas) | La red no «siente» que se acerca el empate |
| **Repetición de posiciones** | **No** (la entrada es una sola posición, sin historia) | La búsqueda detecta repeticiones **dentro de las jugadas que explora** | Ver la nota de abajo |
| **Elección de la pieza al coronar** | No: las 4 coronaciones comparten origen y destino, así que **la red les da el mismo puntaje** | En `predecir_jugada` gana la dama por el orden en que python-chess genera las jugadas; la búsqueda enumera todas las legales | Una subcoronación (por ejemplo a caballo para dar jaque con horquilla o evitar un ahogado) no está garantizada. *No verificado si la búsqueda llega a elegirla.* |
| Reloj | No | — | Juega igual con 10 s que con 10 min |

**Nota sobre la repetición (importante por la regla nueva).** La estrategia recibe solo el **FEN** de la posición (`decidir_jugada(fen)`), y un FEN **no trae las jugadas anteriores**.
Entonces la búsqueda sabe del enroque, la captura al paso y el contador de 50 jugadas (están en el FEN) pero **no conoce las posiciones que ya pasaron en la partida real**. Desde octubre
la triple repetición termina la partida en tablas de forma automática, así que Turing puede caer en ella (o no buscarla cuando le conviene) sin darse cuenta. La mejora más barata no
requiere reentrenar: **pasarle el historial de jugadas a la estrategia** (parámetro opcional, aditivo) para que su búsqueda arranque con el tablero completo.

**Qué NO es un límite:** la legalidad. Aunque la red no vea esos datos, el motor de reglas garantiza que ninguna jugada ilegal salga, y la red vio enroques, capturas al paso y coronaciones
reales en las partidas de Lichess con las que se entrenó (todas las jugadas de cada partida son ejemplos de entrenamiento).

**Referencia que respalda el plan:** AlphaZero (Silver et al., 2017) le da a la red, además de las piezas, planos de **derechos de enroque**, **repeticiones**, **contador de jugadas sin progreso**
y de turno; nuestra red es mucho más simple y no los recibe. *(Verificar la cita completa antes de ponerla en la tesis.)*

---

## 3. Plan v7: reentrenar con la información que falta

### 3.1 Qué se agrega a la entrada
De 12 a **20 canales** (cada uno es un plano de 8×8):

| Canales | Contenido |
|---|---|
| 12 (los de hoy) | Piezas propias y del rival, desde el punto de vista de quien mueve |
| +4 | Derechos de enroque: corto propio, largo propio, corto del rival, largo del rival (1 en todo el plano si todavía existe) |
| +1 | Casilla de captura al paso disponible (un 1 solo en esa casilla) |
| +1 | Contador de 50 jugadas normalizado (`halfmove_clock / 100`, constante en todo el plano) |
| +2 | Repetición: «esta posición ya apareció una vez» y «ya apareció dos veces» |

Los de enroque, captura al paso y contador salen del propio FEN. **El de repetición necesita el historial** de la partida (ver la nota de la sección 2).

### 3.2 Qué hay que tocar
- [`training/data_pipeline.py`](../training/data_pipeline.py): `board_to_tensor` debe aceptar un tablero con su historial (al leer las partidas de Lichess ya existe `tablero.push(jugada)`, así que el historial está disponible).
- [`modelo_jugadas.py`](../backend/servicios/aprendizaje/modelo_jugadas.py): la primera capa `nn.Conv2d(12, canales, …)` pasa a `nn.Conv2d(canales_entrada, canales, …)`.
- [`inferencia.py`](../backend/servicios/aprendizaje/inferencia.py): `cargar_modelo` hoy deduce los canales **de salida** de la primera capa; hay que leer también los de **entrada** (12 para v1–v6, 20 para v7) y guardar `canales_entrada` en el checkpoint. Así las versiones viejas siguen cargando.
- [`modelo_valor.py`](../backend/servicios/aprendizaje/modelo_valor.py): la cabeza de valor tiene la misma capa de 12 canales y se extiende igual.
- `decidir_jugada(fen, historial=None)` en la estrategia del modelo: parámetro opcional; si falta, los canales de repetición van en cero (comportamiento actual).

### 3.3 Arrancar desde v6 (no desde cero)
**Expansión de la entrada con pesos en cero:** se copian los pesos de v6 a los 12 primeros canales de la primera capa y los 8 nuevos se inicializan en **0**. La v7 arranca **idéntica** a v6 (mismos
resultados) y el ajuste fino aprende a usar los canales nuevos. Se entrena pocas épocas con tasa de aprendizaje baja, porque v6 ya mostró sobreajuste desde la época 9 (ver la ficha del modelo).
Se hace en Colab (la GPU local no sirve con la versión actual de PyTorch).

### 3.4 Coronación
Dos opciones, de menor a mayor costo:
1. **Primero** (sin reentrenar): que la **búsqueda** compare las cuatro coronaciones con su evaluación y elija la mejor (y no la primera). Medir cuántas veces cambia la decisión.
2. **Después, si hace falta:** una cabeza chica de 4 salidas (dama, torre, alfil, caballo) entrenada solo con las posiciones donde la jugada humana fue una coronación. Hay muy pocas subcoronaciones en las partidas reales, así que probablemente no aporte datos suficientes.

La partida **física** seguirá forzando la dama: el brazo solo tiene una dama de reserva por bando (limitación del hardware, no del modelo).

### 3.5 Cómo se prueba que valió la pena (criterios de aceptación)
1. **No empeora:** la precisión top-1 de validación (por partida entera) no baja respecto de v6 (ver la ficha del modelo).
2. **Juega igual o mejor:** `python -m training.enfrentar_modelos --a <v6.pt> --b <v7.pt>` con el mismo número de partidas, y el Elo contra Stockfish (`training/medir_elo.py`) dentro del intervalo de v6 o por encima.
3. **Mejora dirigida (lo que se muestra en la defensa):** armar dos conjuntos de posiciones de validación, uno donde la captura al paso es legal y otro donde el enroque sigue disponible y es candidato, y medir cuántas veces el modelo elige la jugada que hizo el jugador humano, **v6 contra v7**. Si v7 sube en esos conjuntos, el aporte queda demostrado con un número.
4. **Repetición:** partidas v7 contra Stockfish con historial; contar cuántas terminan en tablas por repetición frente a v6.

### 3.6 Riesgos
- Más canales de entrada no garantizan más fuerza; por eso los criterios anteriores. Si v7 no gana, v6 se conserva (el sistema ya soporta varias versiones).
- Es ajuste fino en Colab: hay que reservar tiempo para el entrenamiento y la medición (no se prometen horas exactas hasta correr una época de prueba).

---

## 4. Personalidad de Turing: ¿se puede? Sí, en dos niveles

La pregunta es si Turing puede jugar con **carácter** (más atacante, más sólido, más posicional) y verse **más humano**.

### 4.1 Nivel A — por inferencia, sin reentrenar (alcanzable antes de la feria)
La red ya devuelve una lista de candidatas ordenadas. La personalidad **reordena esas candidatas** con unas reglas simples sobre la jugada, calculadas con python-chess:

`puntaje = log(probabilidad de la red) + Σ peso_de_estilo × característica_de_la_jugada`

| Característica de la jugada | Atacante | Sólido | Posicional |
|---|---|---|---|
| Da jaque | + | 0 | 0 |
| Captura una pieza | + | − (prefiere no complicar) | 0 |
| Acerca piezas o peones al rey rival | + | 0 | 0 |
| Acepta dejar una pieza «colgada» a cambio de ataque (penalidad más baja) | + | − (penalidad más alta) | 0 |
| Intercambia piezas cuando va por delante | − | + | 0 |
| Enroca pronto y no adelanta los peones de su propio rey | 0 | + | + |
| Controla el centro, desarrolla piezas, torres en columnas abiertas | 0 | 0 | + |

- **Se mantiene el filtro táctico** (no regalar la dama); la personalidad solo elige *entre jugadas ya aceptables*. Sigue decidiendo el modelo + reglas simples; **no entra Stockfish** (regla 1).
- El sistema ya tiene las piezas: temperatura y tamaño del pool por nivel (`temperatura_y_pool_por_nivel`) y la penalidad por pieza colgada. La personalidad es una tercera perilla.
- **Implementación estimada:** 2 a 3 días (módulo de estilo + selector «Carácter de Turing: Equilibrado / Atacante / Sólido / Posicional» en la Sala de Control + una columna `personalidad` en la partida, lo que obliga a actualizar el esquema como pide el repo: `_COLUMNAS_AGREGADAS`, `generar_esquema.py` y los SQL).
- **Cómo se demuestra que son distintas** (lo que valida la idea): jugar 50 partidas por perfil contra el mismo rival y comparar indicadores medibles: porcentaje de jugadas que son capturas, que dan jaque, cuántas jugadas tarda el primer cambio de damas y cuánto avanzan en promedio los peones del lado del rey rival. Si los números no se separan, **no se presenta como personalidad**.
- **Cuidado con la fuerza:** medir también el resultado de cada perfil contra Turing «Equilibrado»; un perfil que pierde mucho sería un rival fácil, no un carácter.

### 4.2 Nivel B — reentrenando (trabajo futuro de la tesis)
- **Por estilo:** ajuste fino con subconjuntos de partidas elegidos con un criterio medible (por ejemplo, partidas con jaques tempranos y sacrificios para el perfil atacante).
- **Más humano por franja de Elo (idea de Maia Chess).** Hoy los niveles bajos se logran con **temperatura**, que produce errores «de máquina» (jugadas al azar entre las candidatas). Una persona de 1200 no falla al azar: falla de formas características. Maia Chess (McIlroy-Young, Sen, Kleinberg y Anderson, KDD 2020) entrenó modelos separados por franja de rating con partidas humanas de esa franja y logró que predijeran jugadas humanas mejor que un motor debilitado. Para Turing sería: un modelo con partidas de 1100–1400 para los niveles bajos, otro de 1500–1800 para los medios, y el actual para los altos. *(Verificar la cita y los datos antes de usarla.)*
  - Requiere **muchas partidas por franja** y tiempo de Colab; con menos datos habrá menos precisión. Es la mejora que más acerca a Turing a «enseñar jugando contra alguien parecido a un humano», pero no es para antes de la feria.

### 4.3 Recomendación
Para la feria de noviembre: **Nivel A** (carácter por inferencia, con la medición que lo respalde) y la **mejora del historial** (sección 2). Dejar la **v7** y el **modelo por franja de Elo** como trabajo futuro, con el plan de este documento.

---

## 5. Orden de trabajo sugerido hasta la feria

| Orden | Qué | Esfuerzo | Resultado mostrable |
|---|---|---|---|
| 1 | Pasar el **historial** a la estrategia del modelo | Chico | Turing deja de ignorar las repeticiones de la partida real |
| 2 | **Búsqueda elige la coronación** (comparar las 4) | Chico | Subcoronación posible |
| 3 | **Personalidades** (Nivel A) + medición de indicadores | 2–3 días | Cuatro caracteres distintos con números |
| 4 | **v7** con 20 canales en Colab (expansión desde v6) | El mayor; depende del entrenamiento | Mejora dirigida en captura al paso y enroque |
| 5 | Modelo por franja de Elo | Futuro | Rival «humano» en niveles bajos |

Cada paso se cierra con su medición y se registra en [`turing_hoja_de_ruta.md`](turing_hoja_de_ruta.md) (el mismo método de siempre: se acepta solo lo que mejora).

---

## 6. Párrafo listo para la tesis (Limitaciones y trabajo futuro)

> La red de política de Turing (versión 6) recibe como entrada únicamente la posición de las piezas (tensor de 8×8×12 visto desde el jugador que mueve), sin los derechos de enroque, la casilla
> de captura al paso, el contador de jugadas sin progreso ni el historial de posiciones. La legalidad de las jugadas está garantizada por el motor de reglas, que filtra las jugadas candidatas
> de la red, y la búsqueda ligera de nivel maestro sí considera el enroque, la captura al paso y la regla de las 50 jugadas por trabajar con el tablero completo del FEN, pero no conoce las
> repeticiones de la partida real porque la estrategia recibe solo la posición actual. Además, la red no distingue entre las cuatro coronaciones posibles. Como trabajo futuro se propone una
> versión 7 con planos de entrada adicionales (derechos de enroque, captura al paso, contador de 50 jugadas y repeticiones), inicializada desde la versión 6 mediante expansión de la primera capa
> con pesos en cero, evaluada con una prueba dirigida sobre posiciones donde esas reglas intervienen; así como personalidades de juego por reponderación de candidatas y modelos específicos
> por franja de rating para reproducir errores humanos en los niveles bajos.
