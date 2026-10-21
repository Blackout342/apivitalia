# API Vitalia

Cuenta, sesión, perfil, historial y contactos. Las rutas están en `views.py` y la forma del JSON en `models.py`.

Copia `.env.example` a `.env` y pon ahí la contraseña de la base y el secreto del token. Ese archivo no se sube al repositorio.

```bash
uvicorn views:app --reload
```

Documentación interactiva: `http://127.0.0.1:8000/docs`.

Las rutas con token piden `Authorization: Bearer <token>`. El token dura 7 días y solo puede leer y escribir su propia cuenta. Los errores llegan como `{ "error": "mensaje" }`.

El teléfono se guarda con 10 dígitos. La contraseña no se regresa: en la base queda su huella bcrypt. Un correo vacío sale como `""`. `tipo_sangre` null significa «No sé». `activo: 0` es la baja; las filas no se borran.

## APIs

### `POST /usuarios`

Público. Crea la cuenta, su historial, las enfermedades, los antecedentes y el contacto si vino. Si algo falla, no queda nada escrito.

- 201: la ficha completa, la misma de `GET /usuarios/{id}`
- 409: `Ese número ya tiene una cuenta`
- 422: enfermedad desconocida, teléfono inválido o contraseña de menos de 6 caracteres

### `POST /sesion`

Público. Body: `{ "telefono", "contrasena" }`. Solo entra una cuenta activa cuya contraseña coincida.

- 200: `{ "token", "usuario" }`
- 401: `El número o la contraseña no coinciden`

### `POST /usuarios/recuperar`

Público. Body: `{ "telefono" }`. Si la cuenta existe y está activa, invalida los códigos anteriores, crea uno de 6 dígitos que vence en 10 minutos y lo manda por SMS. No viene en la respuesta.

- 204: sin cuerpo
- 404: `Ese número no tiene cuenta`

### `POST /usuarios/restablecer`

Público. Body: `{ "telefono", "codigo", "contrasena_nueva" }`. Si el código sirve, lo marca usado y guarda la contraseña nueva.

- 204: sin cuerpo
- 422: `El código no es correcto o ya venció`, o la contraseña tiene menos de 6 caracteres

### `GET /usuarios/{id}`

Con token. Devuelve la ficha: datos de la cuenta, historial (sangre, alergias, notas, ids de enfermedades y antecedentes en el orden guardado) y contactos.

- 403: el token es de otra cuenta
- 404: no existe esa cuenta

### `PATCH /usuarios/{id}`

Con token. Body parcial: `nombre`, `apellido_paterno`, `apellido_materno`, `telefono`, `correo`, `fecha_nacimiento`. `{ "activo": 0 }` da de baja. Respuesta 200 con la ficha completa.

- 409: el teléfono ya es de otra cuenta

### `PATCH /usuarios/{id}/historial`

Con token. Body: `tipo_sangre`, `alergias`, `notas`. `tipo_sangre` null guarda «No sé». Si el usuario no tenía historial, lo crea. 200 con la ficha completa.

### `PUT /usuarios/{id}/enfermedades`

Con token. Body: `{ "enfermedades": ["diabetes", "renal"] }`. Borra las anteriores e inserta esta lista. Una lista vacía deja al usuario sin enfermedades. 200 con la ficha completa.

- 422: algún id no está en el catálogo activo

### `GET /enfermedades`

Público. Devuelve `[{ "id", "titulo" }]` de las enfermedades con `activa = 1`.

### `PUT /historiales/{historial_id}/antecedentes`

Con token. El historial tiene que ser del usuario del token. Body: `{ "descripciones": ["Infarto", "Caídas frecuentes"] }`. Borra los textos anteriores e inserta uno por cada texto, en ese orden. Los vacíos se ignoran. Una lista vacía los borra todos. 200 con la ficha del dueño.

### `GET /usuarios/{id}/contactos`

Con token. Arreglo de `{ "id", "nombre", "telefono", "parentesco" }`.

### `POST /usuarios/{id}/contactos`

Con token. Body: `{ "nombre", "telefono", "parentesco" }`. El nombre no va vacío, el teléfono tiene 10 dígitos y el parentesco vacío se guarda como `""`. 201 con el contacto.

### `PATCH /contactos/{id}`

Con token. Solo si el contacto es de su usuario. Mismos campos, parciales. 200 con el contacto.

### `DELETE /contactos/{id}`

Con token. Solo el dueño. 204.

### `GET /usuarios/{id}/medicinas`

Con token. Lista las medicinas del usuario. Si una medicina es para varias enfermedades, los títulos van juntos en `enfermedades`. Si salió de una cita, `receta` trae el id, el doctor y la fecha. Si la anotó el usuario, `receta` es null.

### `POST /usuarios/{id}/medicinas`

Con token. Registra una medicina que el usuario anota por su cuenta. Body: `{ "id", "nombre", "dosis", "indicaciones" }`. `id` es opcional; si la app lo manda, se guarda ese mismo. `nombre` es obligatorio. 201 con la medicina, todavía sin enfermedades ni receta.

- 409: esa medicina ya está registrada
- 404: no existe esa cuenta
