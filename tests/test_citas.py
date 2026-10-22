"""Pruebas aisladas: nunca importar views ni usar configuracion de produccion."""

import builtins
import os
import sqlite3
import subprocess
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi import FastAPI, Header
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text,
    create_engine, event, select, text,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool


ABRIR = builtins.open


def abrir_sin_credenciales(archivo, *args, **kwargs):
    if isinstance(archivo, (str, os.PathLike)) and Path(archivo).name.startswith(".env"):
        raise AssertionError("Las pruebas no pueden leer .env")
    return ABRIR(archivo, *args, **kwargs)


with (
    patch("builtins.open", side_effect=abrir_sin_credenciales),
    patch("sqlalchemy.create_engine", side_effect=AssertionError("Conexion durante import")),
    patch.object(Engine, "connect", side_effect=AssertionError("Conexion durante import")),
):
    from citas import registrar_rutas_citas
    from seguridad import ErrorAPI, usuario_desde_token


USUARIO = "11111111-1111-4111-8111-111111111111"
OTRO = "22222222-2222-4222-8222-222222222222"
INACTIVO = "33333333-3333-4333-8333-333333333333"
SECRETO = "solo-pruebas-aisladas-no-es-una-clave-real-ni-se-usa-en-ningun-servidor"
NUEVA = {"nombre_doctor": "Dra. Ana", "fecha_hora": "2026-10-22T15:00:00Z"}
CLAVES = {
    "id", "usuario_id", "nombre_doctor", "especialidad_doctor", "telefono_doctor",
    "fecha_hora", "motivo", "lugar", "estado", "notas", "creado_en", "actualizado_en",
}


class CitasTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def activar_fk(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys = ON")

        metadata = MetaData()
        self.usuarios = Table(
            "usuario", metadata, Column("id", String(36), primary_key=True),
            Column("activo", Integer, nullable=False),
        )
        self.citas = Table(
            "cita", metadata, Column("id", String(36), primary_key=True),
            Column("usuario_id", String(36), ForeignKey("usuario.id"), nullable=False),
            Column("nombre_doctor", String(150), nullable=False),
            Column("especialidad_doctor", String(100)), Column("telefono_doctor", String(15)),
            Column("fecha_hora", DateTime, nullable=False), Column("motivo", String(255)),
            Column("lugar", String(255)), Column("estado", String(10), nullable=False),
            Column("notas", Text), Column("creado_en", DateTime, nullable=False),
            Column("actualizado_en", DateTime, nullable=False),
        )
        self.medicinas = Table(
            "medicina", metadata, Column("id", String(36), primary_key=True),
            Column("cita_id", String(36), ForeignKey("cita.id", ondelete="SET NULL")),
            Column("nombre", String(150), nullable=False), Column("dosis", String(100)),
            Column("indicaciones", Text),
        )
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(self.usuarios.insert(), [
                {"id": USUARIO, "activo": 1}, {"id": OTRO, "activo": 1},
                {"id": INACTIVO, "activo": 0},
            ])
        self.momento = datetime(2026, 10, 22, 1, 2, 3)

        def usuario_actual(authorization: str | None = Header(default=None)):
            return usuario_desde_token(authorization, SECRETO)

        app = FastAPI()

        @app.exception_handler(ErrorAPI)
        async def error_api(_, exc):
            return JSONResponse(status_code=exc.codigo, content={"error": exc.mensaje})

        @app.exception_handler(RequestValidationError)
        async def error_validacion(_, __):
            return JSONResponse(status_code=422, content={"error": "Los datos enviados no son validos"})

        registrar_rutas_citas(app, self.engine, usuario_actual, lambda: self.momento)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = f"/usuarios/{USUARIO}/citas"
        self.headers = self.autorizacion(USUARIO)

    def autorizacion(self, usuario, **claims):
        payload = {"sub": usuario, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
        payload.update(claims)
        return {"Authorization": "Bearer " + jwt.encode(payload, SECRETO, algorithm="HS256")}

    def crear(self, datos=None, usuario=USUARIO):
        respuesta = self.client.post(
            f"/usuarios/{usuario}/citas", json=NUEVA if datos is None else datos,
            headers=self.autorizacion(usuario),
        )
        self.assertEqual(respuesta.status_code, 201, respuesta.text)
        return respuesta.json()

    def operaciones(self, base=None, cita_id=None):
        base = base or self.base
        detalle = base + "/" + (cita_id or str(uuid.uuid4()))
        return [
            ("GET", base, {}), ("POST", base, {"json": NUEVA}),
            ("GET", detalle, {}), ("PATCH", detalle, {"json": {"estado": "realizada"}}),
            ("DELETE", detalle, {}),
        ]

    def test_crud_con_contrato_ios_y_auditoria(self):
        cita_id = str(uuid.uuid4())
        cita = self.crear({**NUEVA, "id": cita_id})
        self.assertEqual(set(cita), CLAVES)
        self.assertEqual(cita["id"], cita_id)
        self.assertEqual(cita["usuario_id"], USUARIO)
        self.assertEqual(cita["estado"], "programada")
        for campo in ("especialidad_doctor", "telefono_doctor", "motivo", "lugar", "notas"):
            self.assertIsNone(cita[campo])
        self.assertEqual(cita["fecha_hora"], "2026-10-22T09:00:00-06:00")
        for campo in ("creado_en", "actualizado_en"):
            fecha = datetime.fromisoformat(cita[campo].replace("Z", "+00:00"))
            self.assertEqual(fecha.utcoffset(), timedelta(0))
            self.assertEqual(fecha.replace(tzinfo=None), self.momento)
        url = self.base + "/" + cita_id
        self.assertEqual(self.client.get(url, headers=self.headers).json(), cita)
        self.assertEqual(self.client.get(self.base, headers=self.headers).json(), [cita])
        self.momento += timedelta(minutes=1)
        respuesta = self.client.patch(url, json={"estado": "realizada", "lugar": "Clinica"}, headers=self.headers)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        actualizada = respuesta.json()
        self.assertEqual(actualizada["estado"], "realizada")
        self.assertEqual(actualizada["lugar"], "Clinica")
        self.assertEqual(actualizada["creado_en"], cita["creado_en"])
        self.assertNotEqual(actualizada["actualizado_en"], cita["actualizado_en"])
        self.assertEqual(actualizada["nombre_doctor"], cita["nombre_doctor"])
        respuesta = self.client.delete(url, headers=self.headers)
        self.assertEqual((respuesta.status_code, respuesta.content), (204, b""))
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(self.base, headers=self.headers).json(), [])

    def test_reconciliar_post_duplicado_con_get_sin_sobrescribir(self):
        datos = {**NUEVA, "id": str(uuid.uuid4()), "notas": "Control", "estado": "cancelada"}
        primera = self.crear(datos)
        respuesta = self.client.post(self.base, json=datos, headers=self.headers)
        self.assertEqual(respuesta.status_code, 409, respuesta.text)
        self.assertEqual(self.client.get(self.base + "/" + datos["id"], headers=self.headers).json(), primera)
        respuesta = self.client.post(self.base, json={**datos, "nombre_doctor": "Otro"}, headers=self.headers)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.client.get(self.base + "/" + datos["id"], headers=self.headers).json(), primera)

    def test_uuid_duplicado_de_otro_usuario_no_se_expone(self):
        ajena = self.crear(usuario=OTRO)
        respuesta = self.client.post(self.base, json={**NUEVA, "id": ajena["id"]}, headers=self.headers)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.client.get(self.base + "/" + ajena["id"], headers=self.headers).status_code, 404)

    def test_jwt_mensajes_originales_para_cada_ruta(self):
        token_vencido = self.autorizacion(USUARIO, exp=datetime.now(timezone.utc) - timedelta(seconds=2))
        sin_sub = jwt.encode({"exp": datetime.now(timezone.utc) + timedelta(hours=1)}, SECRETO, algorithm="HS256")
        firma_mala = jwt.encode({"sub": USUARIO}, "otra-clave-solo-para-pruebas-aisladas", algorithm="HS256")
        otro_algoritmo = jwt.encode({"sub": USUARIO}, SECRETO, algorithm="HS512")
        casos = [
            ({}, "Falta el token"), ({"Authorization": "Basic abc"}, "Falta el token"),
            ({"Authorization": "Bearer "}, "Falta el token"),
            ({"Authorization": "Bearer roto"}, "El token no es v\u00e1lido"),
            ({"Authorization": "Bearer " + sin_sub}, "El token no es v\u00e1lido"),
            ({"Authorization": "Bearer " + firma_mala}, "El token no es v\u00e1lido"),
            ({"Authorization": "Bearer " + otro_algoritmo}, "El token no es v\u00e1lido"),
            (self.autorizacion(123), "El token no es v\u00e1lido"),
            (self.autorizacion(""), "El token no es v\u00e1lido"),
            (token_vencido, "La sesi\u00f3n venci\u00f3"),
        ]
        with patch.object(self.engine, "begin", side_effect=AssertionError("JWT debe validarse antes de SQL")):
            for metodo, url, kwargs in self.operaciones():
                for headers, mensaje in casos:
                    with self.subTest(metodo=metodo, url=url, mensaje=mensaje, headers=headers):
                        respuesta = self.client.request(metodo, url, headers=headers, **kwargs)
                        self.assertEqual(respuesta.status_code, 401, respuesta.text)
                        self.assertEqual(respuesta.json(), {"error": mensaje})

    def test_token_ajeno_403_antes_de_sql_en_cada_ruta(self):
        with patch.object(self.engine, "begin", side_effect=AssertionError("No consultar otra cuenta")):
            for metodo, url, kwargs in self.operaciones():
                with self.subTest(metodo=metodo, url=url):
                    respuesta = self.client.request(metodo, url, headers=self.autorizacion(OTRO), **kwargs)
                    self.assertEqual(respuesta.status_code, 403, respuesta.text)
                    self.assertEqual(respuesta.json(), {"error": "No tienes acceso a esta cuenta"})

    def test_cita_ajena_no_se_lee_edita_ni_borra(self):
        ajena = self.crear(usuario=OTRO)
        url = self.base + "/" + ajena["id"]
        for metodo, kwargs in [("GET", {}), ("PATCH", {"json": {"estado": "realizada"}}), ("DELETE", {})]:
            with self.subTest(metodo=metodo):
                self.assertEqual(self.client.request(metodo, url, headers=self.headers, **kwargs).status_code, 404)

    def test_cita_inexistente_404_en_detalle_patch_y_delete(self):
        for metodo, url, kwargs in self.operaciones():
            if url != self.base:
                respuesta = self.client.request(metodo, url, headers=self.headers, **kwargs)
                self.assertEqual(respuesta.status_code, 404, respuesta.text)

    def test_cuenta_inactiva_o_inexistente_en_cada_ruta(self):
        for usuario, esperado in [(INACTIVO, 403), (str(uuid.uuid4()), 404)]:
            for metodo, url, kwargs in self.operaciones(base=f"/usuarios/{usuario}/citas"):
                with self.subTest(usuario=usuario, metodo=metodo, url=url):
                    respuesta = self.client.request(metodo, url, headers=self.autorizacion(usuario), **kwargs)
                    self.assertEqual(respuesta.status_code, esperado, respuesta.text)

    def test_campos_inmutables_y_desconocidos_rechazados(self):
        cita = self.crear()
        for campo, valor in [("id", cita["id"]), ("usuario_id", USUARIO), ("usuario", OTRO),
                             ("creado_en", "2026-10-21T00:00:00Z"), ("actualizado_en", None), ("intruso", "x")]:
            with self.subTest(campo=campo):
                self.assertEqual(self.client.patch(self.base + "/" + cita["id"], json={campo: valor}, headers=self.headers).status_code, 422)
                if campo != "id":
                    self.assertEqual(self.client.post(self.base, json={**NUEVA, campo: valor}, headers=self.headers).status_code, 422)

    def test_validacion_de_uuid(self):
        for valor in [None, "", 1, True, "no-es-uuid", "x' OR 1=1 --"]:
            with self.subTest(valor=valor):
                respuesta = self.client.post(self.base, json={**NUEVA, "id": valor}, headers=self.headers)
                self.assertEqual(respuesta.status_code, 422, respuesta.text)
        for metodo in ("GET", "PATCH", "DELETE"):
            kwargs = {"json": {}} if metodo == "PATCH" else {}
            self.assertEqual(self.client.request(metodo, self.base + "/invalido", headers=self.headers, **kwargs).status_code, 422)
        cita_id = str(uuid.uuid4())
        self.assertEqual(self.crear({**NUEVA, "id": cita_id.upper()})["id"], cita_id)

    def test_usuario_id_uuid_invalido_rechazado_antes_de_sql(self):
        with patch.object(self.engine, "begin", side_effect=AssertionError("UUID antes de SQL")):
            for usuario in ("invalido", "1" * 36, USUARIO.replace("-", "")):
                for metodo, url, kwargs in self.operaciones(base=f"/usuarios/{usuario}/citas"):
                    respuesta = self.client.request(metodo, url, headers=self.autorizacion(usuario), **kwargs)
                    self.assertEqual(respuesta.status_code, 422, respuesta.text)

    def test_validacion_campos_en_post_y_patch(self):
        cita = self.crear()
        casos = [
            ("nombre_doctor", None), ("nombre_doctor", "  "), ("nombre_doctor", 1),
            ("nombre_doctor", "x" * 151), ("especialidad_doctor", "x" * 101),
            ("telefono_doctor", "x" * 16), ("motivo", "x" * 256), ("lugar", "x" * 256),
            ("notas", "x" * 65536), ("notas", "\u00e9" * 32768),
            ("estado", None), ("estado", "pendiente"), ("estado", []),
        ] + [(campo, 5) for campo in ("especialidad_doctor", "telefono_doctor", "motivo", "lugar", "notas")]
        for campo, valor in casos:
            with self.subTest(campo=campo, tipo=type(valor).__name__):
                self.assertEqual(self.client.post(self.base, json={**NUEVA, campo: valor}, headers=self.headers).status_code, 422)
                self.assertEqual(self.client.patch(self.base + "/" + cita["id"], json={campo: valor}, headers=self.headers).status_code, 422)
        for datos in ({}, {"nombre_doctor": "Ana"}, {"fecha_hora": NUEVA["fecha_hora"]}, [], "texto", None):
            self.assertEqual(self.client.post(self.base, json=datos, headers=self.headers).status_code, 422)

    def test_limites_validos_opcionales_nulos_y_patch_vacio(self):
        limites = {"nombre_doctor": 150, "especialidad_doctor": 100, "telefono_doctor": 15,
                   "motivo": 255, "lugar": 255, "notas": 65535}
        cita = self.crear({**NUEVA, **{campo: "x" * limite for campo, limite in limites.items()}})
        url = self.base + "/" + cita["id"]
        self.momento += timedelta(minutes=1)
        self.assertEqual(self.client.patch(url, json={}, headers=self.headers).json(), cita)
        opcionales = set(limites) - {"nombre_doctor"}
        for valor in (None, "   "):
            respuesta = self.client.patch(url, json={campo: valor for campo in opcionales}, headers=self.headers)
            self.assertEqual(respuesta.status_code, 200, respuesta.text)
            for campo in opcionales:
                self.assertIsNone(respuesta.json()[campo])

    def test_offsets_se_normalizan_a_misma_hora_local_y_almacen_sin_zona(self):
        for fecha in ("2026-10-22T15:00:00Z", "2026-10-22T09:00:00-06:00", "2026-10-22T20:30:00+05:30"):
            with self.subTest(fecha=fecha):
                cita = self.crear({**NUEVA, "fecha_hora": fecha})
                self.assertEqual(cita["fecha_hora"], "2026-10-22T09:00:00-06:00")
                with self.engine.connect() as conn:
                    fila = conn.execute(select(self.citas).where(self.citas.c.id == cita["id"])).mappings().one()
                self.assertEqual(fila["fecha_hora"], datetime(2026, 10, 22, 9))
                self.assertIsNone(fila["fecha_hora"].tzinfo)
                self.assertEqual(fila["creado_en"], self.momento)

    def test_patch_fecha_y_fracciones_precision_datetime(self):
        cita = self.crear({**NUEVA, "fecha_hora": "2026-10-22T01:02:03.123456Z"})
        self.assertEqual(cita["fecha_hora"], "2026-10-21T19:02:03-06:00")
        respuesta = self.client.patch(self.base + "/" + cita["id"], json={"fecha_hora": "2026-07-01T12:00:00Z"}, headers=self.headers)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json()["fecha_hora"], "2026-07-01T06:00:00-06:00")

    def test_rechaza_fechas_naive_invalidas_y_offset_desconocido(self):
        cita = self.crear()
        fechas = [None, 123, True, "2026-10-22", "2026-10-22T09:00:00", "2026-10-22 09:00:00",
                  "2026-02-30T00:00:00Z", "2026-10-22T24:00:00Z", "2026-10-22T09:00:00+25:00",
                  "2026-10-22T09:00:00-00:00", "2026-10-22T09:00:00+06:60",
                  "0001-01-01T00:00:00Z", "9999-12-31T23:00:00-06:00"]
        for fecha in fechas:
            with self.subTest(fecha=fecha):
                self.assertEqual(self.client.post(self.base, json={**NUEVA, "fecha_hora": fecha}, headers=self.headers).status_code, 422)
                self.assertEqual(self.client.patch(self.base + "/" + cita["id"], json={"fecha_hora": fecha}, headers=self.headers).status_code, 422)
                if fecha is not None:
                    for filtro in ("desde", "hasta"):
                        self.assertEqual(self.client.get(self.base, params={filtro: fecha}, headers=self.headers).status_code, 422)

    def test_lista_intervalo_semiabierto_orden_y_aislamiento(self):
        inicio = self.crear({**NUEVA, "fecha_hora": "2026-10-22T09:00:00-06:00"})
        final = self.crear({**NUEVA, "fecha_hora": "2026-10-22T10:00:00-06:00"})
        anterior = self.crear({**NUEVA, "fecha_hora": "2026-10-22T08:59:59-06:00"})
        self.crear(usuario=OTRO)
        limites = {"desde": "2026-10-22T15:00:00Z", "hasta": "2026-10-22T18:00:00+02:00"}
        respuesta = self.client.get(self.base, params=limites, headers=self.headers)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json(), [inicio])
        self.assertEqual(self.client.get(self.base, headers=self.headers).json(), [anterior, inicio, final])
        self.assertEqual(self.client.get(self.base, params={"desde": limites["desde"]}, headers=self.headers).json(), [inicio, final])
        self.assertEqual(self.client.get(self.base, params={"hasta": limites["hasta"]}, headers=self.headers).json(), [anterior, inicio])
        self.assertEqual(self.client.get(self.base, params={"desde": "2026-10-22T09:00:00.000001-06:00"}, headers=self.headers).json(), [final])
        self.assertEqual(self.client.get(self.base, params={"hasta": "2026-10-22T09:00:00.000001-06:00"}, headers=self.headers).json(), [anterior, inicio])

    def test_intervalos_invertidos_o_vacios_rechazados(self):
        for hasta in ("2026-10-22T15:00:00Z", "2026-10-22T14:00:00Z"):
            respuesta = self.client.get(self.base, params={"desde": "2026-10-22T09:00:00-06:00", "hasta": hasta}, headers=self.headers)
            self.assertEqual(respuesta.status_code, 422, respuesta.text)

    def test_datos_historicos_se_leen_sin_conversion_en_base(self):
        cita = self.crear({**NUEVA, "fecha_hora": "2021-07-01T16:00:00Z"})
        self.assertEqual(cita["fecha_hora"], "2021-07-01T11:00:00-05:00")
        with self.engine.connect() as conn:
            antes = dict(conn.execute(select(self.citas)).mappings().one())
        self.client.get(self.base, headers=self.headers)
        self.client.get(self.base + "/" + cita["id"], headers=self.headers)
        with self.engine.connect() as conn:
            despues = dict(conn.execute(select(self.citas)).mappings().one())
        self.assertEqual(antes, despues)

    def test_delete_conserva_medicinas_y_anula_solo_fk(self):
        cita = self.crear()
        medicina = {"id": str(uuid.uuid4()), "cita_id": cita["id"], "nombre": "Medicina de prueba",
                    "dosis": "Una dosis", "indicaciones": "Indicaciones guardadas"}
        with self.engine.begin() as conn:
            conn.execute(self.medicinas.insert(), medicina)
            conn.execute(self.medicinas.insert(), {**medicina, "id": str(uuid.uuid4()), "cita_id": None})
        respuesta = self.client.delete(self.base + "/" + cita["id"], headers=self.headers)
        self.assertEqual(respuesta.status_code, 204, respuesta.text)
        with self.engine.connect() as conn:
            medicinas = conn.execute(select(self.medicinas)).mappings().all()
        self.assertEqual(len(medicinas), 2)
        conservada = next(dict(fila) for fila in medicinas if fila["id"] == medicina["id"])
        self.assertEqual(conservada, {**medicina, "cita_id": None})

    def test_sql_injection_se_guarda_como_texto_y_no_como_sql(self):
        valor = "Dr. O'Hara'); DROP TABLE cita; --"
        cita = self.crear({**NUEVA, "nombre_doctor": valor, "notas": valor})
        respuesta = self.client.patch(self.base + "/" + cita["id"], json={"lugar": valor}, headers=self.headers)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        self.assertEqual(respuesta.json()["lugar"], valor)
        self.assertEqual(respuesta.json()["nombre_doctor"], valor)
        usuario = "x' OR 1=1 --"
        respuesta = self.client.get(f"/usuarios/{usuario}/citas", headers=self.autorizacion(usuario))
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(len(self.client.get(self.base, headers=self.headers).json()), 1)

    def test_bindings_datetime_en_insert_update_y_filtros(self):
        capturados = []

        @event.listens_for(self.engine, "before_execute")
        def capturar(_, query, multiparams, params, options):
            sql = str(query)
            if sql.startswith("UPDATE cita") or "INSERT INTO cita" in sql or "fecha_hora >= :desde" in sql:
                compilado = query.compile()
                tipos = {nombre: param.type for nombre, param in compilado.binds.items()}
                capturados.append((sql, tipos, params))

        cita = self.crear()
        self.client.patch(self.base + "/" + cita["id"], json={"fecha_hora": "2026-10-22T16:00:00Z"}, headers=self.headers)
        self.client.get(self.base, params={"desde": "2026-10-22T00:00:00Z", "hasta": "2026-10-23T00:00:00Z"}, headers=self.headers)
        self.assertEqual(len(capturados), 3)
        for _, tipos, params in capturados:
            for campo in ("fecha_hora", "creado_en", "actualizado_en", "desde", "hasta"):
                if campo in params:
                    self.assertIsInstance(tipos[campo], DateTime)
                    self.assertIsInstance(params[campo], datetime)
                    self.assertIsNone(params[campo].tzinfo)

    def test_errores_sql_503_y_rollback(self):
        with patch("citas.cita_del_usuario", side_effect=SQLAlchemyError("detalle interno no publico")):
            with self.assertLogs(level="ERROR"):
                respuesta = self.client.post(self.base, json=NUEVA, headers=self.headers)
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.json(), {"error": "No se pudo completar la operacion"})
        self.assertEqual(self.client.get(self.base, headers=self.headers).json(), [])

    def test_integridad_mysql_duplicada_409_y_otros_errores_503(self):
        original = Connection.execute
        for origen, esperado in [(Exception(1062, "Duplicate entry"), 409), (sqlite3.IntegrityError("otra integridad"), 503)]:
            def ejecutar(conn, query, *args, **kwargs):
                if "INSERT INTO cita" in str(query):
                    raise IntegrityError(str(query), {}, origen)
                return original(conn, query, *args, **kwargs)

            with self.subTest(esperado=esperado), patch.object(Connection, "execute", ejecutar):
                if esperado == 503:
                    with self.assertLogs(level="ERROR"):
                        respuesta = self.client.post(self.base, json=NUEVA, headers=self.headers)
                else:
                    respuesta = self.client.post(self.base, json=NUEVA, headers=self.headers)
                self.assertEqual(respuesta.status_code, esperado, respuesta.text)

    def test_openapi_con_cinco_operaciones(self):
        spec = self.client.get("/openapi.json").json()
        self.assertEqual(set(spec["paths"]["/usuarios/{usuario_id}/citas"]), {"get", "post"})
        self.assertEqual(set(spec["paths"]["/usuarios/{usuario_id}/citas/{cita_id}"]), {"get", "patch", "delete"})
        self.assertEqual(set(spec["components"]["schemas"]["CitaRespuesta"]["properties"]), CLAVES)


class ImportacionSeguraTest(unittest.TestCase):
    def test_importar_y_registrar_no_lee_env_ni_crea_conexiones(self):
        programa = r'''
import builtins
import pathlib
import sys
from unittest.mock import patch
from fastapi import FastAPI
from sqlalchemy.engine import Engine
abrir = builtins.open
def sin_env(archivo, *args, **kwargs):
    if isinstance(archivo, (str, pathlib.Path)) and pathlib.Path(archivo).name.startswith('.env'):
        raise AssertionError('No leer credenciales')
    return abrir(archivo, *args, **kwargs)
with patch('builtins.open', side_effect=sin_env), \
     patch('sqlalchemy.create_engine', side_effect=AssertionError('No crear engine')), \
     patch.object(Engine, 'connect', side_effect=AssertionError('No conectar')), \
     patch.object(Engine, 'begin', side_effect=AssertionError('No iniciar transaccion')):
    from citas import registrar_rutas_citas
    app = FastAPI()
    def usuario_actual():
        raise AssertionError('No autenticar al registrar')
    def ahora():
        raise AssertionError('No escribir al registrar')
    registrar_rutas_citas(app, object(), usuario_actual, ahora)
    app.openapi()
    assert 'views' not in sys.modules
    assert 'pymysql' not in sys.modules
'''
        respuesta = subprocess.run(
            [sys.executable, "-c", programa], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(respuesta.returncode, 0, respuesta.stderr)


if __name__ == "__main__":
    unittest.main()
