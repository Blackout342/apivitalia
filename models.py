"""Forma del JSON que entra y sale. La validación en español vive en views.py."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ContactoRespuesta(BaseModel):
    """Contacto de la cuenta. Las banderas del celular no viajan aquí."""

    id: str
    nombre: str
    telefono: str
    parentesco: str


class HistorialRespuesta(BaseModel):
    """Ficha médica. tipo_sangre null significa «No sé»."""

    id: str | None = None
    tipo_sangre: str | None = None
    alergias: str = ""
    notas: str = ""
    enfermedades: list[str]
    antecedentes: list[str]


class UsuarioRespuesta(BaseModel):
    """Cuenta lista para rehidratar la app. Nunca incluye la contraseña ni su hash."""

    id: str
    telefono: str
    correo: str = ""
    nombre: str
    apellido_paterno: str
    apellido_materno: str | None = None
    fecha_nacimiento: str | None = None
    activo: int
    historial: HistorialRespuesta
    contactos: list[ContactoRespuesta]


class SesionRespuesta(BaseModel):
    """Token de 7 días y la misma ficha que GET /usuarios/{id}."""

    token: str
    usuario: UsuarioRespuesta


class EnfermedadRespuesta(BaseModel):
    """Fila activa del catálogo. El id es el de la app, por ejemplo diabetes."""

    id: str
    titulo: str


class UsuarioNuevo(BaseModel):
    """Registro. contacto null si la persona todavía no anota a nadie."""

    telefono: str
    correo: str | None = ""
    contrasena: str
    nombre: str
    apellido_paterno: str
    apellido_materno: str | None = None
    fecha_nacimiento: str
    tipo_sangre: str | None = None
    alergias: str | None = ""
    notas: str | None = ""
    enfermedades: list[str] = Field(default_factory=list)
    antecedentes: list[str] = Field(default_factory=list)
    contacto: dict | None = None


class SesionNueva(BaseModel):
    telefono: str
    contrasena: str


class RecuperacionNueva(BaseModel):
    telefono: str


class RestablecerContrasena(BaseModel):
    telefono: str
    codigo: str
    contrasena_nueva: str


class ContactoNuevo(BaseModel):
    nombre: str
    telefono: str
    parentesco: str | None = ""


class RecetaInfo(BaseModel):
    """Cita de la que salió la medicina. No viene si el usuario la anotó solo."""

    cita_id: str
    doctor: str
    fecha: datetime


class CitaRespuesta(BaseModel):
    """fecha_hora usa offset de Monterrey; las fechas de auditoria usan UTC."""

    id: str
    usuario_id: str
    nombre_doctor: str
    especialidad_doctor: str | None
    telefono_doctor: str | None
    fecha_hora: datetime
    motivo: str | None
    lugar: str | None
    estado: Literal["programada", "realizada", "cancelada"]
    notas: str | None
    creado_en: datetime
    actualizado_en: datetime


class MedicinaRespuesta(BaseModel):
    """Medicina guardada, con las enfermedades para las que se toma y su receta si existe."""

    id: str
    nombre: str
    dosis: str | None
    indicaciones: str | None
    enfermedades: list[str]
    receta: RecetaInfo | None
    creado_en: datetime


class DatosEmergencia(BaseModel):
    """Copia de la ficha guardada con la emergencia. contacto es el primer contacto, o null."""

    nombre: str
    # El teléfono de la persona. null en las que se guardaron antes de que existiera o si no lo mandó.
    telefono: str | None = None
    alergias: str
    tipo_sangre: str | None
    contacto: str | None


class EmergenciaRespuesta(BaseModel):
    """Emergencia guardada. El celular solo mandó latitud, longitud y telefono_llamado.
    usuario_id es null y datos_enviados va vacío cuando se mandó sin cuenta."""

    id: str
    usuario_id: str | None
    latitud: float
    longitud: float
    telefono_llamado: str
    # "telefono" o "reloj". null en las que se guardaron antes de que existiera.
    origen: str | None = None
    datos_enviados: DatosEmergencia
    creado_en: datetime


class AvisoRespuesta(BaseModel):
    """Aviso de la cuenta. Lo ven todos los celulares que entraron con ella."""

    id: str
    titulo: str
    contenido: str
    creado_en: datetime


class AnuncioRespuesta(BaseModel):
    """Noticia del tablero de la app. Es la misma para todas las cuentas. publicado_en va en UTC."""

    id: str
    titulo: str
    contenido: str
    publicado_en: datetime
