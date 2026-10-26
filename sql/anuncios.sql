-- Noticias del tablero de la app (la campana de Inicio). Las lee GET /anuncios.
-- Se puede pegar completo en DataGrip o en la consola de Aiven, sobre la base cruz_roja_app.
-- Correrlo dos veces no duplica la tabla, pero sí vuelve a insertar las noticias de ejemplo.

CREATE TABLE IF NOT EXISTS anuncio (
    id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT (UUID()),
    titulo VARCHAR(120) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    contenido TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
    -- En UTC, como el resto de las fechas de la base. Una fecha futura no sale en la app hasta esa hora.
    publicado_en DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
    PRIMARY KEY (id),
    KEY idx_anuncio_publicado (publicado_en)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Las mismas tres que la app mostraba de ejemplo.
INSERT INTO anuncio (titulo, contenido, publicado_en) VALUES
    ('Vacunación contra la influenza',
     'Cruz Roja tiene vacunas gratis para mayores de 60 años. Lleva tu INE y tu cartilla de vacunación.',
     UTC_TIMESTAMP() - INTERVAL 1 DAY),
    ('Tu reloj ya avisa si te caes',
     'Si el Apple Watch detecta una caída te pregunta si estás bien. Si no contestas, pide ayuda por ti.',
     UTC_TIMESTAMP() - INTERVAL 3 DAY),
    ('Consejos para los días de calor',
     'Toma agua aunque no tengas sed, evita salir entre 12 y 4 de la tarde y usa ropa clara.',
     UTC_TIMESTAMP() - INTERVAL 10 DAY);

-- Para publicar una nueva después:
-- INSERT INTO anuncio (titulo, contenido) VALUES ('Título', 'Texto completo del aviso');
-- Para quitar una:
-- DELETE FROM anuncio WHERE titulo = 'Título';
