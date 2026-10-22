"""Validacion compartida de JWT, sin configuracion, credenciales ni conexiones."""

import jwt


class ErrorAPI(Exception):
    """Error de negocio con la forma publica {"error": "..."}."""

    def __init__(self, codigo: int, mensaje: str):
        self.codigo = codigo
        self.mensaje = mensaje


def usuario_desde_token(authorization: str | None, secreto: str, algoritmo: str = "HS256") -> str:
    if not authorization:
        raise ErrorAPI(401, "Falta el token")
    partes = authorization.split(" ", 1)
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1].strip():
        raise ErrorAPI(401, "Falta el token")
    try:
        datos = jwt.decode(partes[1].strip(), secreto, algorithms=[algoritmo])
    except jwt.ExpiredSignatureError:
        raise ErrorAPI(401, "La sesi\u00f3n venci\u00f3")
    except jwt.PyJWTError:
        raise ErrorAPI(401, "El token no es v\u00e1lido")
    usuario_id = datos.get("sub")
    if not isinstance(usuario_id, str) or not usuario_id:
        raise ErrorAPI(401, "El token no es v\u00e1lido")
    return usuario_id


def exigir_propio(token_id: str, ruta_id: str) -> None:
    if token_id != ruta_id:
        raise ErrorAPI(403, "No tienes acceso a esta cuenta")
