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
	es_demostracion BOOLEAN DEFAULT 'false' NOT NULL,
	es_prueba BOOLEAN DEFAULT 'false' NOT NULL,
	dominio_correo VARCHAR,
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

-- pago: Pago que una institución avisa haber hecho. Queda pendiente hasta que la plataforma lo confirma; recién ahí se extiende la suscripción.
CREATE TABLE IF NOT EXISTS pago (
	id SERIAL NOT NULL,
	institucion_id INTEGER NOT NULL,
	plan VARCHAR NOT NULL,
	ciclo VARCHAR NOT NULL,
	monto_bs INTEGER NOT NULL,
	estado VARCHAR DEFAULT 'pendiente' NOT NULL,
	referencia VARCHAR,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	resuelto_en TIMESTAMP WITHOUT TIME ZONE,
	resuelto_por INTEGER,
	periodo_hasta TIMESTAMP WITHOUT TIME ZONE,
	nota VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(institucion_id) REFERENCES institucion (id)
);
CREATE INDEX IF NOT EXISTS ix_pago_creado_en ON pago (creado_en);
CREATE INDEX IF NOT EXISTS ix_pago_estado ON pago (estado);
CREATE INDEX IF NOT EXISTS ix_pago_institucion_id ON pago (institucion_id);

-- suscripcion: Suscripción de una institución: su plan (hasta 60 o hasta 120 alumnos), cuándo termina la prueba gratis (30 días por defecto) y hasta cuándo está pagada. El estado (prueba, activa o vencida) se calcula con las fechas.
CREATE TABLE IF NOT EXISTS suscripcion (
	id SERIAL NOT NULL,
	institucion_id INTEGER NOT NULL,
	plan VARCHAR DEFAULT 'plan_60' NOT NULL,
	ciclo VARCHAR DEFAULT 'mensual' NOT NULL,
	prueba_hasta TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	vigente_hasta TIMESTAMP WITHOUT TIME ZONE,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (institucion_id),
	FOREIGN KEY(institucion_id) REFERENCES institucion (id)
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
	correo_verificado_en TIMESTAMP WITHOUT TIME ZONE,
	codigo_verificacion_hash VARCHAR,
	codigo_verificacion_expira TIMESTAMP WITHOUT TIME ZONE,
	recuperacion_password_hash VARCHAR,
	recuperacion_password_expira TIMESTAMP WITHOUT TIME ZONE,
	version_credenciales INTEGER DEFAULT '0' NOT NULL,
	totp_activo BOOLEAN DEFAULT 'false' NOT NULL,
	totp_secreto VARCHAR,
	totp_ultimo_paso INTEGER,
	totp_codigos_respaldo VARCHAR,
	cuenta_vinculada_id INTEGER,
	codigo_amigo VARCHAR,
	tutor_nombre VARCHAR,
	tutor_telefono VARCHAR,
	tutor_email VARCHAR,
	autorizacion_tutor_en TIMESTAMP WITHOUT TIME ZONE,
	cuenta_gestionada BOOLEAN DEFAULT 'false' NOT NULL,
	elo INTEGER DEFAULT '1000' NOT NULL,
	partidas_elo INTEGER DEFAULT '0' NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(institucion_id) REFERENCES institucion (id),
	FOREIGN KEY(cuenta_vinculada_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_usuario_codigo_amigo ON usuario (codigo_amigo);
CREATE UNIQUE INDEX IF NOT EXISTS ix_usuario_email ON usuario (email);
CREATE INDEX IF NOT EXISTS ix_usuario_google_id ON usuario (google_id);
CREATE INDEX IF NOT EXISTS ix_usuario_institucion_id ON usuario (institucion_id);

-- amistad: Amistad entre dos jugadores que no comparten curso (los compañeros de curso no la necesitan para jugar entre sí). Una solicitud queda pendiente hasta que el destinatario la acepta; si la rechaza, la fila se borra.
CREATE TABLE IF NOT EXISTS amistad (
	id SERIAL NOT NULL,
	solicitante_id INTEGER NOT NULL,
	destinatario_id INTEGER NOT NULL,
	estado VARCHAR DEFAULT 'pendiente' NOT NULL,
	creada_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	respondida_en TIMESTAMP WITHOUT TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_amistad_par UNIQUE (solicitante_id, destinatario_id),
	FOREIGN KEY(solicitante_id) REFERENCES usuario (id),
	FOREIGN KEY(destinatario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_amistad_destinatario_id ON amistad (destinatario_id);
CREATE INDEX IF NOT EXISTS ix_amistad_solicitante_id ON amistad (solicitante_id);

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
	institucion_id INTEGER,
	como_administrador BOOLEAN DEFAULT 'false' NOT NULL,
	es_evaluacion BOOLEAN DEFAULT 'false' NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(creado_por) REFERENCES usuario (id),
	FOREIGN KEY(usado_por) REFERENCES usuario (id),
	FOREIGN KEY(institucion_id) REFERENCES institucion (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_codigo_invitacion_codigo_hash ON codigo_invitacion (codigo_hash);
CREATE INDEX IF NOT EXISTS ix_codigo_invitacion_creado_por ON codigo_invitacion (creado_por);
CREATE INDEX IF NOT EXISTS ix_codigo_invitacion_institucion_id ON codigo_invitacion (institucion_id);
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
	permite_retos BOOLEAN DEFAULT 'true' NOT NULL,
	autorizacion_familias BOOLEAN DEFAULT 'false' NOT NULL,
	autorizacion_familias_en TIMESTAMP WITHOUT TIME ZONE,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(facilitador_id) REFERENCES usuario (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_curso_codigo ON curso (codigo);
CREATE INDEX IF NOT EXISTS ix_curso_facilitador_id ON curso (facilitador_id);

-- denuncia: Denuncia de un jugador contra otro (conducta, trampa o abandonos). La revisa el docente del denunciado, el administrador de su institución o la plataforma.
CREATE TABLE IF NOT EXISTS denuncia (
	id SERIAL NOT NULL,
	denunciante_id INTEGER NOT NULL,
	denunciado_id INTEGER NOT NULL,
	motivo VARCHAR NOT NULL,
	detalle VARCHAR,
	codigo_sala VARCHAR,
	estado VARCHAR DEFAULT 'nueva' NOT NULL,
	creada_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	resuelta_en TIMESTAMP WITHOUT TIME ZONE,
	resuelta_por INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(denunciante_id) REFERENCES usuario (id),
	FOREIGN KEY(denunciado_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_denuncia_denunciado_id ON denuncia (denunciado_id);
CREATE INDEX IF NOT EXISTS ix_denuncia_denunciante_id ON denuncia (denunciante_id);

-- elo_movimiento: Cuánto se movió el Elo de un jugador en una partida contra otra persona (una fila por jugador y por sala). Es su historial de Elo y evita contar dos veces la misma partida.
CREATE TABLE IF NOT EXISTS elo_movimiento (
	id SERIAL NOT NULL,
	usuario_id INTEGER NOT NULL,
	rival_id INTEGER NOT NULL,
	sala_codigo VARCHAR NOT NULL,
	color VARCHAR NOT NULL,
	resultado FLOAT NOT NULL,
	elo_antes INTEGER NOT NULL,
	elo_despues INTEGER NOT NULL,
	elo_rival INTEGER NOT NULL,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_elo_movimiento_sala UNIQUE (usuario_id, sala_codigo),
	FOREIGN KEY(usuario_id) REFERENCES usuario (id),
	FOREIGN KEY(rival_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_elo_movimiento_rival_id ON elo_movimiento (rival_id);
CREATE INDEX IF NOT EXISTS ix_elo_movimiento_sala_codigo ON elo_movimiento (sala_codigo);
CREATE INDEX IF NOT EXISTS ix_elo_movimiento_usuario_id ON elo_movimiento (usuario_id);

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
	puntuada BOOLEAN DEFAULT 'true' NOT NULL,
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

-- aviso_curso: Aviso de un docente a su curso (una consigna, el enlace de una clase en vivo). Es en un solo sentido: los alumnos lo leen, no responden.
CREATE TABLE IF NOT EXISTS aviso_curso (
	id SERIAL NOT NULL,
	curso_id INTEGER NOT NULL,
	autor_id INTEGER NOT NULL,
	texto VARCHAR NOT NULL,
	enlace VARCHAR,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(curso_id) REFERENCES curso (id)
);
CREATE INDEX IF NOT EXISTS ix_aviso_curso_curso_id ON aviso_curso (curso_id);

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

-- tarea_curso: Tarea de un docente para su curso, con una meta que el sistema mide solo con lo que los alumnos juegan (partidas contra la IA, partidas entre compañeros o precisión).
CREATE TABLE IF NOT EXISTS tarea_curso (
	id SERIAL NOT NULL,
	curso_id INTEGER NOT NULL,
	autor_id INTEGER NOT NULL,
	titulo VARCHAR NOT NULL,
	tipo VARCHAR NOT NULL,
	meta INTEGER NOT NULL,
	desde TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	hasta TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	creada_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(curso_id) REFERENCES curso (id)
);
CREATE INDEX IF NOT EXISTS ix_tarea_curso_curso_id ON tarea_curso (curso_id);

-- torneo: Torneo relámpago de un curso (sistema suizo): nombre, reloj, cantidad de rondas y en qué ronda va. El docente lo arma y empareja cada ronda.
CREATE TABLE IF NOT EXISTS torneo (
	id SERIAL NOT NULL,
	curso_id INTEGER NOT NULL,
	nombre VARCHAR NOT NULL,
	control_tiempo_ms INTEGER DEFAULT '0' NOT NULL,
	rondas INTEGER DEFAULT '3' NOT NULL,
	ronda_actual INTEGER DEFAULT '0' NOT NULL,
	estado VARCHAR DEFAULT 'inscripcion' NOT NULL,
	creado_por INTEGER NOT NULL,
	creado_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(curso_id) REFERENCES curso (id)
);
CREATE INDEX IF NOT EXISTS ix_torneo_curso_id ON torneo (curso_id);

-- torneo_jugador: Jugador inscrito en un torneo, con sus puntos (1 por victoria, 0,5 por tablas, 1 por descanso).
CREATE TABLE IF NOT EXISTS torneo_jugador (
	id SERIAL NOT NULL,
	torneo_id INTEGER NOT NULL,
	usuario_id INTEGER NOT NULL,
	puntos FLOAT DEFAULT '0' NOT NULL,
	descansos INTEGER DEFAULT '0' NOT NULL,
	inscrito_en TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_torneo_jugador UNIQUE (torneo_id, usuario_id),
	FOREIGN KEY(torneo_id) REFERENCES torneo (id),
	FOREIGN KEY(usuario_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_torneo_jugador_torneo_id ON torneo_jugador (torneo_id);
CREATE INDEX IF NOT EXISTS ix_torneo_jugador_usuario_id ON torneo_jugador (usuario_id);

-- torneo_partida: Una partida de una ronda de un torneo: quién juega con blancas y con negras, su sala y cómo terminó.
CREATE TABLE IF NOT EXISTS torneo_partida (
	id SERIAL NOT NULL,
	torneo_id INTEGER NOT NULL,
	ronda INTEGER NOT NULL,
	blancas_id INTEGER NOT NULL,
	negras_id INTEGER,
	codigo_sala VARCHAR,
	resultado VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(torneo_id) REFERENCES torneo (id),
	FOREIGN KEY(blancas_id) REFERENCES usuario (id),
	FOREIGN KEY(negras_id) REFERENCES usuario (id)
);
CREATE INDEX IF NOT EXISTS ix_torneo_partida_blancas_id ON torneo_partida (blancas_id);
CREATE INDEX IF NOT EXISTS ix_torneo_partida_negras_id ON torneo_partida (negras_id);
CREATE INDEX IF NOT EXISTS ix_torneo_partida_torneo_id ON torneo_partida (torneo_id);

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
COMMENT ON COLUMN institucion.es_demostracion IS 'true en las instituciones de ejemplo que se cargan para mostrar el panel de ingresos; se quitan todas juntas.';
COMMENT ON COLUMN institucion.es_prueba IS 'true mientras es una institución de prueba, creada para que una institución evalúe el sistema antes de contratarlo.';
COMMENT ON COLUMN institucion.dominio_correo IS 'Dominio del correo institucional (por ejemplo colegio.edu.bo). Si está definido, solo entran a los cursos de sus docentes los jugadores con un correo de ese dominio. Opcional.';
COMMENT ON TABLE participante IS 'Estudiantes de un curso, para sesiones del facilitador.';
COMMENT ON COLUMN participante.id IS 'Identificador del participante.';
COMMENT ON COLUMN participante.nombre IS 'Nombre del participante.';
COMMENT ON TABLE sesion IS 'Sesión de clase que abre un facilitador, con su dificultad y tipo de oponente.';
COMMENT ON COLUMN sesion.id IS 'Identificador de la sesión.';
COMMENT ON COLUMN sesion.facilitador IS 'Nombre del facilitador que la abrió.';
COMMENT ON COLUMN sesion.fecha IS 'Fecha y hora de la sesión.';
COMMENT ON COLUMN sesion.dificultad IS 'Nivel de dificultad de la sesión, de 0 a 20.';
COMMENT ON COLUMN sesion.tipo_oponente IS 'motor, modelo o participante.';
COMMENT ON TABLE pago IS 'Pago que una institución avisa haber hecho. Queda pendiente hasta que la plataforma lo confirma; recién ahí se extiende la suscripción.';
COMMENT ON COLUMN pago.id IS 'Identificador del pago.';
COMMENT ON COLUMN pago.institucion_id IS 'Institución que pagó. FK → institucion.id.';
COMMENT ON COLUMN pago.plan IS 'Plan que se pagó (plan_60 o plan_120).';
COMMENT ON COLUMN pago.ciclo IS 'Ciclo que se pagó: mensual, trimestral o anual.';
COMMENT ON COLUMN pago.monto_bs IS 'Monto en bolivianos, ya con el descuento del ciclo.';
COMMENT ON COLUMN pago.estado IS 'pendiente (avisado, sin confirmar), confirmado (la plataforma lo confirmó) o rechazado.';
COMMENT ON COLUMN pago.referencia IS 'Lo que escribió la institución para que la plataforma lo encuentre (número de transferencia, nombre del titular…). Opcional.';
COMMENT ON COLUMN pago.creado_en IS 'Fecha y hora en que la institución avisó el pago.';
COMMENT ON COLUMN pago.resuelto_en IS 'Fecha y hora en que la plataforma lo confirmó o rechazó.';
COMMENT ON COLUMN pago.resuelto_por IS 'Usuario de la plataforma que lo resolvió. Sin clave foránea: el registro sobrevive a su cuenta.';
COMMENT ON COLUMN pago.periodo_hasta IS 'Hasta cuándo quedó pagada la suscripción con este pago (solo si se confirmó).';
COMMENT ON COLUMN pago.nota IS 'Comentario de la plataforma al resolver (por ejemplo, el motivo de un rechazo). Opcional.';
COMMENT ON TABLE suscripcion IS 'Suscripción de una institución: su plan (hasta 60 o hasta 120 alumnos), cuándo termina la prueba gratis (30 días por defecto) y hasta cuándo está pagada. El estado (prueba, activa o vencida) se calcula con las fechas.';
COMMENT ON COLUMN suscripcion.id IS 'Identificador de la suscripción.';
COMMENT ON COLUMN suscripcion.institucion_id IS 'Institución a la que pertenece. FK → institucion.id. Única: cada institución tiene una sola suscripción.';
COMMENT ON COLUMN suscripcion.plan IS 'plan_60 (hasta 60 alumnos, Bs 350 al mes) o plan_120 (hasta 120 alumnos, Bs 500 al mes).';
COMMENT ON COLUMN suscripcion.ciclo IS 'Ciclo del último pago: mensual, trimestral (10 % de descuento) o anual (paga 10 meses de 12).';
COMMENT ON COLUMN suscripcion.prueba_hasta IS 'Fecha y hora en que termina la prueba gratis (30 días desde que se creó, o el plazo que se haya elegido al dar de alta).';
COMMENT ON COLUMN suscripcion.vigente_hasta IS 'Fecha y hora hasta la que está pagada. Nulo si nunca pagó.';
COMMENT ON COLUMN suscripcion.creado_en IS 'Fecha y hora de creación.';
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
COMMENT ON COLUMN usuario.correo_verificado_en IS 'Fecha de comprobación de la propiedad del correo por código o Google. Nulo para cuentas antiguas o creadas sin envío de correo. Exigido al ingresar por dominio institucional.';
COMMENT ON COLUMN usuario.codigo_verificacion_hash IS 'Huella SHA-256 del código de 6 dígitos enviado al correo. El código en claro no se guarda.';
COMMENT ON COLUMN usuario.codigo_verificacion_expira IS 'Fecha y hora a partir de la cual el código de verificación ya no sirve (15 minutos).';
COMMENT ON COLUMN usuario.recuperacion_password_hash IS 'Huella del código de recuperación de contraseña, ligada a la contraseña vigente. Nunca se guarda el código en claro.';
COMMENT ON COLUMN usuario.recuperacion_password_expira IS 'Vencimiento UTC del código de recuperación de contraseña (15 minutos).';
COMMENT ON COLUMN usuario.version_credenciales IS 'Versión incluida en los tokens. Aumenta al cambiar o restablecer la contraseña para invalidar sesiones anteriores.';
COMMENT ON COLUMN usuario.totp_activo IS 'true si el facilitador activó la verificación en dos pasos (app de códigos como Google Authenticator).';
COMMENT ON COLUMN usuario.totp_secreto IS 'Secreto de la app de códigos, CIFRADO con una clave derivada de JWT_SECRET_KEY. Nulo si nunca lo preparó.';
COMMENT ON COLUMN usuario.totp_ultimo_paso IS 'Último paso de 30 segundos cuyo código se aceptó: impide usar el mismo código dos veces.';
COMMENT ON COLUMN usuario.totp_codigos_respaldo IS 'Lista (JSON) de huellas SHA-256 de los códigos de respaldo que le quedan. Los códigos en claro no se guardan.';
COMMENT ON COLUMN usuario.cuenta_vinculada_id IS 'Cuenta con la que se alterna en el modo evaluación: un facilitador que prueba el sistema como director apunta a su jugador de prueba, y viceversa. FK → usuario.id. Nulo en el resto de las cuentas.';
COMMENT ON COLUMN usuario.codigo_amigo IS 'Código corto para que otro jugador le mande una solicitud de amistad sin saber su correo (se comparte por WhatsApp). Se genera la primera vez que se pide. Nulo hasta entonces.';
COMMENT ON COLUMN usuario.tutor_nombre IS 'Nombre de la madre, el padre o el tutor de un jugador menor de edad. Lo ven solo el propio jugador, su docente, el administrador de su institución y la plataforma.';
COMMENT ON COLUMN usuario.tutor_telefono IS 'Teléfono (WhatsApp) del tutor, para que el docente pueda avisarle si pasa algo.';
COMMENT ON COLUMN usuario.tutor_email IS 'Correo opcional de la madre, el padre o el tutor para compartir el progreso o contactarse. Privado, con el mismo alcance que el teléfono.';
COMMENT ON COLUMN usuario.autorizacion_tutor_en IS 'Fecha y hora en que se declaró que el tutor autoriza el uso de ChessIA. Nulo si no se declaró.';
COMMENT ON COLUMN usuario.cuenta_gestionada IS 'true si la cuenta la creó un docente para un alumno sin correo: el alumno entra con un usuario y una clave que el docente le entrega.';
COMMENT ON COLUMN usuario.elo IS 'Elo del jugador entre personas: empieza en 1000 y sube o baja con cada partida contra otro jugador (reto, búsqueda de rival o torneo del curso).';
COMMENT ON COLUMN usuario.partidas_elo IS 'Cuántas partidas contra otras personas movieron su Elo. Con menos de 20 el Elo se muestra como provisional.';
COMMENT ON TABLE amistad IS 'Amistad entre dos jugadores que no comparten curso (los compañeros de curso no la necesitan para jugar entre sí). Una solicitud queda pendiente hasta que el destinatario la acepta; si la rechaza, la fila se borra.';
COMMENT ON COLUMN amistad.id IS 'Identificador de la amistad.';
COMMENT ON COLUMN amistad.solicitante_id IS 'Jugador que mandó la solicitud. FK → usuario.id.';
COMMENT ON COLUMN amistad.destinatario_id IS 'Jugador que la recibe y la acepta o la rechaza. FK → usuario.id.';
COMMENT ON COLUMN amistad.estado IS 'pendiente (esperando respuesta) o aceptada. Una solicitud rechazada se borra.';
COMMENT ON COLUMN amistad.creada_en IS 'Fecha y hora en que se mandó la solicitud.';
COMMENT ON COLUMN amistad.respondida_en IS 'Fecha y hora en que el destinatario la aceptó. Nulo mientras está pendiente.';
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
COMMENT ON COLUMN codigo_invitacion.institucion_id IS 'Institución para la que se generó. FK → institucion.id. Quien se registra con el código queda dentro de ella. Nulo si solo da permiso para registrarse como facilitador.';
COMMENT ON COLUMN codigo_invitacion.como_administrador IS 'true si quien se registra con el código queda como administrador de la institución (solo lo genera la plataforma).';
COMMENT ON COLUMN codigo_invitacion.es_evaluacion IS 'true si es una invitación para evaluar el sistema: a quien se registre se le crea además un jugador de prueba.';
COMMENT ON TABLE curso IS 'Grupo de estudiantes de un facilitador. Los jugadores entran con un código del curso, que sirve para muchos y el facilitador puede volver a ver y regenerar.';
COMMENT ON COLUMN curso.id IS 'Identificador del curso.';
COMMENT ON COLUMN curso.nombre IS 'Nombre del curso, que elige el facilitador (por ejemplo, Ajedrez 1ro A).';
COMMENT ON COLUMN curso.facilitador_id IS 'Facilitador dueño del curso. FK → usuario.id.';
COMMENT ON COLUMN curso.codigo IS 'Código de 6 caracteres (alfabeto sin ambiguos) que el facilitador le da a la clase. Es único, sirve para muchos jugadores y se guarda en claro porque solo permite entrar al curso como jugador.';
COMMENT ON COLUMN curso.acepta_ingresos IS 'Si es falso, el código no deja entrar a nadie más; los ya inscritos siguen.';
COMMENT ON COLUMN curso.cupo IS 'Cantidad máxima de jugadores que pueden inscribirse, de 1 a 120 (60 por defecto). Lo define quien crea el curso.';
COMMENT ON COLUMN curso.preset_ensenanza IS 'Tono con el que Turing les habla a los jugadores de este curso: infantil, estandar o adultos. Nulo = se usa el tono que eligió el facilitador en su perfil.';
COMMENT ON COLUMN curso.permite_retos IS '«Modo clase»: si es falso, los alumnos del curso no pueden retarse, buscar rival ni jugar salas entre ellos (el docente lo pausa mientras explica).';
COMMENT ON COLUMN curso.autorizacion_familias IS 'El docente (o la institución) declara que tiene la autorización de las familias de los alumnos de este curso. Con eso los alumnos menores no cargan los datos de un tutor uno por uno para jugar con sus compañeros.';
COMMENT ON COLUMN curso.autorizacion_familias_en IS 'Fecha y hora en que se hizo esa declaración. Nulo si no se hizo.';
COMMENT ON COLUMN curso.creado_en IS 'Fecha y hora de creación.';
COMMENT ON TABLE denuncia IS 'Denuncia de un jugador contra otro (conducta, trampa o abandonos). La revisa el docente del denunciado, el administrador de su institución o la plataforma.';
COMMENT ON COLUMN denuncia.id IS 'Identificador de la denuncia.';
COMMENT ON COLUMN denuncia.denunciante_id IS 'Jugador que denuncia. FK → usuario.id.';
COMMENT ON COLUMN denuncia.denunciado_id IS 'Jugador denunciado. FK → usuario.id.';
COMMENT ON COLUMN denuncia.motivo IS 'conducta, trampa, abandono u otro.';
COMMENT ON COLUMN denuncia.detalle IS 'Lo que escribió quien denuncia (hasta 300 caracteres). Opcional.';
COMMENT ON COLUMN denuncia.codigo_sala IS 'Código de la sala donde pasó, si fue en una partida. Opcional.';
COMMENT ON COLUMN denuncia.estado IS 'nueva o revisada.';
COMMENT ON COLUMN denuncia.creada_en IS 'Fecha y hora en que se hizo.';
COMMENT ON COLUMN denuncia.resuelta_en IS 'Fecha y hora en que un docente o la plataforma la marcó como revisada.';
COMMENT ON COLUMN denuncia.resuelta_por IS 'Usuario que la revisó. Sin clave foránea: el registro sobrevive a su cuenta.';
COMMENT ON TABLE elo_movimiento IS 'Cuánto se movió el Elo de un jugador en una partida contra otra persona (una fila por jugador y por sala). Es su historial de Elo y evita contar dos veces la misma partida.';
COMMENT ON COLUMN elo_movimiento.id IS 'Identificador del movimiento de Elo.';
COMMENT ON COLUMN elo_movimiento.usuario_id IS 'Jugador cuyo Elo se movió. FK → usuario.id.';
COMMENT ON COLUMN elo_movimiento.rival_id IS 'Su rival en esa partida. FK → usuario.id.';
COMMENT ON COLUMN elo_movimiento.sala_codigo IS 'Código de la sala donde se jugó. Con usuario_id es único: cada partida se anota una sola vez por jugador.';
COMMENT ON COLUMN elo_movimiento.color IS 'blancas o negras: con qué color jugó.';
COMMENT ON COLUMN elo_movimiento.resultado IS 'Puntos que sacó: 1 ganó, 0,5 tablas, 0 perdió.';
COMMENT ON COLUMN elo_movimiento.elo_antes IS 'Su Elo antes de la partida.';
COMMENT ON COLUMN elo_movimiento.elo_despues IS 'Su Elo después de la partida.';
COMMENT ON COLUMN elo_movimiento.elo_rival IS 'El Elo que tenía su rival antes de la partida.';
COMMENT ON COLUMN elo_movimiento.creado_en IS 'Fecha y hora en que se anotó.';
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
COMMENT ON COLUMN partida.puntuada IS 'Solo en partidas entre personas: true si mueve el Elo de los dos jugadores, false si es amistosa (no puntúa). Los torneos siempre puntúan.';
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
COMMENT ON TABLE aviso_curso IS 'Aviso de un docente a su curso (una consigna, el enlace de una clase en vivo). Es en un solo sentido: los alumnos lo leen, no responden.';
COMMENT ON COLUMN aviso_curso.id IS 'Identificador del aviso.';
COMMENT ON COLUMN aviso_curso.curso_id IS 'Curso al que va dirigido. FK → curso.id.';
COMMENT ON COLUMN aviso_curso.autor_id IS 'Docente que lo escribió. Sin clave foránea: el aviso sobrevive a su cuenta.';
COMMENT ON COLUMN aviso_curso.texto IS 'El mensaje (hasta 500 caracteres).';
COMMENT ON COLUMN aviso_curso.enlace IS 'Un enlace opcional, por ejemplo el de una clase en vivo.';
COMMENT ON COLUMN aviso_curso.creado_en IS 'Fecha y hora en que se publicó.';
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
COMMENT ON TABLE tarea_curso IS 'Tarea de un docente para su curso, con una meta que el sistema mide solo con lo que los alumnos juegan (partidas contra la IA, partidas entre compañeros o precisión).';
COMMENT ON COLUMN tarea_curso.id IS 'Identificador de la tarea.';
COMMENT ON COLUMN tarea_curso.curso_id IS 'Curso al que se le pone. FK → curso.id.';
COMMENT ON COLUMN tarea_curso.autor_id IS 'Docente que la puso. Sin clave foránea: la tarea sobrevive a su cuenta.';
COMMENT ON COLUMN tarea_curso.titulo IS 'Cómo se la presenta a los alumnos.';
COMMENT ON COLUMN tarea_curso.tipo IS 'Qué se mide: partidas_ia (partidas terminadas contra Turing o Stockfish), partidas_amigos (partidas terminadas contra compañeros o amigos) o precision (una partida con esa precisión o más).';
COMMENT ON COLUMN tarea_curso.meta IS 'La cantidad de partidas, o el porcentaje de precisión, que hay que alcanzar.';
COMMENT ON COLUMN tarea_curso.desde IS 'Desde cuándo cuenta lo que juegan.';
COMMENT ON COLUMN tarea_curso.hasta IS 'Hasta cuándo cuenta (fecha límite).';
COMMENT ON COLUMN tarea_curso.creada_en IS 'Fecha y hora en que se puso.';
COMMENT ON TABLE torneo IS 'Torneo relámpago de un curso (sistema suizo): nombre, reloj, cantidad de rondas y en qué ronda va. El docente lo arma y empareja cada ronda.';
COMMENT ON COLUMN torneo.id IS 'Identificador del torneo.';
COMMENT ON COLUMN torneo.curso_id IS 'Curso al que pertenece. FK → curso.id.';
COMMENT ON COLUMN torneo.nombre IS 'Nombre del torneo, que elige el docente.';
COMMENT ON COLUMN torneo.control_tiempo_ms IS 'Milisegundos por lado de cada partida (0 = sin reloj).';
COMMENT ON COLUMN torneo.rondas IS 'Cuántas rondas tiene (de 1 a 9).';
COMMENT ON COLUMN torneo.ronda_actual IS 'La ronda que se está jugando (0 mientras se inscriben).';
COMMENT ON COLUMN torneo.estado IS 'inscripcion, en_juego o terminado.';
COMMENT ON COLUMN torneo.creado_por IS 'Docente que lo armó. Sin clave foránea: el torneo sobrevive a su cuenta.';
COMMENT ON COLUMN torneo.creado_en IS 'Fecha y hora de creación.';
COMMENT ON TABLE torneo_jugador IS 'Jugador inscrito en un torneo, con sus puntos (1 por victoria, 0,5 por tablas, 1 por descanso).';
COMMENT ON COLUMN torneo_jugador.id IS 'Identificador de la inscripción.';
COMMENT ON COLUMN torneo_jugador.torneo_id IS 'Torneo en el que juega. FK → torneo.id.';
COMMENT ON COLUMN torneo_jugador.usuario_id IS 'Jugador inscrito. FK → usuario.id.';
COMMENT ON COLUMN torneo_jugador.puntos IS 'Puntos acumulados: 1 por victoria, 0,5 por tablas, 1 por descanso.';
COMMENT ON COLUMN torneo_jugador.descansos IS 'Rondas en que le tocó descansar (cuando los jugadores son impares).';
COMMENT ON COLUMN torneo_jugador.inscrito_en IS 'Fecha y hora en que se inscribió.';
COMMENT ON TABLE torneo_partida IS 'Una partida de una ronda de un torneo: quién juega con blancas y con negras, su sala y cómo terminó.';
COMMENT ON COLUMN torneo_partida.id IS 'Identificador de la partida del torneo.';
COMMENT ON COLUMN torneo_partida.torneo_id IS 'Torneo al que pertenece. FK → torneo.id.';
COMMENT ON COLUMN torneo_partida.ronda IS 'Ronda en que se juega.';
COMMENT ON COLUMN torneo_partida.blancas_id IS 'Jugador de blancas. FK → usuario.id.';
COMMENT ON COLUMN torneo_partida.negras_id IS 'Jugador de negras. FK → usuario.id. Nulo si el de blancas descansa esa ronda.';
COMMENT ON COLUMN torneo_partida.codigo_sala IS 'Código de la sala donde se juega.';
COMMENT ON COLUMN torneo_partida.resultado IS '1-0, 0-1, 1/2-1/2 o 0-0 (nadie jugó). Nulo mientras se juega.';

-- Seguridad: Row Level Security sin políticas = nadie accede por la API de Supabase.
DO $$
DECLARE
    tabla text;
    rol text;
BEGIN
    FOREACH tabla IN ARRAY ARRAY['auditoria', 'institucion', 'participante', 'sesion', 'pago', 'suscripcion', 'usuario', 'amistad', 'calibracion', 'codigo_invitacion', 'curso', 'denuncia', 'elo_movimiento', 'exportacion_dataset', 'medalla_ganada', 'medalla_personalizada', 'mensaje_tutor', 'partida', 'aviso_curso', 'inscripcion', 'jugada', 'tarea_curso', 'torneo', 'torneo_jugador', 'torneo_partida'] LOOP
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
WHERE schemaname = current_schema() AND tablename IN ('auditoria', 'institucion', 'participante', 'sesion', 'pago', 'suscripcion', 'usuario', 'amistad', 'calibracion', 'codigo_invitacion', 'curso', 'denuncia', 'elo_movimiento', 'exportacion_dataset', 'medalla_ganada', 'medalla_personalizada', 'mensaje_tutor', 'partida', 'aviso_curso', 'inscripcion', 'jugada', 'tarea_curso', 'torneo', 'torneo_jugador', 'torneo_partida')
ORDER BY tablename;
