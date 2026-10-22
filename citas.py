"""CRUD de citas sobre el esquema existente; importar no abre conexiones."""

import logging
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, Response
from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models import CitaRespuesta
from seguridad import ErrorAPI, exigir_propio

ZONA_CITAS = ZoneInfo("America/Monterrey")
ESTADOS = {"programada", "realizada", "cancelada"}
LIMITES = {
    "nombre_doctor": 150,
    "especialidad_doctor": 100,
    "telefono_doctor": 15,
    "motivo": 255,
    "lugar": 255,
    "notas": 65535,
}
CAMPOS_EDITABLES = set(LIMITES) | {"fecha_hora", "estado"}
ISO_CON_ZONA = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
SELECT_CITAS = """
    SELECT id, usuario_id, nombre_doctor, especialidad_doctor, telefono_doctor,
           fecha_hora, motivo, lugar, estado, notas, creado_en, actualizado_en
    FROM cita
"""


def leer_id_cita(valor) -> str:
    if isinstance(valor, str):
        try:
            return str(uuid.UUID(valor))
        except ValueError:
            pass
    raise ErrorAPI(422, "El id de la cita no es valido")


def exigir_usuario(token_id: str, usuario_id: str) -> None:
    exigir_propio(token_id, usuario_id)
    try:
        if len(usuario_id) == 36 and str(uuid.UUID(usuario_id)) == usuario_id.lower():
            return
    except ValueError:
        pass
    raise ErrorAPI(422, "El id del usuario no es valido")


def leer_instante(valor, campo: str) -> datetime:
    """Exige offset; convierte el instante a Monterrey antes de quitar la zona."""
    mensaje = f"{campo} debe ser ISO8601 con offset explicito, por ejemplo 2026-10-22T09:00:00-06:00"
    if not isinstance(valor, str) or not ISO_CON_ZONA.fullmatch(valor) or valor.endswith("-00:00"):
        raise ErrorAPI(422, mensaje)
    try:
        local = datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(ZONA_CITAS)
    except (ValueError, OverflowError):
        raise ErrorAPI(422, mensaje)
    if local.year < 1000:
        raise ErrorAPI(422, f"{campo} esta fuera del rango DATETIME")
    return local.replace(tzinfo=None)


def leer_cambios(datos: dict, *, nueva: bool = False) -> dict:
    permitidos = CAMPOS_EDITABLES | ({"id"} if nueva else set())
    if set(datos) - permitidos:
        raise ErrorAPI(422, "Hay campos desconocidos o inmutables en la cita")
    if nueva and not {"nombre_doctor", "fecha_hora"} <= set(datos):
        raise ErrorAPI(422, "nombre_doctor y fecha_hora son obligatorios")
    cambios = {}
    for campo, limite in LIMITES.items():
        if campo not in datos:
            continue
        valor = datos[campo]
        if valor is None and campo != "nombre_doctor":
            cambios[campo] = None
            continue
        if not isinstance(valor, str):
            raise ErrorAPI(422, f"{campo} debe ser texto")
        valor = valor.strip()
        if campo == "nombre_doctor" and not valor:
            raise ErrorAPI(422, "El nombre del doctor es obligatorio")
        if len(valor) > limite or (campo == "notas" and len(valor.encode("utf-8")) > limite):
            raise ErrorAPI(422, f"{campo} es demasiado largo")
        cambios[campo] = valor or None
    if "fecha_hora" in datos:
        # El DATETIME existente no tiene precision fraccionaria.
        cambios["fecha_hora"] = leer_instante(datos["fecha_hora"], "fecha_hora").replace(microsecond=0)
    if "estado" in datos:
        if not isinstance(datos["estado"], str) or datos["estado"] not in ESTADOS:
            raise ErrorAPI(422, "El estado debe ser programada, realizada o cancelada")
        cambios["estado"] = datos["estado"]
    return cambios


def cuenta_activa(conn, usuario_id: str) -> None:
    activo = conn.execute(
        text("SELECT activo FROM usuario WHERE id = :usuario_id"), {"usuario_id": usuario_id}
    ).scalar()
    if activo is None:
        raise ErrorAPI(404, "No existe esa cuenta")
    if not int(activo):
        raise ErrorAPI(403, "La cuenta esta inactiva")


def consulta_citas(sql: str):
    return text(sql).columns(fecha_hora=DateTime(), creado_en=DateTime(), actualizado_en=DateTime())


def respuesta_cita(fila) -> dict:
    return dict(
        fila,
        fecha_hora=fila["fecha_hora"].replace(tzinfo=ZONA_CITAS),
        creado_en=fila["creado_en"].replace(tzinfo=timezone.utc),
        actualizado_en=fila["actualizado_en"].replace(tzinfo=timezone.utc),
    )


def cita_del_usuario(conn, usuario_id: str, cita_id: str):
    fila = conn.execute(
        consulta_citas(SELECT_CITAS + " WHERE usuario_id = :usuario_id AND id = :id"),
        {"usuario_id": usuario_id, "id": cita_id},
    ).mappings().first()
    if fila is None:
        raise ErrorAPI(404, "No existe esa cita")
    return fila


@contextmanager
def operacion_citas(engine):
    try:
        with engine.begin() as conn:
            yield conn
    except SQLAlchemyError:
        logging.exception("No se pudo completar la operacion de citas")
        raise ErrorAPI(503, "No se pudo completar la operacion")


def registrar_rutas_citas(app, engine, usuario_actual, ahora) -> None:
    """Inyecta el engine y la autenticacion existentes, sin importar views ni hacer DDL."""

    @app.get("/usuarios/{usuario_id}/citas", response_model=list[CitaRespuesta])
    def listar_citas(
        usuario_id: str,
        desde: str | None = None,
        hasta: str | None = None,
        token_id: str = Depends(usuario_actual),
    ):
        exigir_usuario(token_id, usuario_id)
        params = {"usuario_id": usuario_id}
        condiciones = ["usuario_id = :usuario_id"]
        for campo, valor, operador in (("desde", desde, ">="), ("hasta", hasta, "<")):
            if valor is not None:
                params[campo] = leer_instante(valor, campo)
                condiciones.append(f"fecha_hora {operador} :{campo}")
        if desde is not None and hasta is not None and params["desde"] >= params["hasta"]:
            raise ErrorAPI(422, "desde debe ser anterior a hasta")
        query = consulta_citas(
            SELECT_CITAS + " WHERE " + " AND ".join(condiciones) + " ORDER BY fecha_hora, id"
        ).bindparams(*(bindparam(campo, type_=DateTime()) for campo in ("desde", "hasta") if campo in params))
        with operacion_citas(engine) as conn:
            cuenta_activa(conn, usuario_id)
            filas = conn.execute(query, params).mappings().all()
            return [respuesta_cita(fila) for fila in filas]

    @app.post("/usuarios/{usuario_id}/citas", response_model=CitaRespuesta, status_code=201)
    def crear_cita(usuario_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual)):
        exigir_usuario(token_id, usuario_id)
        cambios = leer_cambios(datos, nueva=True)
        cita_id = leer_id_cita(datos["id"]) if "id" in datos else str(uuid.uuid4())
        momento = ahora().replace(microsecond=0)
        nueva = {campo: None for campo in LIMITES}
        nueva.update(cambios)
        nueva.update(
            id=cita_id, usuario_id=usuario_id, creado_en=momento, actualizado_en=momento,
            estado=cambios.get("estado", "programada"),
        )
        with operacion_citas(engine) as conn:
            cuenta_activa(conn, usuario_id)
            try:
                conn.execute(
                    text("""
                        INSERT INTO cita (
                            id, usuario_id, nombre_doctor, especialidad_doctor, telefono_doctor,
                            fecha_hora, motivo, lugar, estado, notas, creado_en, actualizado_en
                        ) VALUES (
                            :id, :usuario_id, :nombre_doctor, :especialidad_doctor, :telefono_doctor,
                            :fecha_hora, :motivo, :lugar, :estado, :notas, :creado_en, :actualizado_en
                        )
                    """).bindparams(*(bindparam(campo, type_=DateTime()) for campo in (
                        "fecha_hora", "creado_en", "actualizado_en"
                    ))),
                    nueva,
                )
            except IntegrityError as exc:
                # MySQL 1062 y SQLite PRIMARYKEY/UNIQUE, incluso ante inserciones concurrentes.
                if (getattr(exc.orig, "args", (None,))[0] == 1062
                        or getattr(exc.orig, "sqlite_errorcode", None) in (1555, 2067)):
                    raise ErrorAPI(409, "Esa cita ya esta registrada")
                raise
            return respuesta_cita(cita_del_usuario(conn, usuario_id, cita_id))

    @app.get("/usuarios/{usuario_id}/citas/{cita_id}", response_model=CitaRespuesta)
    def obtener_cita(usuario_id: str, cita_id: str, token_id: str = Depends(usuario_actual)):
        exigir_usuario(token_id, usuario_id)
        cita_id = leer_id_cita(cita_id)
        with operacion_citas(engine) as conn:
            cuenta_activa(conn, usuario_id)
            return respuesta_cita(cita_del_usuario(conn, usuario_id, cita_id))

    @app.patch("/usuarios/{usuario_id}/citas/{cita_id}", response_model=CitaRespuesta)
    def actualizar_cita(
        usuario_id: str, cita_id: str, datos: dict = Body(...), token_id: str = Depends(usuario_actual),
    ):
        exigir_usuario(token_id, usuario_id)
        cita_id = leer_id_cita(cita_id)
        cambios = leer_cambios(datos)
        with operacion_citas(engine) as conn:
            cuenta_activa(conn, usuario_id)
            cita_del_usuario(conn, usuario_id, cita_id)
            if cambios:
                cambios["actualizado_en"] = ahora().replace(microsecond=0)
                # Las columnas vienen de leer_cambios, nunca de claves arbitrarias del cliente.
                asignaciones = ", ".join(f"{campo} = :{campo}" for campo in cambios)
                query = text(
                    f"UPDATE cita SET {asignaciones} WHERE usuario_id = :usuario_id AND id = :id"
                ).bindparams(*(bindparam(campo, type_=DateTime()) for campo in (
                    "fecha_hora", "actualizado_en"
                ) if campo in cambios))
                conn.execute(query, {**cambios, "usuario_id": usuario_id, "id": cita_id})
            return respuesta_cita(cita_del_usuario(conn, usuario_id, cita_id))

    @app.delete("/usuarios/{usuario_id}/citas/{cita_id}", status_code=204)
    def borrar_cita(usuario_id: str, cita_id: str, token_id: str = Depends(usuario_actual)):
        exigir_usuario(token_id, usuario_id)
        cita_id = leer_id_cita(cita_id)
        with operacion_citas(engine) as conn:
            cuenta_activa(conn, usuario_id)
            cita_del_usuario(conn, usuario_id, cita_id)
            # La FK existente medicina.cita_id ON DELETE SET NULL conserva las medicinas.
            conn.execute(
                text("DELETE FROM cita WHERE usuario_id = :usuario_id AND id = :id"),
                {"usuario_id": usuario_id, "id": cita_id},
            )
        return Response(status_code=204)
