"""API de cuenta, sesión, perfil, historial, contactos, medicinas y emergencias.

No incluye citas. La forma del JSON está en models.py.
"""

import json
import logging
import os
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

import bcrypt
import jwt
from fastapi import Body, Depends, FastAPI, Header, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import (
    ContactoRespuesta,
    EmergenciaRespuesta,
    EnfermedadRespuesta,
    MedicinaRespuesta,
    SesionRespuesta,
    UsuarioRespuesta,
)

app = FastAPI(title="Vitalia")


def cargar_env() -> None:
    """Lee el archivo .env de esta carpeta. No pisa variables que ya existan."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as origen:
        for linea in origen:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def requerida(nombre: str) -> str:
    """Falla al arrancar si falta un dato, en vez de conectar con la contraseña vacía."""
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise RuntimeError(f"Falta {nombre}.")
    return valor


cargar_env()

# La contraseña y el secreto del token viven en .env, no en el repositorio.
DB_HOST = requerida("DB_HOST")
DB_PORT = requerida("DB_PORT")
DB_NAME = requerida("DB_NAME")
DB_USER = requerida("DB_USER")
DB_PASSWORD = requerida("DB_PASSWORD")
JWT_SECRETO = requerida("JWT_SECRETO")
JWT_ALGORITMO = "HS256"
TIPOS_SANGRE = {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}

# Ids que la app ya conoce. Se aseguran al arrancar, sin borrar el resto del catálogo.
SEMILLA_ENFERMEDADES = (
    ("diabetes", "Diabetes"),
    ("hipertension", "Presión alta"),
    ("cardiaca", "Enfermedad del corazón"),
    ("respiratoria", "Asma o EPOC"),
    ("renal", "Enfermedad de los riñones"),
    ("epilepsia", "Epilepsia"),
    ("demencia", "Alzheimer o demencia"),
    ("cancer", "Cáncer"),
)

DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"ssl": {"ssl_disabled": False}, "charset": "utf8mb4"},
)


@event.listens_for(engine, "connect")
def fijar_utc(conexion, _):
    """Las fechas de auditoría se escriben en UTC."""
    cursor = conexion.cursor()
    cursor.execute("SET time_zone = '+00:00'")
    cursor.close()


class ErrorAPI(Exception):
    """Error de negocio. El cliente siempre recibe {"error": "..."}."""

    def __init__(self, codigo: int, mensaje: str):
        self.codigo = codigo
        self.mensaje = mensaje


def ahora() -> datetime:
    """Hora UTC sin zona, que es lo que guarda un DATETIME de MySQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_contrasena(contrasena: str) -> str:
    """bcrypt con cost 12. Esto es lo único que se guarda de la contraseña."""
    return bcrypt.hashpw(contrasena.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def contrasena_coincide(contrasena: str, huella: str) -> bool:
    try:
        return bcrypt.checkpw(contrasena.encode("utf-8"), huella.encode("utf-8"))
    except ValueError:
        return False


# Huella fija para tardar lo mismo cuando el teléfono no existe.
HUELLA_DE_RELLENO = hash_contrasena("vitalia-relleno")


def crear_token(usuario_id: str) -> str:
    """JWT sin tabla de sesión. Dura 7 días y solo trae el id del usuario."""
    expira = datetime.now(timezone.utc) + timedelta(days=7)
    return jwt.encode({"sub": usuario_id, "exp": expira}, JWT_SECRETO, algorithm=JWT_ALGORITMO)


def usuario_actual(authorization: str | None = Header(default=None)) -> str:
    """Lee Authorization: Bearer <token> y regresa el usuario_id del token."""
    if not authorization:
        raise ErrorAPI(401, "Falta el token")
    partes = authorization.split(" ", 1)
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1].strip():
        raise ErrorAPI(401, "Falta el token")
    try:
        datos = jwt.decode(partes[1].strip(), JWT_SECRETO, algorithms=[JWT_ALGORITMO])
    except jwt.ExpiredSignatureError:
        raise ErrorAPI(401, "La sesión venció")
    except jwt.PyJWTError:
        raise ErrorAPI(401, "El token no es válido")
    usuario_id = datos.get("sub")
    if not isinstance(usuario_id, str) or not usuario_id:
        raise ErrorAPI(401, "El token no es válido")
    return usuario_id


def exigir_propio(token_id: str, ruta_id: str) -> None:
    """Un token no lee ni escribe la cuenta de otra persona."""
    if token_id != ruta_id:
        raise ErrorAPI(403, "No tienes acceso a esta cuenta")


def leer_telefono(valor) -> str:
    """Exactamente 10 dígitos, sin espacios ni lada."""
    if not isinstance(valor, str) or not valor.isdigit() or len(valor) != 10:
        raise ErrorAPI(422, "El teléfono debe tener 10 dígitos")
    return valor


def leer_correo(valor) -> str | None:
    """Vacío se guarda NULL. Si viene texto, tiene que traer @ y un punto."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorAPI(422, "El correo no es válido")
    limpio = valor.strip()
    if limpio == "":
        return None
    if "@" not in limpio or "." not in limpio or len(limpio) > 255:
        raise ErrorAPI(422, "El correo no es válido")
    return limpio


def leer_obligatorio(valor, mensaje_vacio: str, maximo: int, mensaje_largo: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorAPI(422, mensaje_vacio)
    limpio = valor.strip()
    if len(limpio) > maximo:
        raise ErrorAPI(422, mensaje_largo)
    return limpio


def leer_opcional(valor, maximo: int, mensaje_largo: str) -> str | None:
    """Apellido materno. Vacío o ausente se guarda NULL."""
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorAPI(422, "El apellido materno no es válido")
    limpio = valor.strip()
    if limpio == "":
        return None
    if len(limpio) > maximo:
        raise ErrorAPI(422, mensaje_largo)
    return limpio


def leer_fecha(valor) -> date:
    if not isinstance(valor, str) or len(valor) != 10:
        raise ErrorAPI(422, "La fecha de nacimiento debe ir como YYYY-MM-DD")
    try:
        nace = datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        raise ErrorAPI(422, "La fecha de nacimiento debe ir como YYYY-MM-DD")
    if nace.isoformat() != valor or nace > ahora().date():
        raise ErrorAPI(422, "La fecha de nacimiento no puede ser futura")
    return nace


def leer_contrasena(valor) -> str:
    if not isinstance(valor, str) or len(valor) < 6:
        raise ErrorAPI(422, "La contraseña debe tener al menos 6 caracteres")
    if len(valor.encode("utf-8")) > 72:
        raise ErrorAPI(422, "La contraseña es demasiado larga")
    return valor


def leer_sangre(valor) -> str | None:
    """null, vacío o «No sé» se guardan NULL."""
    if valor is None or valor == "" or valor == "No sé":
        return None
    if not isinstance(valor, str) or valor not in TIPOS_SANGRE:
        raise ErrorAPI(422, "El tipo de sangre no es válido")
    return valor


def leer_texto_libre(valor, mensaje: str) -> str | None:
    """Alergias y notas. Vacío se guarda NULL y en la respuesta sale ""."""
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise ErrorAPI(422, mensaje)
    limpio = valor.strip()
    if limpio == "":
        return None
    if len(limpio) > 60000:
        raise ErrorAPI(422, mensaje)
    return limpio


def leer_ids_enfermedad(valor) -> list[str]:
    if not isinstance(valor, list):
        raise ErrorAPI(422, "Las enfermedades deben ser una lista")
    vistos: list[str] = []
    for item in valor:
        if not isinstance(item, str) or not item.strip():
            raise ErrorAPI(422, "Hay una enfermedad que no existe")
        clave = item.strip()
        if clave not in vistos:
            vistos.append(clave)
    return vistos


def leer_descripciones(valor) -> list[str]:
    """Conserva el texto, incluido el que el usuario escribió libre. Ignora vacíos."""
    if not isinstance(valor, list):
        raise ErrorAPI(422, "Los antecedentes deben ser una lista")
    textos: list[str] = []
    for item in valor:
        if not isinstance(item, str):
            raise ErrorAPI(422, "Hay un antecedente que no es texto")
        limpio = item.strip()
        if limpio:
            textos.append(limpio)
    return textos


def leer_contacto(valor) -> dict | None:
    if valor is None:
        return None
    if not isinstance(valor, dict):
        raise ErrorAPI(422, "El contacto no es válido")
    nombre = leer_obligatorio(
        valor.get("nombre"),
        "El nombre del contacto es obligatorio",
        150,
        "El nombre del contacto es demasiado largo",
    )
    telefono = leer_telefono(valor.get("telefono"))
    parentesco = valor.get("parentesco", "")
    if parentesco is None:
        parentesco = ""
    if not isinstance(parentesco, str):
        raise ErrorAPI(422, "El parentesco no es válido")
    parentesco = parentesco.strip()
    if len(parentesco) > 50:
        raise ErrorAPI(422, "El parentesco es demasiado largo")
    return {"nombre": nombre, "telefono": telefono, "parentesco": parentesco}


def fecha_texto(valor) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    return valor.isoformat()


def texto_respuesta(valor) -> str:
    """NULL de la base sale como cadena vacía en alergias, notas y correo."""
    return "" if valor is None else valor


def parentesco_respuesta(valor) -> str:
    return "" if valor is None else valor


def traducir_integridad(exc: IntegrityError) -> ErrorAPI:
    mensaje = str(getattr(exc, "orig", exc))
    if "correo" in mensaje:
        return ErrorAPI(409, "Ese correo ya tiene una cuenta")
    return ErrorAPI(409, "Ese número ya tiene una cuenta")


def enfermedades_validas(conn, ids: list[str]) -> None:
    """Solo acepta ids activos del catálogo. Uno desconocido rechaza toda la lista."""
    if not ids:
        return
    params = {f"e{i}": clave for i, clave in enumerate(ids)}
    marcas = ", ".join(f":e{i}" for i in range(len(ids)))
    encontrados = conn.execute(
        text(f"SELECT id FROM enfermedad WHERE activa = 1 AND id IN ({marcas})"),
        params,
    ).scalars().all()
    if set(encontrados) != set(ids):
        raise ErrorAPI(422, "Hay una enfermedad que no existe")


def insertar_enfermedades(conn, usuario_id: str, ids: list[str]) -> None:
    # Un segundo entre filas: el DATETIME no guarda fracciones y así el orden se conserva.
    base = ahora().replace(microsecond=0)
    for indice, clave in enumerate(ids):
        conn.execute(
            text("""
                INSERT INTO usuario_enfermedad (usuario_id, enfermedad_id, creado_en)
                VALUES (:usuario_id, :enfermedad_id, :creado_en)
            """),
            {
                "usuario_id": usuario_id,
                "enfermedad_id": clave,
                "creado_en": base + timedelta(seconds=indice),
            },
        )


def insertar_antecedentes(conn, historial_id: str, descripciones: list[str]) -> None:
    base = ahora().replace(microsecond=0)
    for indice, descripcion in enumerate(descripciones):
        conn.execute(
            text("""
                INSERT INTO antecedente (id, historial_id, descripcion, creado_en, actualizado_en)
                VALUES (:id, :historial_id, :descripcion, :creado_en, :actualizado_en)
            """),
            {
                "id": str(uuid.uuid4()),
                "historial_id": historial_id,
                "descripcion": descripcion,
                "creado_en": base + timedelta(seconds=indice),
                "actualizado_en": base + timedelta(seconds=indice),
            },
        )


def asegurar_historial(conn, usuario_id: str) -> str:
    """Un usuario tiene un solo historial. Si falta al editar, se crea vacío."""
    existente = conn.execute(
        text("SELECT id FROM historial_medico WHERE usuario_id = :usuario_id"),
        {"usuario_id": usuario_id},
    ).scalar()
    if existente:
        return existente
    nuevo = str(uuid.uuid4())
    momento = ahora()
    conn.execute(
        text("""
            INSERT INTO historial_medico
                (id, usuario_id, tipo_sangre, alergias, notas, creado_en, actualizado_en)
            VALUES
                (:id, :usuario_id, NULL, NULL, NULL, :creado_en, :actualizado_en)
        """),
        {
            "id": nuevo,
            "usuario_id": usuario_id,
            "creado_en": momento,
            "actualizado_en": momento,
        },
    )
    return nuevo


def cuenta_existente(conn, usuario_id: str) -> None:
    existe = conn.execute(
        text("SELECT 1 FROM usuario WHERE id = :id"),
        {"id": usuario_id},
    ).scalar()
    if existe is None:
        raise ErrorAPI(404, "No existe esa cuenta")


def armar_ficha(conn, usuario_id: str) -> dict:
    """La ficha que la app vuelve a guardar en el teléfono."""
    usuario = conn.execute(
        text("""
            SELECT id, telefono, correo, nombre, apellido_paterno, apellido_materno,
                   fecha_nacimiento, activo
            FROM usuario
            WHERE id = :id
        """),
        {"id": usuario_id},
    ).mappings().first()
    if usuario is None:
        raise ErrorAPI(404, "No existe esa cuenta")

    historial = conn.execute(
        text("""
            SELECT id, tipo_sangre, alergias, notas
            FROM historial_medico
            WHERE usuario_id = :id
        """),
        {"id": usuario_id},
    ).mappings().first()

    enfermedades = conn.execute(
        text("""
            SELECT enfermedad_id
            FROM usuario_enfermedad
            WHERE usuario_id = :id
            ORDER BY creado_en, enfermedad_id
        """),
        {"id": usuario_id},
    ).scalars().all()

    antecedentes: list[str] = []
    if historial is not None:
        antecedentes = list(conn.execute(
            text("""
                SELECT descripcion
                FROM antecedente
                WHERE historial_id = :historial_id
                ORDER BY creado_en, id
            """),
            {"historial_id": historial["id"]},
        ).scalars().all())

    contactos = conn.execute(
        text("""
            SELECT id, nombre, telefono, parentesco
            FROM contacto
            WHERE usuario_id = :id
            ORDER BY creado_en, id
        """),
        {"id": usuario_id},
    ).mappings().all()

    return {
        "id": usuario["id"],
        "telefono": usuario["telefono"],
        "correo": texto_respuesta(usuario["correo"]),
        "nombre": usuario["nombre"],
        "apellido_paterno": usuario["apellido_paterno"],
        "apellido_materno": usuario["apellido_materno"],
        "fecha_nacimiento": fecha_texto(usuario["fecha_nacimiento"]),
        "activo": int(usuario["activo"]),
        "historial": {
            "id": None if historial is None else historial["id"],
            "tipo_sangre": None if historial is None else historial["tipo_sangre"],
            "alergias": "" if historial is None else texto_respuesta(historial["alergias"]),
            "notas": "" if historial is None else texto_respuesta(historial["notas"]),
            "enfermedades": list(enfermedades),
            "antecedentes": antecedentes,
        },
        "contactos": [
            {
                "id": fila["id"],
                "nombre": fila["nombre"],
                "telefono": fila["telefono"],
                "parentesco": parentesco_respuesta(fila["parentesco"]),
            }
            for fila in contactos
        ],
    }


def ficha(usuario_id: str) -> dict:
    try:
        with engine.connect() as conn:
            return armar_ficha(conn, usuario_id)
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo leer la ficha")
        raise ErrorAPI(503, "No se pudo completar la operación")


def asegurar_esquema() -> None:
    """Crea las tablas del código y de emergencias, y asegura el catálogo de enfermedades de la app."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS codigo_recuperacion (
                id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                usuario_id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                codigo CHAR(6) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                vence_en DATETIME NOT NULL,
                usado TINYINT(1) NOT NULL DEFAULT 0,
                PRIMARY KEY (id),
                KEY idx_codigo_usuario (usuario_id, usado),
                CONSTRAINT fk_codigo_usuario
                    FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS emergencia (
                id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                usuario_id CHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                latitud DECIMAL(9,6) NOT NULL,
                longitud DECIMAL(9,6) NOT NULL,
                telefono_llamado VARCHAR(15) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
                datos_enviados JSON NOT NULL,
                creado_en DATETIME NOT NULL,
                PRIMARY KEY (id),
                KEY idx_emergencia_usuario (usuario_id, creado_en),
                CONSTRAINT fk_emergencia_usuario
                    FOREIGN KEY (usuario_id) REFERENCES usuario (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """))
        # NULL significa «No sé». La columna ya podía serlo; se deja explícito.
        conn.execute(text("""
            ALTER TABLE historial_medico
            MODIFY tipo_sangre ENUM('A+','A-','B+','B-','AB+','AB-','O+','O-') NULL
        """))
        for clave, titulo in SEMILLA_ENFERMEDADES:
            conn.execute(
                text("""
                    INSERT INTO enfermedad (id, titulo, activa)
                    VALUES (:id, :titulo, 1)
                    ON DUPLICATE KEY UPDATE titulo = :titulo, activa = 1
                """),
                {"id": clave, "titulo": titulo},
            )


def enviar_sms(telefono: str, mensaje: str) -> None:
    """El código no se devuelve en el JSON. No hay proveedor de SMS: queda en el log."""
    logging.warning("SMS para %s: %s", telefono, mensaje)


@app.exception_handler(ErrorAPI)
async def responder_error(_, exc: ErrorAPI):
    return JSONResponse(status_code=exc.codigo, content={"error": exc.mensaje})


@app.exception_handler(RequestValidationError)
async def responder_validacion(_, __):
    return JSONResponse(status_code=422, content={"error": "Los datos enviados no son válidos"})


@app.post("/usuarios", response_model=UsuarioRespuesta, status_code=201)
def crear_usuario(datos: dict = Body(...)):
    """Crea cuenta, historial, enfermedades, antecedentes y contacto en una sola transacción."""
    telefono = leer_telefono(datos.get("telefono"))
    correo = leer_correo(datos.get("correo"))
    contrasena = leer_contrasena(datos.get("contrasena"))
    nombre = leer_obligatorio(datos.get("nombre"), "El nombre es obligatorio", 100, "El nombre es demasiado largo")
    apellido_paterno = leer_obligatorio(
        datos.get("apellido_paterno"),
        "El apellido paterno es obligatorio",
        100,
        "El apellido paterno es demasiado largo",
    )
    apellido_materno = leer_opcional(datos.get("apellido_materno"), 100, "El apellido materno es demasiado largo")
    fecha_nacimiento = leer_fecha(datos.get("fecha_nacimiento"))
    tipo_sangre = leer_sangre(datos.get("tipo_sangre"))
    alergias = leer_texto_libre(datos.get("alergias"), "Las alergias no son válidas")
    notas = leer_texto_libre(datos.get("notas"), "Las notas no son válidas")
    enfermedades = leer_ids_enfermedad(datos.get("enfermedades", []))
    antecedentes = leer_descripciones(datos.get("antecedentes", []))
    contacto = leer_contacto(datos.get("contacto"))
    huella = hash_contrasena(contrasena)
    usuario_id = str(uuid.uuid4())
    historial_id = str(uuid.uuid4())
    momento = ahora()

    try:
        with engine.begin() as conn:
            ocupado = conn.execute(
                text("SELECT 1 FROM usuario WHERE telefono = :telefono"),
                {"telefono": telefono},
            ).scalar()
            if ocupado:
                raise ErrorAPI(409, "Ese número ya tiene una cuenta")
            if correo:
                correo_ocupado = conn.execute(
                    text("SELECT 1 FROM usuario WHERE correo = :correo"),
                    {"correo": correo},
                ).scalar()
                if correo_ocupado:
                    raise ErrorAPI(409, "Ese correo ya tiene una cuenta")
            enfermedades_validas(conn, enfermedades)
            conn.execute(
                text("""
                    INSERT INTO usuario (
                        id, telefono, correo, contrasena_hash, nombre, apellido_paterno,
                        apellido_materno, fecha_nacimiento, activo, creado_en, actualizado_en
                    ) VALUES (
                        :id, :telefono, :correo, :contrasena_hash, :nombre, :apellido_paterno,
                        :apellido_materno, :fecha_nacimiento, 1, :creado_en, :actualizado_en
                    )
                """),
                {
                    "id": usuario_id,
                    "telefono": telefono,
                    "correo": correo,
                    "contrasena_hash": huella,
                    "nombre": nombre,
                    "apellido_paterno": apellido_paterno,
                    "apellido_materno": apellido_materno,
                    "fecha_nacimiento": fecha_nacimiento,
                    "creado_en": momento,
                    "actualizado_en": momento,
                },
            )
            conn.execute(
                text("""
                    INSERT INTO historial_medico (
                        id, usuario_id, tipo_sangre, alergias, notas, creado_en, actualizado_en
                    ) VALUES (
                        :id, :usuario_id, :tipo_sangre, :alergias, :notas, :creado_en, :actualizado_en
                    )
                """),
                {
                    "id": historial_id,
                    "usuario_id": usuario_id,
                    "tipo_sangre": tipo_sangre,
                    "alergias": alergias,
                    "notas": notas,
                    "creado_en": momento,
                    "actualizado_en": momento,
                },
            )
            insertar_enfermedades(conn, usuario_id, enfermedades)
            insertar_antecedentes(conn, historial_id, antecedentes)
            if contacto:
                conn.execute(
                    text("""
                        INSERT INTO contacto (id, usuario_id, nombre, telefono, parentesco, creado_en)
                        VALUES (:id, :usuario_id, :nombre, :telefono, :parentesco, :creado_en)
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "usuario_id": usuario_id,
                        "nombre": contacto["nombre"],
                        "telefono": contacto["telefono"],
                        "parentesco": contacto["parentesco"],
                        "creado_en": momento,
                    },
                )
    except ErrorAPI:
        raise
    except IntegrityError as exc:
        raise traducir_integridad(exc)
    except SQLAlchemyError:
        logging.exception("No se pudo crear la cuenta")
        raise ErrorAPI(503, "No se pudo completar la operación")

    return ficha(usuario_id)


@app.post("/sesion", response_model=SesionRespuesta)
def iniciar_sesion(datos: dict = Body(...)):
    """Entra solo si la cuenta existe, está activa y la contraseña coincide."""
    telefono = leer_telefono(datos.get("telefono"))
    contrasena = datos.get("contrasena")
    if not isinstance(contrasena, str):
        raise ErrorAPI(401, "El número o la contraseña no coinciden")
    try:
        with engine.connect() as conn:
            fila = conn.execute(
                text("""
                    SELECT id, contrasena_hash, activo
                    FROM usuario
                    WHERE telefono = :telefono
                """),
                {"telefono": telefono},
            ).mappings().first()
    except SQLAlchemyError:
        logging.exception("No se pudo iniciar sesión")
        raise ErrorAPI(503, "No se pudo completar la operación")

    huella = fila["contrasena_hash"] if fila else HUELLA_DE_RELLENO
    coincide = contrasena_coincide(contrasena, huella)
    if fila is None or not coincide or not int(fila["activo"]):
        raise ErrorAPI(401, "El número o la contraseña no coinciden")
    return {"token": crear_token(fila["id"]), "usuario": ficha(fila["id"])}


@app.post("/usuarios/recuperar", status_code=204)
def recuperar_contrasena(datos: dict = Body(...)):
    """Invalida códigos viejos, crea uno de 6 dígitos por 10 minutos y lo manda por SMS."""
    telefono = leer_telefono(datos.get("telefono"))
    try:
        with engine.begin() as conn:
            usuario_id = conn.execute(
                text("SELECT id FROM usuario WHERE telefono = :telefono AND activo = 1"),
                {"telefono": telefono},
            ).scalar()
            if usuario_id is None:
                raise ErrorAPI(404, "Ese número no tiene cuenta")
            conn.execute(
                text("""
                    UPDATE codigo_recuperacion
                    SET usado = 1
                    WHERE usuario_id = :usuario_id AND usado = 0
                """),
                {"usuario_id": usuario_id},
            )
            codigo = f"{secrets.randbelow(1_000_000):06d}"
            conn.execute(
                text("""
                    INSERT INTO codigo_recuperacion (id, usuario_id, codigo, vence_en, usado)
                    VALUES (:id, :usuario_id, :codigo, :vence_en, 0)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "usuario_id": usuario_id,
                    "codigo": codigo,
                    "vence_en": ahora() + timedelta(minutes=10),
                },
            )
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo crear el código")
        raise ErrorAPI(503, "No se pudo completar la operación")

    enviar_sms(telefono, f"Tu código de Vitalia es {codigo}. Vence en 10 minutos.")
    return Response(status_code=204)


@app.post("/usuarios/restablecer", status_code=204)
def restablecer_contrasena(datos: dict = Body(...)):
    """Cambia la contraseña si el código existe, no se usó y no venció."""
    telefono = leer_telefono(datos.get("telefono"))
    codigo = datos.get("codigo")
    if not isinstance(codigo, str) or len(codigo) != 6 or not codigo.isdigit():
        raise ErrorAPI(422, "El código no es correcto o ya venció")
    contrasena = leer_contrasena(datos.get("contrasena_nueva"))
    huella = hash_contrasena(contrasena)
    try:
        with engine.begin() as conn:
            fila = conn.execute(
                text("""
                    SELECT c.id
                    FROM codigo_recuperacion c
                    JOIN usuario u ON u.id = c.usuario_id
                    WHERE u.telefono = :telefono
                      AND c.codigo = :codigo
                      AND c.usado = 0
                      AND c.vence_en > :ahora
                """),
                {"telefono": telefono, "codigo": codigo, "ahora": ahora()},
            ).mappings().first()
            if fila is None:
                raise ErrorAPI(422, "El código no es correcto o ya venció")
            conn.execute(
                text("UPDATE codigo_recuperacion SET usado = 1 WHERE id = :id"),
                {"id": fila["id"]},
            )
            conn.execute(
                text("""
                    UPDATE usuario
                    SET contrasena_hash = :huella, actualizado_en = :actualizado_en
                    WHERE telefono = :telefono
                """),
                {"huella": huella, "actualizado_en": ahora(), "telefono": telefono},
            )
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo restablecer la contraseña")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return Response(status_code=204)


@app.get("/usuarios/{usuario_id}", response_model=UsuarioRespuesta)
def obtener_usuario(usuario_id: str, token_id: str = Depends(usuario_actual)):
    exigir_propio(token_id, usuario_id)
    return ficha(usuario_id)


@app.patch("/usuarios/{usuario_id}", response_model=UsuarioRespuesta)
def actualizar_usuario(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """Actualiza solo los campos que vienen. activo = 0 da de baja sin borrar filas."""
    exigir_propio(token_id, usuario_id)
    cambios: dict = {"id": usuario_id, "actualizado_en": ahora()}
    columnas: list[str] = []

    if "nombre" in datos:
        cambios["nombre"] = leer_obligatorio(
            datos.get("nombre"), "El nombre es obligatorio", 100, "El nombre es demasiado largo"
        )
        columnas.append("nombre = :nombre")
    if "apellido_paterno" in datos:
        cambios["apellido_paterno"] = leer_obligatorio(
            datos.get("apellido_paterno"),
            "El apellido paterno es obligatorio",
            100,
            "El apellido paterno es demasiado largo",
        )
        columnas.append("apellido_paterno = :apellido_paterno")
    if "apellido_materno" in datos:
        cambios["apellido_materno"] = leer_opcional(
            datos.get("apellido_materno"), 100, "El apellido materno es demasiado largo"
        )
        columnas.append("apellido_materno = :apellido_materno")
    if "telefono" in datos:
        cambios["telefono"] = leer_telefono(datos.get("telefono"))
        columnas.append("telefono = :telefono")
    if "correo" in datos:
        cambios["correo"] = leer_correo(datos.get("correo"))
        columnas.append("correo = :correo")
    if "fecha_nacimiento" in datos:
        cambios["fecha_nacimiento"] = leer_fecha(datos.get("fecha_nacimiento"))
        columnas.append("fecha_nacimiento = :fecha_nacimiento")
    if "activo" in datos:
        valor = datos.get("activo")
        if isinstance(valor, bool) or valor not in (0, 1):
            raise ErrorAPI(422, "El estado de la cuenta no es válido")
        cambios["activo"] = int(valor)
        columnas.append("activo = :activo")

    try:
        with engine.begin() as conn:
            cuenta_existente(conn, usuario_id)
            if "telefono" in cambios:
                otro = conn.execute(
                    text("SELECT 1 FROM usuario WHERE telefono = :telefono AND id <> :id"),
                    {"telefono": cambios["telefono"], "id": usuario_id},
                ).scalar()
                if otro:
                    raise ErrorAPI(409, "Ese número ya tiene una cuenta")
            if "correo" in cambios and cambios["correo"]:
                otro = conn.execute(
                    text("SELECT 1 FROM usuario WHERE correo = :correo AND id <> :id"),
                    {"correo": cambios["correo"], "id": usuario_id},
                ).scalar()
                if otro:
                    raise ErrorAPI(409, "Ese correo ya tiene una cuenta")
            if columnas:
                columnas.append("actualizado_en = :actualizado_en")
                conn.execute(
                    text(f"UPDATE usuario SET {', '.join(columnas)} WHERE id = :id"),
                    cambios,
                )
    except ErrorAPI:
        raise
    except IntegrityError as exc:
        raise traducir_integridad(exc)
    except SQLAlchemyError:
        logging.exception("No se pudo actualizar la cuenta")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return ficha(usuario_id)


@app.patch("/usuarios/{usuario_id}/historial", response_model=UsuarioRespuesta)
def actualizar_historial(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    exigir_propio(token_id, usuario_id)
    cambios: dict = {"actualizado_en": ahora()}
    columnas: list[str] = []
    if "tipo_sangre" in datos:
        cambios["tipo_sangre"] = leer_sangre(datos.get("tipo_sangre"))
        columnas.append("tipo_sangre = :tipo_sangre")
    if "alergias" in datos:
        cambios["alergias"] = leer_texto_libre(datos.get("alergias"), "Las alergias no son válidas")
        columnas.append("alergias = :alergias")
    if "notas" in datos:
        cambios["notas"] = leer_texto_libre(datos.get("notas"), "Las notas no son válidas")
        columnas.append("notas = :notas")

    try:
        with engine.begin() as conn:
            cuenta_existente(conn, usuario_id)
            historial_id = asegurar_historial(conn, usuario_id)
            if columnas:
                cambios["id"] = historial_id
                columnas.append("actualizado_en = :actualizado_en")
                conn.execute(
                    text(f"UPDATE historial_medico SET {', '.join(columnas)} WHERE id = :id"),
                    cambios,
                )
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo actualizar el historial")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return ficha(usuario_id)


@app.put("/usuarios/{usuario_id}/enfermedades", response_model=UsuarioRespuesta)
def reemplazar_enfermedades(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """Borra las enfermedades de la cuenta e inserta el conjunto nuevo."""
    exigir_propio(token_id, usuario_id)
    if "enfermedades" not in datos:
        raise ErrorAPI(422, "Las enfermedades deben ser una lista")
    ids = leer_ids_enfermedad(datos.get("enfermedades"))
    try:
        with engine.begin() as conn:
            cuenta_existente(conn, usuario_id)
            enfermedades_validas(conn, ids)
            conn.execute(
                text("DELETE FROM usuario_enfermedad WHERE usuario_id = :usuario_id"),
                {"usuario_id": usuario_id},
            )
            insertar_enfermedades(conn, usuario_id, ids)
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudieron guardar las enfermedades")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return ficha(usuario_id)


@app.get("/enfermedades", response_model=list[EnfermedadRespuesta])
def listar_enfermedades():
    """Catálogo público. Solo las que siguen activas."""
    try:
        with engine.connect() as conn:
            filas = conn.execute(
                text("SELECT id, titulo FROM enfermedad WHERE activa = 1 ORDER BY titulo")
            ).mappings().all()
    except SQLAlchemyError:
        logging.exception("No se pudo leer el catálogo")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return [{"id": fila["id"], "titulo": fila["titulo"]} for fila in filas]


@app.put("/historiales/{historial_id}/antecedentes", response_model=UsuarioRespuesta)
def reemplazar_antecedentes(historial_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """Sustituye los textos del historial. El historial tiene que ser del token."""
    if "descripciones" not in datos:
        raise ErrorAPI(422, "Los antecedentes deben ser una lista")
    descripciones = leer_descripciones(datos.get("descripciones"))
    try:
        with engine.begin() as conn:
            historial = conn.execute(
                text("SELECT id, usuario_id FROM historial_medico WHERE id = :id"),
                {"id": historial_id},
            ).mappings().first()
            if historial is None:
                raise ErrorAPI(404, "No existe ese historial")
            if historial["usuario_id"] != token_id:
                raise ErrorAPI(403, "No tienes acceso a esta cuenta")
            conn.execute(
                text("DELETE FROM antecedente WHERE historial_id = :historial_id"),
                {"historial_id": historial_id},
            )
            insertar_antecedentes(conn, historial_id, descripciones)
            dueno = historial["usuario_id"]
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudieron guardar los antecedentes")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return ficha(dueno)


@app.get("/usuarios/{usuario_id}/contactos", response_model=list[ContactoRespuesta])
def listar_contactos(usuario_id: str, token_id: str = Depends(usuario_actual)):
    exigir_propio(token_id, usuario_id)
    return ficha(usuario_id)["contactos"]


@app.post("/usuarios/{usuario_id}/contactos", response_model=ContactoRespuesta, status_code=201)
def crear_contacto(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    exigir_propio(token_id, usuario_id)
    contacto = leer_contacto(datos)
    if contacto is None:
        raise ErrorAPI(422, "El contacto no es válido")
    contacto_id = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            cuenta_existente(conn, usuario_id)
            conn.execute(
                text("""
                    INSERT INTO contacto (id, usuario_id, nombre, telefono, parentesco, creado_en)
                    VALUES (:id, :usuario_id, :nombre, :telefono, :parentesco, :creado_en)
                """),
                {
                    "id": contacto_id,
                    "usuario_id": usuario_id,
                    "nombre": contacto["nombre"],
                    "telefono": contacto["telefono"],
                    "parentesco": contacto["parentesco"],
                    "creado_en": ahora(),
                },
            )
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo guardar el contacto")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return {"id": contacto_id, **contacto}


def contacto_del_dueno(conn, contacto_id: str, token_id: str):
    fila = conn.execute(
        text("SELECT id, usuario_id, nombre, telefono, parentesco FROM contacto WHERE id = :id"),
        {"id": contacto_id},
    ).mappings().first()
    if fila is None:
        raise ErrorAPI(404, "No existe ese contacto")
    if fila["usuario_id"] != token_id:
        raise ErrorAPI(403, "No tienes acceso a esta cuenta")
    return fila


@app.patch("/contactos/{contacto_id}", response_model=ContactoRespuesta)
def actualizar_contacto(contacto_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """Cambia nombre, teléfono o parentesco solo si el contacto es de esta cuenta."""
    cambios = {}
    if "nombre" in datos:
        cambios["nombre"] = leer_obligatorio(
            datos.get("nombre"),
            "El nombre del contacto es obligatorio",
            150,
            "El nombre del contacto es demasiado largo",
        )
    if "telefono" in datos:
        cambios["telefono"] = leer_telefono(datos.get("telefono"))
    if "parentesco" in datos:
        contacto = leer_contacto({
            "nombre": "valido",
            "telefono": "0000000000",
            "parentesco": datos.get("parentesco"),
        })
        cambios["parentesco"] = contacto["parentesco"]

    try:
        with engine.begin() as conn:
            fila = contacto_del_dueno(conn, contacto_id, token_id)
            if cambios:
                asignaciones = ", ".join(f"{campo} = :{campo}" for campo in cambios)
                conn.execute(
                    text(f"UPDATE contacto SET {asignaciones} WHERE id = :id"),
                    {"id": contacto_id, **cambios},
                )
            else:
                cambios = {
                    "nombre": fila["nombre"],
                    "telefono": fila["telefono"],
                    "parentesco": parentesco_respuesta(fila["parentesco"]),
                }
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo actualizar el contacto")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return {
        "id": contacto_id,
        "nombre": cambios.get("nombre", fila["nombre"]),
        "telefono": cambios.get("telefono", fila["telefono"]),
        "parentesco": cambios.get("parentesco", parentesco_respuesta(fila["parentesco"])),
    }


@app.delete("/contactos/{contacto_id}", status_code=204)
def borrar_contacto(contacto_id: str, token_id: str = Depends(usuario_actual)):
    try:
        with engine.begin() as conn:
            contacto_del_dueno(conn, contacto_id, token_id)
            conn.execute(text("DELETE FROM contacto WHERE id = :id"), {"id": contacto_id})
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo borrar el contacto")
        raise ErrorAPI(503, "No se pudo completar la operación")
    return Response(status_code=204)


# Une medicina, cita y enfermedades. Varias enfermedades salen en varias filas.
QUERY_MEDICINAS = text("""
    SELECT
        m.id,
        m.nombre,
        m.dosis,
        m.indicaciones,
        m.creado_en,
        c.id AS cita_id,
        c.nombre_doctor,
        c.fecha_hora AS fecha_receta,
        e.titulo AS enfermedad
    FROM medicina m
    JOIN historial_medico h ON h.id = m.historial_id
    LEFT JOIN cita c ON c.id = m.cita_id
    LEFT JOIN enfermedad_medicina em ON em.medicina_id = m.id
    LEFT JOIN enfermedad e ON e.id = em.enfermedad_id
    WHERE h.usuario_id = :usuario_id
    ORDER BY m.nombre, e.titulo
""")


def leer_medicina_nueva(datos: dict) -> dict:
    """Nombre obligatorio. El id es opcional para que el celular y la base usen el mismo."""
    medicina_id = datos.get("id")
    if medicina_id in (None, ""):
        medicina_id = str(uuid.uuid4())
    elif not isinstance(medicina_id, str):
        raise ErrorAPI(422, "El id de la medicina no es válido")
    else:
        try:
            medicina_id = str(uuid.UUID(medicina_id))
        except ValueError:
            raise ErrorAPI(422, "El id de la medicina no es válido")

    nombre = datos.get("nombre")
    if not isinstance(nombre, str) or not nombre.strip() or len(nombre.strip()) > 150:
        raise ErrorAPI(422, "El nombre de la medicina es obligatorio")
    dosis = datos.get("dosis")
    if dosis is not None:
        if not isinstance(dosis, str) or len(dosis.strip()) > 100:
            raise ErrorAPI(422, "La dosis no es válida")
        dosis = dosis.strip() or None
    indicaciones = datos.get("indicaciones")
    if indicaciones is not None:
        if not isinstance(indicaciones, str):
            raise ErrorAPI(422, "Las indicaciones no son válidas")
        indicaciones = indicaciones.strip() or None
    return {
        "id": medicina_id,
        "nombre": nombre.strip(),
        "dosis": dosis,
        "indicaciones": indicaciones,
    }


@app.get("/usuarios/{usuario_id}/medicinas", response_model=list[MedicinaRespuesta])
def listar_medicinas(usuario_id: str, token_id: str = Depends(usuario_actual)):
    """Lista las medicinas del usuario. Varias filas de la misma medicina se juntan en una."""
    exigir_propio(token_id, usuario_id)
    try:
        with engine.connect() as conn:
            cuenta_existente(conn, usuario_id)
            filas = conn.execute(QUERY_MEDICINAS, {"usuario_id": usuario_id}).mappings().all()
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudieron leer las medicinas")
        raise ErrorAPI(503, "No se pudo consultar la base de datos")

    medicinas: dict[str, dict] = {}
    for fila in filas:
        medicina = medicinas.get(fila["id"])
        if medicina is None:
            receta = None
            if fila["cita_id"]:
                receta = {
                    "cita_id": fila["cita_id"],
                    "doctor": fila["nombre_doctor"],
                    "fecha": fila["fecha_receta"],
                }
            medicina = {
                "id": fila["id"],
                "nombre": fila["nombre"],
                "dosis": fila["dosis"],
                "indicaciones": fila["indicaciones"],
                "enfermedades": [],
                "receta": receta,
                "creado_en": fila["creado_en"],
            }
            medicinas[fila["id"]] = medicina
        if fila["enfermedad"]:
            medicina["enfermedades"].append(fila["enfermedad"])
    return list(medicinas.values())


@app.post("/usuarios/{usuario_id}/medicinas", response_model=MedicinaRespuesta, status_code=201)
def registrar_medicina(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """Registra una medicina anotada por el usuario. Todavía no trae receta ni enfermedades."""
    exigir_propio(token_id, usuario_id)
    nueva = leer_medicina_nueva(datos)
    try:
        with engine.begin() as conn:
            cuenta_existente(conn, usuario_id)
            historial_id = asegurar_historial(conn, usuario_id)
            ya_existe = conn.execute(
                text("SELECT 1 FROM medicina WHERE id = :id"),
                {"id": nueva["id"]},
            ).scalar()
            if ya_existe:
                raise ErrorAPI(409, "Esa medicina ya está registrada")
            conn.execute(
                text("""
                    INSERT INTO medicina (id, historial_id, nombre, dosis, indicaciones)
                    VALUES (:id, :historial_id, :nombre, :dosis, :indicaciones)
                """),
                {
                    "id": nueva["id"],
                    "historial_id": historial_id,
                    "nombre": nueva["nombre"],
                    "dosis": nueva["dosis"],
                    "indicaciones": nueva["indicaciones"],
                },
            )
            fila = conn.execute(
                text("""
                    SELECT id, nombre, dosis, indicaciones, creado_en
                    FROM medicina
                    WHERE id = :id
                """),
                {"id": nueva["id"]},
            ).mappings().one()
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo guardar la medicina")
        raise ErrorAPI(503, "No se pudo guardar la medicina")

    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "dosis": fila["dosis"],
        "indicaciones": fila["indicaciones"],
        "enfermedades": [],
        "receta": None,
        "creado_en": fila["creado_en"],
    }


def leer_coordenada(valor, limite: int, mensaje: str) -> float:
    """Número entre -limite y limite. Un booleano no cuenta como número."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErrorAPI(422, mensaje)
    if not -limite <= valor <= limite:
        raise ErrorAPI(422, mensaje)
    return round(float(valor), 6)


def leer_telefono_llamado(valor) -> str:
    """Solo dígitos, de 1 a 15. Acepta números cortos como 911."""
    if not isinstance(valor, str) or not valor.strip().isdigit() or len(valor.strip()) > 15:
        raise ErrorAPI(422, "El número llamado no es válido")
    return valor.strip()


def datos_para_emergencia(conn, usuario_id: str) -> dict:
    """Copia de la ficha al momento del botón. No cambia si luego se edita el perfil."""
    perfil = armar_ficha(conn, usuario_id)
    nombre = " ".join(
        parte
        for parte in (perfil["nombre"], perfil["apellido_paterno"], perfil["apellido_materno"])
        if parte
    )
    contactos = perfil["contactos"]
    return {
        "nombre": nombre,
        "alergias": perfil["historial"]["alergias"],
        "tipo_sangre": perfil["historial"]["tipo_sangre"],
        "contacto": contactos[0]["telefono"] if contactos else None,
    }


@app.post("/usuarios/{usuario_id}/emergencias", response_model=EmergenciaRespuesta, status_code=201)
def registrar_emergencia(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
    """El reloj o el celular solo manda ubicación y número. Lo médico sale de la cuenta del token."""
    exigir_propio(token_id, usuario_id)
    latitud = leer_coordenada(datos.get("latitud"), 90, "La latitud no es válida")
    longitud = leer_coordenada(datos.get("longitud"), 180, "La longitud no es válida")
    telefono_llamado = leer_telefono_llamado(datos.get("telefono_llamado"))
    emergencia_id = str(uuid.uuid4())
    creado_en = ahora().replace(microsecond=0)
    try:
        with engine.begin() as conn:
            enviados = datos_para_emergencia(conn, usuario_id)
            conn.execute(
                text("""
                    INSERT INTO emergencia
                        (id, usuario_id, latitud, longitud, telefono_llamado, datos_enviados, creado_en)
                    VALUES
                        (:id, :usuario_id, :latitud, :longitud, :telefono_llamado, :datos_enviados, :creado_en)
                """),
                {
                    "id": emergencia_id,
                    "usuario_id": usuario_id,
                    "latitud": latitud,
                    "longitud": longitud,
                    "telefono_llamado": telefono_llamado,
                    "datos_enviados": json.dumps(enviados, ensure_ascii=False),
                    "creado_en": creado_en,
                },
            )
    except ErrorAPI:
        raise
    except SQLAlchemyError:
        logging.exception("No se pudo guardar la emergencia")
        raise ErrorAPI(503, "No se pudo guardar la emergencia")

    return {
        "id": emergencia_id,
        "usuario_id": usuario_id,
        "latitud": latitud,
        "longitud": longitud,
        "telefono_llamado": telefono_llamado,
        "datos_enviados": enviados,
        "creado_en": creado_en,
    }


asegurar_esquema()
