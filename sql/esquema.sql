-- Esquema completo de la API Vitalia para MySQL 8 (Aiven).
-- Se corre una sola vez en una base vacía. Crea las 14 tablas y el catálogo de enfermedades.
-- Las fechas de auditoría se guardan en UTC (la API pone time_zone = '+00:00' al conectar).
-- cita.fecha_hora es hora de pared de America/Monterrey, sin zona.
-- Aiven exige llave primaria en todas las tablas, incluidas las de relación.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS usuario (
    id CHAR(36) NOT NULL,
    telefono CHAR(10) NOT NULL,
    correo VARCHAR(255) NULL,
    contrasena_hash VARCHAR(255) NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    apellido_paterno VARCHAR(100) NOT NULL,
    apellido_materno VARCHAR(100) NULL,
    fecha_nacimiento DATE NOT NULL,
    activo TINYINT(1) NOT NULL DEFAULT 1,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_usuario_telefono (telefono),
    UNIQUE KEY uq_usuario_correo (correo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS historial_medico (
    id CHAR(36) NOT NULL,
    usuario_id CHAR(36) NOT NULL,
    tipo_sangre ENUM('A+','A-','B+','B-','AB+','AB-','O+','O-') NULL,
    alergias TEXT NULL,
    notas TEXT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_historial_usuario (usuario_id),
    CONSTRAINT fk_historial_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enfermedad (
    id VARCHAR(50) NOT NULL,
    titulo VARCHAR(100) NOT NULL,
    activa TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS usuario_enfermedad (
    usuario_id CHAR(36) NOT NULL,
    enfermedad_id VARCHAR(50) NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, enfermedad_id),
    KEY idx_usuario_enfermedad_enfermedad (enfermedad_id),
    CONSTRAINT fk_ue_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE,
    CONSTRAINT fk_ue_enfermedad FOREIGN KEY (enfermedad_id) REFERENCES enfermedad (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS antecedente (
    id CHAR(36) NOT NULL,
    historial_id CHAR(36) NOT NULL,
    descripcion TEXT NOT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_antecedente_historial (historial_id, creado_en),
    CONSTRAINT fk_antecedente_historial FOREIGN KEY (historial_id) REFERENCES historial_medico (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS contacto (
    id CHAR(36) NOT NULL,
    usuario_id CHAR(36) NOT NULL,
    nombre VARCHAR(150) NOT NULL,
    telefono CHAR(10) NOT NULL,
    parentesco VARCHAR(50) NOT NULL DEFAULT '',
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_contacto_usuario (usuario_id, creado_en),
    CONSTRAINT fk_contacto_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS cita (
    id CHAR(36) NOT NULL,
    usuario_id CHAR(36) NOT NULL,
    nombre_doctor VARCHAR(150) NOT NULL,
    especialidad_doctor VARCHAR(100) NULL,
    telefono_doctor VARCHAR(15) NULL,
    fecha_hora DATETIME NOT NULL,
    motivo VARCHAR(255) NULL,
    lugar VARCHAR(255) NULL,
    estado ENUM('programada','realizada','cancelada') NOT NULL DEFAULT 'programada',
    notas TEXT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cita_usuario_fecha (usuario_id, fecha_hora),
    CONSTRAINT fk_cita_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS medicina (
    id CHAR(36) NOT NULL,
    historial_id CHAR(36) NOT NULL,
    cita_id CHAR(36) NULL,
    nombre VARCHAR(150) NOT NULL,
    dosis VARCHAR(100) NULL,
    indicaciones TEXT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_medicina_historial (historial_id),
    KEY idx_medicina_cita (cita_id),
    CONSTRAINT fk_medicina_historial FOREIGN KEY (historial_id) REFERENCES historial_medico (id) ON DELETE CASCADE,
    -- Borrar una cita deja la medicina, solo sin cita.
    CONSTRAINT fk_medicina_cita FOREIGN KEY (cita_id) REFERENCES cita (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS enfermedad_medicina (
    medicina_id CHAR(36) NOT NULL,
    enfermedad_id VARCHAR(50) NOT NULL,
    PRIMARY KEY (medicina_id, enfermedad_id),
    KEY idx_em_enfermedad (enfermedad_id),
    CONSTRAINT fk_em_medicina FOREIGN KEY (medicina_id) REFERENCES medicina (id) ON DELETE CASCADE,
    CONSTRAINT fk_em_enfermedad FOREIGN KEY (enfermedad_id) REFERENCES enfermedad (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Estas cuatro las crea también la API (asegurar_esquema). Van aquí para que la base quede lista sin eso.
CREATE TABLE IF NOT EXISTS codigo_recuperacion (
    id CHAR(36) NOT NULL,
    usuario_id CHAR(36) NOT NULL,
    codigo CHAR(6) NOT NULL,
    vence_en DATETIME NOT NULL,
    usado TINYINT(1) NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_codigo_usuario (usuario_id, usado),
    CONSTRAINT fk_codigo_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS emergencia (
    id CHAR(36) NOT NULL,
    -- NULL cuando el reloj pidió ayuda sin cuenta.
    usuario_id CHAR(36) NULL,
    latitud DECIMAL(9,6) NOT NULL,
    longitud DECIMAL(9,6) NOT NULL,
    telefono_llamado VARCHAR(15) NOT NULL,
    origen VARCHAR(10) NULL,
    datos_enviados JSON NOT NULL,
    creado_en DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_emergencia_usuario (usuario_id, creado_en),
    CONSTRAINT fk_emergencia_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS aviso (
    id CHAR(36) NOT NULL,
    usuario_id CHAR(36) NOT NULL,
    titulo VARCHAR(120) NOT NULL,
    contenido VARCHAR(500) NOT NULL,
    creado_en DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_aviso_usuario (usuario_id, creado_en),
    CONSTRAINT fk_aviso_usuario FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS anuncio (
    id CHAR(36) NOT NULL,
    titulo VARCHAR(150) NOT NULL,
    descripcion TEXT NOT NULL,
    fecha DATE NOT NULL,
    PRIMARY KEY (id),
    KEY idx_anuncio_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Catálogo que la app ya conoce. Se puede correr otra vez sin duplicar.
INSERT INTO enfermedad (id, titulo, activa) VALUES
    ('diabetes', 'Diabetes', 1),
    ('hipertension', 'Presión alta', 1),
    ('cardiaca', 'Enfermedad del corazón', 1),
    ('respiratoria', 'Asma o EPOC', 1),
    ('renal', 'Enfermedad de los riñones', 1),
    ('epilepsia', 'Epilepsia', 1),
    ('demencia', 'Alzheimer o demencia', 1),
    ('cancer', 'Cáncer', 1)
ON DUPLICATE KEY UPDATE titulo = VALUES(titulo), activa = 1;
