-- Noticias del tablero de la app (la campana de Inicio). Las lee GET /anuncios.
-- La tabla ya existe en cruz_roja_app; esto es su forma, por si hay que crearla en otra base.
-- La app muestra descripcion como el texto y fecha como el día de publicación.
-- Un día futuro no sale en la app hasta ese día (hora de Monterrey).

CREATE TABLE IF NOT EXISTS anuncio (
    id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    titulo VARCHAR(150) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    descripcion TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    fecha DATE NOT NULL,
    PRIMARY KEY (id),
    KEY idx_anuncio_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Para publicar una nueva:
-- INSERT INTO anuncio (id, titulo, descripcion, fecha) VALUES (UUID(), 'Título', 'Texto completo del aviso', CURDATE());
-- Para quitar una:
-- DELETE FROM anuncio WHERE titulo = 'Título';
