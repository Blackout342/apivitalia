"""Entrada para AWS Lambda. En la Mac se sigue corriendo con uvicorn views:app."""

from mangum import Mangum

from views import app

# La app no usa eventos de arranque/apagado, así que no hace falta el lifespan.
handler = Mangum(app, lifespan="off")
