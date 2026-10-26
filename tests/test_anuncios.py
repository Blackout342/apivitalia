"""Pruebas de GET /anuncios sobre SQLite en memoria, sin leer .env ni conectar MySQL."""

import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, create_engine
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
            Column("titulo", String(120), nullable=False), Column("contenido", Text, nullable=False),
            Column("publicado_en", DateTime, nullable=False),
        )
        metadata.create_all(self.engine)
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

    def insertar(self, id, titulo, publicado_en):
        with self.engine.begin() as conn:
            conn.execute(self.anuncios.insert(), {
                "id": id, "titulo": titulo, "contenido": "Texto de " + titulo, "publicado_en": publicado_en,
            })

    def test_sin_token_mas_nuevo_primero_y_en_utc(self):
        self.insertar("a", "Viejo", self.momento - timedelta(days=3))
        self.insertar("b", "Nuevo", self.momento - timedelta(hours=1))
        respuesta = self.client.get("/anuncios")
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        anuncios = respuesta.json()
        self.assertEqual([a["titulo"] for a in anuncios], ["Nuevo", "Viejo"])
        self.assertEqual(set(anuncios[0]), {"id", "titulo", "contenido", "publicado_en"})
        self.assertEqual(anuncios[0]["contenido"], "Texto de Nuevo")
        fecha = datetime.fromisoformat(anuncios[0]["publicado_en"].replace("Z", "+00:00"))
        self.assertEqual(fecha.utcoffset(), timedelta(0))
        self.assertEqual(fecha.replace(tzinfo=None), self.momento - timedelta(hours=1))

    def test_programados_no_salen_hasta_su_hora(self):
        self.insertar("a", "Ya salio", self.momento)
        self.insertar("b", "Programado", self.momento + timedelta(minutes=5))
        self.assertEqual([a["titulo"] for a in self.client.get("/anuncios").json()], ["Ya salio"])
        self.momento += timedelta(minutes=5)
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
