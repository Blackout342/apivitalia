"""Pruebas de GET /anuncios sobre SQLite en memoria, sin leer .env ni conectar MySQL."""

import unittest
from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Column, Date, MetaData, String, Table, Text, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

# test_citas importa views con credenciales ficticias y sin tocar la base.
from test_citas import views


class AnunciosTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        metadata = MetaData()
        self.anuncios = Table(
            "anuncio", metadata, Column("id", String(36), primary_key=True),
            Column("titulo", String(150), nullable=False), Column("descripcion", Text, nullable=False),
            Column("fecha", Date, nullable=False),
        )
        metadata.create_all(self.engine)
        # 25 de octubre a las 12:00 UTC: en Monterrey ya es el 25
        self.momento = datetime(2026, 10, 25, 12, 0, 0)

        engine_previo, ahora_previo = views.engine, views.ahora
        views.engine = self.engine
        views.ahora = lambda: self.momento

        def restaurar():
            views.engine = engine_previo
            views.ahora = ahora_previo

        self.addCleanup(restaurar)
        self.client = TestClient(views.app)
        self.addCleanup(self.client.close)

    def insertar(self, id, titulo, fecha):
        with self.engine.begin() as conn:
            conn.execute(self.anuncios.insert(), {
                "id": id, "titulo": titulo, "descripcion": "Texto de " + titulo, "fecha": fecha,
            })

    def test_sin_token_mas_nuevo_primero_a_medianoche_de_monterrey(self):
        self.insertar("a", "Viejo", date(2026, 10, 20))
        self.insertar("b", "Nuevo", date(2026, 10, 25))
        respuesta = self.client.get("/anuncios")
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        anuncios = respuesta.json()
        self.assertEqual([a["titulo"] for a in anuncios], ["Nuevo", "Viejo"])
        self.assertEqual(set(anuncios[0]), {"id", "titulo", "contenido", "publicado_en"})
        self.assertEqual(anuncios[0]["contenido"], "Texto de Nuevo")
        fecha = datetime.fromisoformat(anuncios[0]["publicado_en"].replace("Z", "+00:00"))
        self.assertEqual(fecha.utcoffset(), timedelta(hours=-6))
        self.assertEqual(fecha.replace(tzinfo=None), datetime(2026, 10, 25))

    def test_dias_futuros_no_salen_hasta_ese_dia_en_monterrey(self):
        self.insertar("a", "Hoy", date(2026, 10, 25))
        self.insertar("b", "Mañana", date(2026, 10, 26))
        self.assertEqual([a["titulo"] for a in self.client.get("/anuncios").json()], ["Hoy"])
        # 03:00 UTC del 26 sigue siendo el 25 en Monterrey
        self.momento = datetime(2026, 10, 26, 3, 0, 0)
        self.assertEqual(len(self.client.get("/anuncios").json()), 1)
        self.momento = datetime(2026, 10, 26, 6, 0, 0)
        self.assertEqual(len(self.client.get("/anuncios").json()), 2)

    def test_vacio(self):
        self.assertEqual(self.client.get("/anuncios").json(), [])

    def test_error_de_base_503(self):
        class EngineRoto:
            def connect(self):
                raise SQLAlchemyError("caida")

        views.engine = EngineRoto()
        with self.assertLogs(level="ERROR"):
            respuesta = self.client.get("/anuncios")
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.json(), {"error": "No se pudo consultar la base de datos"})


if __name__ == "__main__":
    unittest.main()
