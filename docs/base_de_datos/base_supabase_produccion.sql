-- =====================================================================
-- BASE DE PRODUCCIÓN PARA SUPABASE - Sistema Tutor Inteligente de Ajedrez
-- Mismas tablas y columnas que base_completa.sql (generado desde
-- backend/modelos/tablas_orm.py con docs/base_de_datos/generar_esquema.py).
-- No editar a mano: cambiar el modelo y volver a generar.
--
-- Cómo usarlo: Supabase > SQL Editor > New query > pegar todo > Run.
-- Es idempotente: se puede ejecutar más de una vez sin duplicar ni romper nada.
--
-- Diferencias con la base de desarrollo (ninguna cambia el comportamiento):
--   1. CREATE ... IF NOT EXISTS y una sola transacción (todo o nada).
--   2. Índices extra en las claves foráneas (partida.usuario_id, jugada.partida_id).
--   3. Seguridad: Row Level Security activado en todas las tablas y sin acceso para
--      los roles anon y authenticated. Supabase expone por defecto una API REST sobre
--      el esquema public; sin esto, quien tenga la clave pública podría leer la tabla
--      usuario (incluidos los hashes de contraseña). El backend no usa esa API: se
--      conecta directamente como 'postgres', que no se ve afectado por RLS.
--   4. NO se cargan usuarios de prueba. El primer facilitador se crea registrándose
--      en la aplicación (ver docs/base_de_datos/DESPLIEGUE_SUPABASE.md).
-- =====================================================================

BEGIN;

-- auditoria: Registro de lo que hacen los administradores sobre cuentas, instituciones y códigos de invitación: quién, qué y a quién. Sin datos personales; se conserva 1 año.
CREATE TABLE IF NOT EXISTS auditoria (
	id SERIAL NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	actor_id INTEGER, 
	accion VARCHAR NOT NULL, 
	objetivo_tipo VARCHAR, 
	objetivo_id INTEGER, 
	detalle VARCHAR, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_auditoria_accion ON auditoria (accion);
CREATE INDEX IF NOT EXISTS ix_auditoria_actor_id ON auditoria (actor_id);
CREATE INDEX IF NOT EXISTS ix_auditoria_creado_en ON auditoria (creado_en);

-- institucion: Institución cliente de la plataforma (asociación, colegio u organizador de torneos). Agrupa a sus docentes y tiene un administrador, que es quien administra su suscripción.
CREATE TABLE IF NOT EXISTS institucion (
	id SERIAL NOT NULL, 
	nombre VARCHAR NOT NULL, 
	tipo VARCHAR DEFAULT 'academia' NOT NULL, 
	administrador_id INTEGER, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (nombre)
);

-- participante: Estudiantes de un curso, para sesiones del facilitador.
CREATE TABLE IF NOT EXISTS participante (
	id SERIAL NOT NULL, 
	nombre VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);

-- sesion: Sesión de clase que abre un facilitador, con su dificultad y tipo de oponente.
CREATE TABLE IF NOT EXISTS sesion (
	id SERIAL NOT NULL, 
	facilitador VARCHAR, 
	fecha TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	dificultad INTEGER, 
	tipo_oponente VARCHAR, 
	PRIMARY KEY (id)
);

-- usuario: Personas que usan el sistema, con su rol, su nivel de juego y su perfil.
CREATE TABLE IF NOT EXISTS usuario (
	id SERIAL NOT NULL, 
	email VARCHAR NOT NULL, 
	nombre VARCHAR NOT NULL, 
	password_hash VARCHAR, 
	rol VARCHAR DEFAULT 'jugador' NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	activo BOOLEAN NOT NULL, 
	google_id VARCHAR, 
	avatar_url VARCHAR, 
	nivel_estimado INTEGER, 
	rango_estimado VARCHAR, 
	edad INTEGER, 
	descripcion VARCHAR, 
	preset_ensenanza VARCHAR, 
	institucion_id INTEGER, 
	correo_verificado BOOLEAN DEFAULT 'true' NOT NULL, 
	codigo_verificacion_hash VARCHAR, 
	codigo_verificacion_expira TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(institucion_id) REFERENCES institucion (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_usuario_email ON usuario (email);
CREATE INDEX IF NOT EXISTS ix_usuario_google_id ON usuario (google_id);
CREATE INDEX IF NOT EXISTS ix_usuario_institucion_id ON usuario (institucion_id);

-- calibracion: Una fila por partida terminada que sirvió para medir el nivel del jugador. Con las últimas tres se calcula su nivel vigente.
CREATE TABLE IF NOT EXISTS calibracion (
	id SERIAL NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	partida_id VARCHAR NOT NULL, 
	precision_global FLOAT NOT NULL, 
	nivel_partida INTEGER NOT NULL, 
	rango_partida VARCHAR NOT NULL, 
	total_jugadas INTEGER NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_calibracion_usuario_partida UNIQUE (usuario_id, partida_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_calibracion_usuario_id ON calibracion (usuario_id);

-- codigo_invitacion: Códigos de un solo uso, con vencimiento, que un facilitador genera para que otra persona pueda registrarse como facilitador. Solo se guarda su hash.
CREATE TABLE IF NOT EXISTS codigo_invitacion (
	id SERIAL NOT NULL, 
	codigo_hash VARCHAR NOT NULL, 
	creado_por INTEGER, 
	para VARCHAR, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	expira_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	usado_en TIMESTAMP WITHOUT TIME ZONE, 
	usado_por INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(creado_por) REFERENCES usuario (id), 
	FOREIGN KEY(usado_por) REFERENCES usuario (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_codigo_invitacion_codigo_hash ON codigo_invitacion (codigo_hash);
CREATE INDEX IF NOT EXISTS ix_codigo_invitacion_creado_por ON codigo_invitacion (creado_por);
CREATE INDEX IF NOT EXISTS ix_codigo_invitacion_usado_por ON codigo_invitacion (usado_por);

-- curso: Grupo de estudiantes de un facilitador. Los jugadores entran con un código del curso, que sirve para muchos y el facilitador puede volver a ver y regenerar.
CREATE TABLE IF NOT EXISTS curso (
	id SERIAL NOT NULL, 
	nombre VARCHAR NOT NULL, 
	facilitador_id INTEGER NOT NULL, 
	codigo VARCHAR NOT NULL, 
	acepta_ingresos BOOLEAN DEFAULT 'true' NOT NULL, 
	cupo INTEGER DEFAULT '60' NOT NULL, 
	preset_ensenanza VARCHAR, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(facilitador_id) REFERENCES usuario (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_curso_codigo ON curso (codigo);
CREATE INDEX IF NOT EXISTS ix_curso_facilitador_id ON curso (facilitador_id);

-- exportacion_dataset: Una fila por cada vez que un facilitador descargó el dataset de partidas para reentrenar el modelo propio (HU4, todavía pendiente de construir).
CREATE TABLE IF NOT EXISTS exportacion_dataset (
	id SERIAL NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	corte_en TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	cantidad_partidas INTEGER NOT NULL, 
	cantidad_jugadas INTEGER NOT NULL, 
	formato VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_exportacion_dataset_usuario_id ON exportacion_dataset (usuario_id);

-- medalla_ganada: Medallas (figuritas) que cada jugador ya ganó, con la fecha. Son permanentes. El catálogo de medallas vive en el código (`backend/servicios/medallas/catalogo.py`).
CREATE TABLE IF NOT EXISTS medalla_ganada (
	id SERIAL NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	codigo VARCHAR NOT NULL, 
	ganada_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	vista_en TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_medalla_ganada_usuario_codigo UNIQUE (usuario_id, codigo), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_medalla_ganada_usuario_id ON medalla_ganada (usuario_id);

-- medalla_personalizada: Medallas que crean los facilitadores (nombre, descripción, nivel, pieza, regla para ganarla y alcance). Las 13 medallas de fábrica no están acá: viven en el código.
CREATE TABLE IF NOT EXISTS medalla_personalizada (
	id SERIAL NOT NULL, 
	codigo VARCHAR NOT NULL, 
	nombre VARCHAR NOT NULL, 
	descripcion VARCHAR NOT NULL, 
	nivel VARCHAR NOT NULL, 
	pieza VARCHAR DEFAULT 'peon' NOT NULL, 
	regla VARCHAR NOT NULL, 
	meta INTEGER DEFAULT '1' NOT NULL, 
	general BOOLEAN DEFAULT 'false' NOT NULL, 
	creada_por INTEGER NOT NULL, 
	creada_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(creada_por) REFERENCES usuario (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_medalla_personalizada_codigo ON medalla_personalizada (codigo);
CREATE INDEX IF NOT EXISTS ix_medalla_personalizada_creada_por ON medalla_personalizada (creada_por);

-- mensaje_tutor: Historial de la conversación de cada jugador con el tutor Turing (memoria multi-turno).
CREATE TABLE IF NOT EXISTS mensaje_tutor (
	id SERIAL NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	rol VARCHAR NOT NULL, 
	contenido VARCHAR NOT NULL, 
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_mensaje_tutor_usuario_id ON mensaje_tutor (usuario_id);

-- partida: Cada partida jugada: quién la jugó, contra qué oponente, a qué nivel, la posición actual y las funciones opcionales que el facilitador habilitó.
CREATE TABLE IF NOT EXISTS partida (
	id VARCHAR NOT NULL, 
	usuario_id INTEGER, 
	participante_id INTEGER, 
	sesion_id INTEGER, 
	fecha TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	resultado VARCHAR, 
	tipo VARCHAR NOT NULL, 
	fen VARCHAR NOT NULL, 
	fen_inicial VARCHAR, 
	nivel INTEGER NOT NULL, 
	tipo_oponente VARCHAR NOT NULL, 
	jugadas_uci VARCHAR NOT NULL, 
	permite_simulacion_3d BOOLEAN DEFAULT 'false' NOT NULL, 
	permite_camara BOOLEAN DEFAULT 'false' NOT NULL, 
	es_demostracion BOOLEAN DEFAULT 'false' NOT NULL, 
	usa_brazo BOOLEAN DEFAULT 'false' NOT NULL, 
	estado VARCHAR DEFAULT 'en_curso' NOT NULL, 
	iniciada_en TIMESTAMP WITHOUT TIME ZONE, 
	actualizada_en TIMESTAMP WITHOUT TIME ZONE, 
	control_tiempo_ms INTEGER DEFAULT '0' NOT NULL, 
	tiempo_blancas_ms INTEGER, 
	tiempo_negras_ms INTEGER, 
	tiempos_jugadas_ms VARCHAR DEFAULT '' NOT NULL, 
	codigo_sala VARCHAR, 
	usuario_blancas_id INTEGER, 
	usuario_negras_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id), 
	FOREIGN KEY(participante_id) REFERENCES participante (id), 
	FOREIGN KEY(sesion_id) REFERENCES sesion (id), 
	FOREIGN KEY(usuario_blancas_id) REFERENCES usuario (id), 
	FOREIGN KEY(usuario_negras_id) REFERENCES usuario (id)
);

-- inscripcion: Un jugador dentro de un curso. Un jugador está en un solo curso a la vez.
CREATE TABLE IF NOT EXISTS inscripcion (
	id SERIAL NOT NULL, 
	curso_id INTEGER NOT NULL, 
	usuario_id INTEGER NOT NULL, 
	unido_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(curso_id) REFERENCES curso (id), 
	UNIQUE (usuario_id), 
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_inscripcion_curso_id ON inscripcion (curso_id);

-- jugada: Detalle de cada jugada de una partida: quién la decidió y cómo la evaluó Stockfish.
CREATE TABLE IF NOT EXISTS jugada (
	id SERIAL NOT NULL, 
	partida_id VARCHAR NOT NULL, 
	numero INTEGER NOT NULL, 
	fen_antes VARCHAR NOT NULL, 
	movimiento VARCHAR NOT NULL, 
	decidido_por VARCHAR, 
	tiempo_calculo_ms INTEGER, 
	explicacion VARCHAR, 
	evaluacion_cp INTEGER, 
	mate_en INTEGER, 
	evaluacion_mejor_cp INTEGER, 
	mate_en_mejor INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(partida_id) REFERENCES partida (id)
);

-- Claves foráneas entre tablas que se apuntan entre sí
ALTER TABLE institucion DROP CONSTRAINT IF EXISTS fk_institucion_administrador;
ALTER TABLE institucion ADD CONSTRAINT fk_institucion_administrador FOREIGN KEY (administrador_id) REFERENCES usuario (id);

-- Índices extra para consultas por clave foránea
CREATE INDEX IF NOT EXISTS ix_partida_usuario_id ON partida (usuario_id);
CREATE INDEX IF NOT EXISTS ix_jugada_partida_id ON jugada (partida_id);

-- Comentarios de tablas y columnas (visibles en pgAdmin y en el editor de Supabase)
COMMENT ON TABLE auditoria IS 'Registro de lo que hacen los administradores sobre cuentas, instituciones y códigos de invitación: quién, qué y a quién. Sin datos personales; se conserva 1 año.';
COMMENT ON COLUMN auditoria.id IS 'Identificador del evento.';
COMMENT ON COLUMN auditoria.creado_en IS 'Fecha y hora del evento.';
COMMENT ON COLUMN auditoria.actor_id IS 'Usuario que hizo la acción. Sin clave foránea: el registro sobrevive a la baja de su cuenta. Nulo si fue el sistema.';
COMMENT ON COLUMN auditoria.accion IS 'Qué se hizo, por ejemplo usuario.eliminar, usuario.baja_propia o institucion.crear.';
COMMENT ON COLUMN auditoria.objetivo_tipo IS 'Sobre qué se hizo: usuario, institucion o codigo_invitacion.';
COMMENT ON COLUMN auditoria.objetivo_id IS 'Identificador de lo afectado. Sin clave foránea, por la misma razón que actor_id.';
COMMENT ON COLUMN auditoria.detalle IS 'Texto corto sin datos personales (por ejemplo, los campos cambiados o el rol). Opcional.';
COMMENT ON TABLE institucion IS 'Institución cliente de la plataforma (asociación, colegio u organizador de torneos). Agrupa a sus docentes y tiene un administrador, que es quien administra su suscripción.';
COMMENT ON COLUMN institucion.id IS 'Identificador de la institución.';
COMMENT ON COLUMN institucion.nombre IS 'Nombre de la institución (por ejemplo, Asociación Departamental de Ajedrez de Santa Cruz). Es único.';
COMMENT ON COLUMN institucion.tipo IS 'academia (academias y clubes), colegio u organizador (de torneos y exhibiciones).';
COMMENT ON COLUMN institucion.administrador_id IS 'Usuario que administra la institución. FK → usuario.id. Nulo hasta que se designa uno.';
COMMENT ON COLUMN institucion.creado_en IS 'Fecha y hora de creación.';
COMMENT ON TABLE participante IS 'Estudiantes de un curso, para sesiones del facilitador.';
COMMENT ON COLUMN participante.id IS 'Identificador del participante.';
COMMENT ON COLUMN participante.nombre IS 'Nombre del participante.';
COMMENT ON TABLE sesion IS 'Sesión de clase que abre un facilitador, con su dificultad y tipo de oponente.';
COMMENT ON COLUMN sesion.id IS 'Identificador de la sesión.';
COMMENT ON COLUMN sesion.facilitador IS 'Nombre del facilitador que la abrió.';
COMMENT ON COLUMN sesion.fecha IS 'Fecha y hora de la sesión.';
COMMENT ON COLUMN sesion.dificultad IS 'Nivel de dificultad de la sesión, de 0 a 20.';
COMMENT ON COLUMN sesion.tipo_oponente IS 'motor, modelo o participante.';
COMMENT ON TABLE usuario IS 'Personas que usan el sistema, con su rol, su nivel de juego y su perfil.';
COMMENT ON COLUMN usuario.id IS 'Identificador del usuario.';
COMMENT ON COLUMN usuario.email IS 'Correo del usuario; es único y se usa para iniciar sesión.';
COMMENT ON COLUMN usuario.nombre IS 'Nombre visible.';
COMMENT ON COLUMN usuario.password_hash IS 'Hash de la contraseña (nunca se guarda en claro). Nulo si entra solo con Google.';
COMMENT ON COLUMN usuario.rol IS 'jugador o facilitador.';
COMMENT ON COLUMN usuario.creado_en IS 'Fecha y hora de registro.';
COMMENT ON COLUMN usuario.activo IS 'Indica si la cuenta está habilitada.';
COMMENT ON COLUMN usuario.google_id IS 'Identificador de Google cuando entra con su cuenta de Google.';
COMMENT ON COLUMN usuario.avatar_url IS 'Ruta o URL de la foto de perfil.';
COMMENT ON COLUMN usuario.nivel_estimado IS 'Nivel de juego vigente, de 0 a 20 (la escala del Skill Level de Stockfish). Lo recalcula el sistema al terminar cada partida, o lo fija el jugador como punto de partida desde su perfil.';
COMMENT ON COLUMN usuario.rango_estimado IS 'Principiante (niveles 0-6), Intermedio (7-13) o Avanzado (14-20).';
COMMENT ON COLUMN usuario.edad IS 'Edad, de uso libre en el perfil.';
COMMENT ON COLUMN usuario.descripcion IS 'Biografía o trayectoria breve, de uso libre en el perfil.';
COMMENT ON COLUMN usuario.preset_ensenanza IS 'Tono con el que Turing habla: infantil, estandar o adultos. Lo elige el facilitador.';
COMMENT ON COLUMN usuario.institucion_id IS 'Institución a la que pertenece un facilitador (docente o administrador). FK → institucion.id. Nulo en los jugadores, cuya institución se deduce de su curso, y en los facilitadores de la plataforma.';
COMMENT ON COLUMN usuario.correo_verificado IS 'true si el correo está verificado. Las cuentas anteriores y las de Google lo están; una cuenta nueva con contraseña queda en false solo si el servidor tiene configurado el envío de correo.';
COMMENT ON COLUMN usuario.codigo_verificacion_hash IS 'Huella SHA-256 del código de 6 dígitos enviado al correo. El código en claro no se guarda.';
COMMENT ON COLUMN usuario.codigo_verificacion_expira IS 'Fecha y hora a partir de la cual el código de verificación ya no sirve (15 minutos).';
COMMENT ON TABLE calibracion IS 'Una fila por partida terminada que sirvió para medir el nivel del jugador. Con las últimas tres se calcula su nivel vigente.';
COMMENT ON COLUMN calibracion.id IS 'Identificador de la calibración.';
COMMENT ON COLUMN calibracion.usuario_id IS 'Jugador medido.';
COMMENT ON COLUMN calibracion.partida_id IS 'Partida que se usó para medir. No es clave foránea a propósito: una partida puede vivir solo en memoria y no tener fila en partida.';
COMMENT ON COLUMN calibracion.precision_global IS 'Precisión del jugador en esa partida, de 0 a 100: compara solo sus jugadas con las de Stockfish.';
COMMENT ON COLUMN calibracion.nivel_partida IS 'Nivel (0-20) que saldría mirando solamente esa partida.';
COMMENT ON COLUMN calibracion.rango_partida IS 'Rango que saldría mirando solamente esa partida.';
COMMENT ON COLUMN calibracion.total_jugadas IS 'Cantidad de jugadas del jugador que se evaluaron (mínimo 5 para registrarla).';
COMMENT ON COLUMN calibracion.creado_en IS 'Fecha y hora en que se registró.';
COMMENT ON TABLE codigo_invitacion IS 'Códigos de un solo uso, con vencimiento, que un facilitador genera para que otra persona pueda registrarse como facilitador. Solo se guarda su hash.';
COMMENT ON COLUMN codigo_invitacion.id IS 'Identificador del código.';
COMMENT ON COLUMN codigo_invitacion.codigo_hash IS 'Huella SHA-256 del código. El código en claro no se guarda: solo se ve al generarlo.';
COMMENT ON COLUMN codigo_invitacion.creado_por IS 'Facilitador que lo generó.';
COMMENT ON COLUMN codigo_invitacion.para IS 'A quién se lo dio (texto libre, opcional), solo para acordarse.';
COMMENT ON COLUMN codigo_invitacion.creado_en IS 'Fecha y hora en que se generó.';
COMMENT ON COLUMN codigo_invitacion.expira_en IS 'Fecha y hora a partir de la cual el código ya no sirve.';
COMMENT ON COLUMN codigo_invitacion.usado_en IS 'Fecha y hora en que se usó. Nulo mientras no se haya usado: sirve una sola vez.';
COMMENT ON COLUMN codigo_invitacion.usado_por IS 'Usuario que se registró con este código.';
COMMENT ON TABLE curso IS 'Grupo de estudiantes de un facilitador. Los jugadores entran con un código del curso, que sirve para muchos y el facilitador puede volver a ver y regenerar.';
COMMENT ON COLUMN curso.id IS 'Identificador del curso.';
COMMENT ON COLUMN curso.nombre IS 'Nombre del curso, que elige el facilitador (por ejemplo, Ajedrez 1ro A).';
COMMENT ON COLUMN curso.facilitador_id IS 'Facilitador dueño del curso. FK → usuario.id.';
COMMENT ON COLUMN curso.codigo IS 'Código de 6 caracteres (alfabeto sin ambiguos) que el facilitador le da a la clase. Es único, sirve para muchos jugadores y se guarda en claro porque solo permite entrar al curso como jugador.';
COMMENT ON COLUMN curso.acepta_ingresos IS 'Si es falso, el código no deja entrar a nadie más; los ya inscritos siguen.';
COMMENT ON COLUMN curso.cupo IS 'Cantidad máxima de jugadores que pueden inscribirse, de 1 a 120 (60 por defecto). Lo define quien crea el curso.';
COMMENT ON COLUMN curso.preset_ensenanza IS 'Tono con el que Turing les habla a los jugadores de este curso: infantil, estandar o adultos. Nulo = se usa el tono que eligió el facilitador en su perfil.';
COMMENT ON COLUMN curso.creado_en IS 'Fecha y hora de creación.';
COMMENT ON TABLE exportacion_dataset IS 'Una fila por cada vez que un facilitador descargó el dataset de partidas para reentrenar el modelo propio (HU4, todavía pendiente de construir).';
COMMENT ON COLUMN exportacion_dataset.id IS 'Identificador de la descarga.';
COMMENT ON COLUMN exportacion_dataset.usuario_id IS 'Facilitador que descargó el dataset.';
COMMENT ON COLUMN exportacion_dataset.creado_en IS 'Fecha y hora en que se generó la descarga.';
COMMENT ON COLUMN exportacion_dataset.corte_en IS 'Instante de corte de esta descarga: la fecha más reciente entre las partidas incluidas. Sirve para saber qué partidas son "nuevas" en la próxima descarga.';
COMMENT ON COLUMN exportacion_dataset.cantidad_partidas IS 'Cuántas partidas incluyó esta descarga.';
COMMENT ON COLUMN exportacion_dataset.cantidad_jugadas IS 'Cuántas jugadas del jugador incluyó esta descarga.';
COMMENT ON COLUMN exportacion_dataset.formato IS 'Formato del archivo entregado, por ejemplo pgn+csv.';
COMMENT ON TABLE medalla_ganada IS 'Medallas (figuritas) que cada jugador ya ganó, con la fecha. Son permanentes. El catálogo de medallas vive en el código (`backend/servicios/medallas/catalogo.py`).';
COMMENT ON COLUMN medalla_ganada.id IS 'Identificador de la medalla ganada.';
COMMENT ON COLUMN medalla_ganada.usuario_id IS 'Jugador que la ganó. FK → usuario.id.';
COMMENT ON COLUMN medalla_ganada.codigo IS 'Código de la medalla en el catálogo (por ejemplo primer_peon). Único por jugador.';
COMMENT ON COLUMN medalla_ganada.ganada_en IS 'Fecha y hora en que la ganó.';
COMMENT ON COLUMN medalla_ganada.vista_en IS 'Cuándo se le mostró el aviso de nueva medalla. Nulo = todavía no se le avisó.';
COMMENT ON TABLE medalla_personalizada IS 'Medallas que crean los facilitadores (nombre, descripción, nivel, pieza, regla para ganarla y alcance). Las 13 medallas de fábrica no están acá: viven en el código.';
COMMENT ON COLUMN medalla_personalizada.id IS 'Identificador de la medalla.';
COMMENT ON COLUMN medalla_personalizada.codigo IS 'Código único y estable (propia_ y 8 caracteres): es la clave de las medallas ganadas y de su imagen.';
COMMENT ON COLUMN medalla_personalizada.nombre IS 'Nombre corto de la medalla.';
COMMENT ON COLUMN medalla_personalizada.descripcion IS 'Frase que dice cómo se gana.';
COMMENT ON COLUMN medalla_personalizada.nivel IS 'bronce, plata, oro o legendaria: define el color y el brillo del escudo.';
COMMENT ON COLUMN medalla_personalizada.pieza IS 'Silueta de pieza que se muestra mientras no tenga imagen propia.';
COMMENT ON COLUMN medalla_personalizada.regla IS 'Cómo se gana: una regla automática (por ejemplo victorias) o manual.';
COMMENT ON COLUMN medalla_personalizada.meta IS 'Cantidad que hay que alcanzar con la regla (1 en las manuales y en las de sí o no).';
COMMENT ON COLUMN medalla_personalizada.general IS 'Verdadero: la ven todos los jugadores (solo el facilitador principal puede crearlas así). Falso: solo los de los cursos de su creador.';
COMMENT ON COLUMN medalla_personalizada.creada_por IS 'Facilitador que la creó. FK → usuario.id.';
COMMENT ON COLUMN medalla_personalizada.creada_en IS 'Fecha y hora de creación.';
COMMENT ON TABLE mensaje_tutor IS 'Historial de la conversación de cada jugador con el tutor Turing (memoria multi-turno).';
COMMENT ON COLUMN mensaje_tutor.id IS 'Identificador del mensaje.';
COMMENT ON COLUMN mensaje_tutor.usuario_id IS 'Jugador que conversa con el tutor.';
COMMENT ON COLUMN mensaje_tutor.rol IS 'user (lo escribió el jugador) o assistant (respondió Turing).';
COMMENT ON COLUMN mensaje_tutor.contenido IS 'Texto del mensaje.';
COMMENT ON COLUMN mensaje_tutor.creado_en IS 'Fecha y hora del mensaje.';
COMMENT ON TABLE partida IS 'Cada partida jugada: quién la jugó, contra qué oponente, a qué nivel, la posición actual y las funciones opcionales que el facilitador habilitó.';
COMMENT ON COLUMN partida.id IS 'Identificador de la partida: un UUID en hexadecimal (texto), no un número.';
COMMENT ON COLUMN partida.usuario_id IS 'Dueño de la partida. Nulo en partidas antiguas o de prueba sin dueño.';
COMMENT ON COLUMN partida.participante_id IS 'Reservado para sesiones de clase. Hoy siempre nulo.';
COMMENT ON COLUMN partida.sesion_id IS 'Reservado para sesiones de clase. Hoy siempre nulo.';
COMMENT ON COLUMN partida.fecha IS 'Fecha y hora de creación.';
COMMENT ON COLUMN partida.resultado IS '1-0, 0-1 o 1/2-1/2. Nulo mientras la partida sigue en curso o no se terminó.';
COMMENT ON COLUMN partida.tipo IS 'digital (se juega en pantalla) o fisica (con el tablero real detectado por visión).';
COMMENT ON COLUMN partida.fen IS 'Posición actual del tablero en notación FEN.';
COMMENT ON COLUMN partida.fen_inicial IS 'Posición desde la que arrancó la partida (FEN). Nulo en partidas antiguas.';
COMMENT ON COLUMN partida.nivel IS 'Nivel del oponente elegido al crear la partida, de 0 a 20. No cambia durante la partida.';
COMMENT ON COLUMN partida.tipo_oponente IS 'motor (Stockfish) o modelo (Turing, el modelo propio).';
COMMENT ON COLUMN partida.jugadas_uci IS 'Todas las jugadas en notación UCI separadas por espacio; permite reconstruir el tablero.';
COMMENT ON COLUMN partida.permite_simulacion_3d IS 'El facilitador habilitó la simulación 3D para esta partida.';
COMMENT ON COLUMN partida.permite_camara IS 'El facilitador habilitó la cámara del tablero físico para esta partida.';
COMMENT ON COLUMN partida.es_demostracion IS 'Partida que el facilitador transmite en vivo a la clase. Solo una a la vez en todo el sistema.';
COMMENT ON COLUMN partida.usa_brazo IS 'La respuesta del oponente también se ejecuta en el brazo robótico (o su simulador).';
COMMENT ON COLUMN partida.estado IS 'esperando, en_curso, terminada o abandonada. Lo administra el ciclo de vida de la partida. esperando = sala creada, falta segundo jugador (solo humano).';
COMMENT ON COLUMN partida.iniciada_en IS 'Momento de la primera jugada del jugador humano. Nulo si todavía no jugó ninguna.';
COMMENT ON COLUMN partida.actualizada_en IS 'Momento de la última jugada aplicada (humano o estrategia). Nulo hasta la primera jugada.';
COMMENT ON COLUMN partida.control_tiempo_ms IS 'Control de tiempo: milisegundos que tiene cada lado para toda la partida. 0 = sin reloj. Se elige al crear la partida.';
COMMENT ON COLUMN partida.tiempo_blancas_ms IS 'Tiempo que le queda al jugador humano (blancas), en milisegundos. Nulo = todavía tiene el control completo. Permite reanudar la partida con el reloj donde se quedó.';
COMMENT ON COLUMN partida.tiempo_negras_ms IS 'Tiempo que le queda al rival (negras), en milisegundos. Nulo = todavía tiene el control completo.';
COMMENT ON COLUMN partida.tiempos_jugadas_ms IS 'Milisegundos que tardó cada jugada, separados por espacio y en el mismo orden que las jugadas; - si no se midió. Alimenta el tiempo por jugada del análisis y la retroalimentación.';
COMMENT ON COLUMN partida.codigo_sala IS 'Código corto de 6 caracteres para compartir la sala (alfabeto sin ambiguos). Único donde no es nulo (índice único parcial). Solo partidas tipo_oponente = ''humano''.';
COMMENT ON COLUMN partida.usuario_blancas_id IS 'Usuario que juega con blancas en la sala. Nulo si el asiento está libre. FK → usuario.id.';
COMMENT ON COLUMN partida.usuario_negras_id IS 'Usuario que juega con negras en la sala. Nulo si el asiento está libre. FK → usuario.id.';
COMMENT ON TABLE inscripcion IS 'Un jugador dentro de un curso. Un jugador está en un solo curso a la vez.';
COMMENT ON COLUMN inscripcion.id IS 'Identificador de la inscripción.';
COMMENT ON COLUMN inscripcion.curso_id IS 'Curso en el que está el jugador. FK → curso.id.';
COMMENT ON COLUMN inscripcion.usuario_id IS 'Jugador inscrito. Único: está en un solo curso a la vez. FK → usuario.id.';
COMMENT ON COLUMN inscripcion.unido_en IS 'Fecha y hora en que entró al curso.';
COMMENT ON TABLE jugada IS 'Detalle de cada jugada de una partida: quién la decidió y cómo la evaluó Stockfish.';
COMMENT ON COLUMN jugada.id IS 'Identificador de la jugada.';
COMMENT ON COLUMN jugada.partida_id IS 'Partida a la que pertenece.';
COMMENT ON COLUMN jugada.numero IS 'Número de la jugada dentro de la partida (1, 2, 3...). Las impares son del jugador humano.';
COMMENT ON COLUMN jugada.fen_antes IS 'Posición (FEN) justo antes de jugarla.';
COMMENT ON COLUMN jugada.movimiento IS 'Jugada en notación UCI, por ejemplo e2e4.';
COMMENT ON COLUMN jugada.decidido_por IS 'Quién la jugó: jugador, motor o modelo.';
COMMENT ON COLUMN jugada.tiempo_calculo_ms IS 'Milisegundos que tardó en calcularse. Nulo si no se midió.';
COMMENT ON COLUMN jugada.explicacion IS 'Explicación en lenguaje natural de la jugada. Nulo si no se generó.';
COMMENT ON COLUMN jugada.evaluacion_cp IS 'Evaluación de Stockfish, en centipeones, del resultado de la jugada realmente jugada.';
COMMENT ON COLUMN jugada.mate_en IS 'Si tras la jugada hay mate forzado, en cuántas jugadas. Nulo si no lo hay.';
COMMENT ON COLUMN jugada.evaluacion_mejor_cp IS 'Evaluación, en centipeones, de la mejor jugada posible en esa posición.';
COMMENT ON COLUMN jugada.mate_en_mejor IS 'Mate forzado disponible con la mejor jugada. Nulo si no lo había.';

-- Seguridad: Row Level Security sin políticas = nadie accede por la API de Supabase.
DO $$
DECLARE
    tabla text;
    rol text;
BEGIN
    FOREACH tabla IN ARRAY ARRAY['auditoria', 'institucion', 'participante', 'sesion', 'usuario', 'calibracion', 'codigo_invitacion', 'curso', 'exportacion_dataset', 'medalla_ganada', 'medalla_personalizada', 'mensaje_tutor', 'partida', 'inscripcion', 'jugada'] LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', current_schema(), tabla);
    END LOOP;
    FOREACH rol IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rol) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', current_schema(), rol);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', current_schema(), rol);
        END IF;
    END LOOP;
END $$;

COMMIT;

-- Verificación: las 7 tablas deben aparecer con rowsecurity = true.
SELECT tablename, rowsecurity FROM pg_tables
WHERE schemaname = current_schema() AND tablename IN ('auditoria', 'institucion', 'participante', 'sesion', 'usuario', 'calibracion', 'codigo_invitacion', 'curso', 'exportacion_dataset', 'medalla_ganada', 'medalla_personalizada', 'mensaje_tutor', 'partida', 'inscripcion', 'jugada')
ORDER BY tablename;
