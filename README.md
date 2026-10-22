# API Vitalia

Cuenta, sesión, perfil, historial, contactos, citas, medicinas y emergencias. Las rutas están en `views.py` y `citas.py`, la forma del JSON en `models.py` y la validación JWT compartida en `seguridad.py`.

## Cómo correrla

Necesita Python 3.10 o más nuevo. Con [uv](https://docs.astral.sh/uv/) se baja solo.

1. Crea el entorno e instala las dependencias:

   ```bash
   uv venv --python 3.12 .venv
   uv pip install -r requirements.txt
   ```

   Sin uv: `python3.12 -m venv .venv` y `.venv/bin/pip install -r requirements.txt`.

2. Crea un archivo `.env` en esta carpeta. No se sube al repositorio.

   ```
   DB_HOST=
   DB_PORT=3306
   DB_NAME=
   DB_USER=
   DB_PASSWORD=
   JWT_SECRETO=
   CRUZ_ROJA_CLAVE=
   VITALIA_PREPARAR_ESQUEMA=0
   ```

   La base es MySQL y la conexión usa SSL. Para `JWT_SECRETO` sirve cualquier texto largo, por ejemplo el que da `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Si falta algún dato, la API no arranca y dice cuál (`Falta DB_HOST.`). Con `VITALIA_PREPARAR_ESQUEMA=0` el arranque no crea/altera tablas ni actualiza catálogos: usa el esquema existente. Si se omite la variable, conserva la preparación automática anterior; úsala solo cuando corresponda administrar ese esquema.

3. Arranca la API:

   ```bash
   source .venv/bin/activate
   uvicorn views:app --reload
   ```

   Documentación interactiva: `http://127.0.0.1:8000/docs`.

   Para probar desde un iPhone o reloj de verdad, arranca con `--host 0.0.0.0` y usa la IP de la Mac en la red, por ejemplo `http://192.168.1.20:8000`. El simulador sí puede usar `127.0.0.1`.

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

### Citas (Sprint 1)

Todas las rutas piden el JWT existente y que `usuario_id` sea su propio UUID. Verifican que la cuenta exista y siga activa, incluso si el token fue emitido antes de darla de baja. Los errores mantienen `{ "error": "mensaje" }`.

| Método | Ruta | Resultado |
| --- | --- | --- |
| GET | `/usuarios/{usuario_id}/citas` | 200, arreglo ordenado por `fecha_hora`, luego `id`; vacío si no hay citas |
| POST | `/usuarios/{usuario_id}/citas` | 201, cita creada |
| GET | `/usuarios/{usuario_id}/citas/{cita_id}` | 200, cita del usuario |
| PATCH | `/usuarios/{usuario_id}/citas/{cita_id}` | 200, cita actualizada; solo modifica campos enviados |
| DELETE | `/usuarios/{usuario_id}/citas/{cita_id}` | 204 sin cuerpo |

Ejemplo de POST, con UUID enviado por iOS (también se genera uno si se omite `id`):

```json
{
  "id": "40ca19fe-bc02-4415-b80d-72584287a7de",
  "nombre_doctor": "Dra. Ana López",
  "especialidad_doctor": "Cardiología",
  "telefono_doctor": "+528112345678",
  "fecha_hora": "2026-10-22T09:00:00-06:00",
  "motivo": "Control",
  "lugar": "Consultorio 2",
  "estado": "programada",
  "notas": "Llevar estudios"
}
```

La respuesta de POST/GET/PATCH contiene siempre `id`, `usuario_id`, `nombre_doctor`, `especialidad_doctor`, `telefono_doctor`, `fecha_hora`, `motivo`, `lugar`, `estado`, `notas`, `creado_en`, `actualizado_en`. Las tres fechas son ISO8601 con zona explícita: `fecha_hora` lleva el offset de Monterrey y la auditoría UTC (`Z` o `+00:00`). Los opcionales ausentes, vacíos o enviados como `null` se guardan/devuelven como `null`.

Validación: `nombre_doctor` obligatorio, no vacío, máximo 150 caracteres; `fecha_hora` obligatoria; `especialidad_doctor` máximo 100, `telefono_doctor` 15, `motivo` y `lugar` 255. `notas` es texto, máximo 65535 bytes UTF-8 (límite MySQL TEXT). Se recortan espacios externos. `estado` solo acepta `programada` (predeterminado), `realizada` o `cancelada`. Los estados no borran registros. PATCH permite esos mismos campos editables, no `id`, `usuario_id` ni fechas de auditoría; los campos desconocidos también se rechazan. PATCH `{}` devuelve la cita sin cambiar auditoría.

Un UUID duplicado devuelve 409 sin sobrescribir. Ante una respuesta perdida, el cliente puede hacer GET del mismo UUID y comparar los campos normalizados; el 409 por sí solo no confirma que el contenido coincida. Un UUID de otra cuenta no se expone por GET/PATCH/DELETE: devuelve 404 dentro de la ruta propia.

- 401: falta JWT, firma/formato inválido o sesión vencida; conserva los mensajes de autenticación existentes.
- 403: el JWT pertenece a otra cuenta o la cuenta está inactiva.
- 404: no existe la cuenta o no existe la cita dentro de esa cuenta.
- 409: UUID de cita ya registrado (también ante una carrera de inserciones).
- 422: UUID inválido, campos inválidos/inmutables, fechas sin offset o intervalo inválido.
- 503: error de base de datos; las escrituras se revierten y no se exponen detalles SQL.

#### Fechas y Compatibilidad

**Decisión de almacenamiento:** `cita.fecha_hora` sigue siendo un DATETIME sin zona que representa **hora de pared de `America/Monterrey`**, no UTC. La API exige ISO8601 con `Z` o un offset numérico `±HH:MM`, convierte ese instante a Monterrey y guarda sus componentes locales sin zona. Por ejemplo `2026-10-22T15:00:00Z` se guarda como `2026-10-22 09:00:00` y se devuelve como `2026-10-22T09:00:00-06:00`. No acepta fechas sin zona, fechas solas ni `-00:00` (offset desconocido). Acepta hasta seis decimales de segundo y descarta fracciones al guardar, porque el DATETIME existente tiene precisión de segundos.

`creado_en` y `actualizado_en` se siguen guardando como UTC sin zona y se devuelven con UTC explícito. No se cambia la sesión MySQL UTC ni el consumidor de recetas/medicinas: este sigue leyendo la hora local existente directamente. El offset de citas se obtiene de `zoneinfo`, no de una constante: respeta las reglas históricas de Monterrey. Los DATETIME históricos no distinguen las horas repetidas durante antiguos cambios de horario; no se inventa ni recupera esa información.

GET de colección admite `desde` y/o `hasta`, con la misma exigencia de offset, y aplica el intervalo semiabierto **`desde <= fecha_hora < hasta`** después de convertir los límites a hora local de Monterrey. Si vienen ambos, `desde` debe preceder a `hasta`. Las fracciones de los filtros sí se conservan para comparar con los segundos almacenados. En URLs, codificar `+` como `%2B` o usar un cliente que codifique query params.

Ejemplo: `/usuarios/{usuario_id}/citas?desde=2026-10-22T00:00:00-06:00&hasta=2026-10-23T00:00:00-06:00`.

**Antes de desplegar, confirmar con quien administra la base que los valores existentes de `cita.fecha_hora` ya usan hora de pared de Monterrey**, y que `medicina.cita_id` tiene `ON DELETE SET NULL`. Esta implementación asume el esquema de cita acordado y esa FK existente; no crea/altera esas tablas, no migra ni convierte datos existentes y no se verificó contra la base compartida. DELETE borra solo la cita: la FK anula su referencia y conserva las medicinas. No se modifican las rutas ni el esquema de medicinas.

#### Pruebas Aisladas

```bash
python3 -m venv /tmp/vitalia-citas-venv
/tmp/vitalia-citas-venv/bin/python -m pip install -r requirements-dev.txt
/tmp/vitalia-citas-venv/bin/python -m unittest discover -s tests -v
```

No arrancan `views:app`, no importan `views`, no leen `.env` y no conectan MySQL. Usan unittest, TestClient, JWT reales con secreto ficticio y SQLite en memoria con foreign keys activas. `citas.registrar_rutas_citas` recibe el engine, autenticación y reloj; importar o registrar rutas no inicia conexiones ni ejecuta DDL. Hay protecciones explícitas contra lectura de `.env` y creación/conexión de engines durante importación, incluidas pruebas en un intérprete nuevo.

Dependencias: no se agrega ninguna librería de producción; `zoneinfo` está en Python 3.10+. El sistema debe tener la base IANA que incluya `America/Monterrey`; en sistemas sin ella instalar `tzdata`. Para pruebas, `requirements-dev.txt` incluye las dependencias actuales y agrega `httpx` para TestClient; unittest y SQLite vienen con Python. SQLite verifica comportamiento y FK; no sustituye una prueba de despliegue sobre un MySQL desechable con el esquema real.

### `GET /usuarios/{id}/medicinas`

Con token. Lista las medicinas del usuario. Si una medicina es para varias enfermedades, los títulos van juntos en `enfermedades`. Si salió de una cita, `receta` trae el id, el doctor y la fecha. Si la anotó el usuario, `receta` es null.

### `POST /usuarios/{id}/medicinas`

Con token. Registra una medicina que el usuario anota por su cuenta. Body: `{ "id", "nombre", "dosis", "indicaciones" }`. `id` es opcional; si la app lo manda, se guarda ese mismo. `nombre` es obligatorio. 201 con la medicina, todavía sin enfermedades ni receta.

- 409: esa medicina ya está registrada
- 404: no existe esa cuenta

### `POST /usuarios/{id}/emergencias`

Con token. Lo manda el botón de emergencia del reloj o del celular. Body: `{ "latitud", "longitud", "telefono_llamado" }`, por ejemplo `{ "latitud": 25.671380, "longitud": -100.309765, "telefono_llamado": "911" }`. El servidor llena `datos_enviados` con la ficha de la cuenta del token: nombre completo, alergias, tipo de sangre y el teléfono del primer contacto (null si no tiene). Esa copia no cambia aunque después se edite el perfil. 201 con `{ "id", "usuario_id", "latitud", "longitud", "telefono_llamado", "datos_enviados", "creado_en" }`.

- 403: el token es de otra cuenta
- 404: no existe esa cuenta
- 422: `La latitud no es válida`, `La longitud no es válida` o `El número llamado no es válido`

### `POST /emergencias`

Sin token. Lo manda el reloj cuando no está ligado al celular, así el aviso sale aunque no haya cuenta. Mismo body y misma respuesta que `POST /usuarios/{id}/emergencias`, pero `usuario_id` es null y `datos_enviados` va vacío: `{ "nombre": "", "alergias": "", "tipo_sangre": null, "contacto": null }`.

- 422: `La latitud no es válida`, `La longitud no es válida` o `El número llamado no es válido`

### `GET /emergencias`

Para el panel de Cruz Roja. Pide `Authorization: Bearer <clave>` con la clave `CRUZ_ROJA_CLAVE` del `.env`, no un token de usuario. Si la variable falta, nadie entra. Devuelve todas las emergencias, la más nueva primero, cada una con la misma forma que `POST /emergencias`. `creado_en` va en UTC.

El navegador solo deja leerla desde los orígenes de `CORS_ORIGENES`, separados por coma. Si falta, valen `http://localhost:5500` y `http://127.0.0.1:5500`.

- 401: `Falta la clave` o `La clave no es válida`



easter egg 
dont grab me
