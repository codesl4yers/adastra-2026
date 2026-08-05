"""Extractor de tiles vectoriales (.pbf, .mvt).

Ojo con el nombre: ``.pbf`` designa dos formatos distintos que se leen con
librerías distintas, y lo primero que hace este extractor es distinguirlos.

- **Mapbox Vector Tile**: un tile de mapa. Se lee con ``mapbox-vector-tile``.
  Trae capas, cada una con features que llevan ``properties``. Son los 73
  archivos del corpus, todos de Amazon Underworld.
- **OpenStreetMap PBF** (``.osm.pbf``): un volcado de OSM en Protocol Buffers.
  Necesita ``osmium`` y no está en el corpus, así que se detecta por su cabecera
  y se reporta en vez de intentar leerlo con la librería equivocada, que daría
  un error incomprensible.

Qué se indexa
-------------
Las **propiedades**, no la geometría: los topónimos, la clasificación
administrativa y, en este corpus, qué grupo armado tiene presencia en cada
municipio. Cada feature es un bloque atómico, igual que una fila de CSV: es un
registro completo y partirlo rompe la correspondencia ``clave: valor``.

Las propiedades cuyo valor es una **negación** (``FALSO``, ``false``, ``0``) se
descartan. En este corpus cada municipio trae una docena de banderas de las que
casi todas son falsas: repetir "au_eln: FALSO" en 250 features no distingue nada
y desplaza del vector al contenido que sí discrimina.

Las dos trampas
---------------
1. Un tile casi no contiene lenguaje natural: son coordenadas codificadas en
   delta. Convertirlo a texto plano produce megabytes de ruido numérico que
   destruyen la recuperación. La geometría no entra al índice.
2. Las coordenadas de un tile son **relativas al propio tile**, no geográficas.
   Derivar lat/lon de ellas sin cuidado da datos que parecen razonables y son
   falsos. Por eso el ``bbox`` se calcula del tile ``z/x/y`` —que es exacto por
   definición— y no de las geometrías.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from contrato import Bloque, Documento
from extractores.comun import Jerarquia, construir_documento, documento_fallido, es_valor_opaco
from limpieza import normalizar_texto

FORMATO = "pbf"

EXTENSIONES = (".pbf", ".mvt")

# Valores que expresan ausencia. Un vector lleno de negaciones no discrimina.
VALORES_NEGATIVOS = frozenset({"falso", "false", "no", "0", "none", "null", "nao", "não"})

# Cabecera de un volcado de OpenStreetMap: bloque "OSMHeader" en los primeros bytes.
_MARCA_OSM = b"OSMHeader"

# Tope de features por tile. Los tiles del corpus rondan las 250; uno de
# 100.000 puntos sería ruido, no contenido.
MAXIMO_FEATURES = 5000

_NUMERO = re.compile(r"^\d+$")


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un tile vectorial. Nunca lanza."""
    path = Path(path)

    try:
        crudo = path.read_bytes()
    except OSError as exc:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"no se pudo leer el archivo ({type(exc).__name__}): {exc}",
        )

    if _MARCA_OSM in crudo[:64]:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=(
                "es un volcado OSM PBF, no un tile vectorial: requiere osmium "
                "y no está soportado por este extractor"
            ),
        )

    try:
        import mapbox_vector_tile
    except ImportError:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo="falta mapbox-vector-tile: no se puede decodificar el tile",
        )

    try:
        tile = mapbox_vector_tile.decode(crudo)
    except Exception as exc:  # noqa: BLE001 - un tile corrupto no tumba la corrida
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"tile vectorial ilegible ({type(exc).__name__}): {exc}",
        )

    meta: dict[str, Any] = _metadata_del_tile(path)
    bloques, n_features = _bloques_de_tile(tile, meta)

    meta["n_features"] = n_features
    errores = [] if any(bloques) else ["el tile no tiene propiedades con contenido indexable"]
    return construir_documento(
        fuente=path.name,
        formato=FORMATO,
        fenomeno=fenomeno,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


def _bloques_de_tile(tile: dict, meta: dict[str, Any]) -> tuple[list[Bloque | None], int]:
    """Una capa, una sección; una feature, un registro atómico.

    Las capas se recorren ordenadas por nombre y las features por su ``fid``:
    el orden en que la librería las devuelve no está garantizado entre
    versiones, y sin ordenar la salida deja de ser reproducible.
    """
    jerarquia = Jerarquia()
    bloques: list[Bloque | None] = []
    capas: list[str] = []
    total = 0

    for nombre in sorted(tile):
        features = tile[nombre].get("features", [])
        textos = [
            texto
            for texto in (
                _texto_de_feature(feature) for feature in _ordenadas(features)[:MAXIMO_FEATURES]
            )
            if texto
        ]
        if not textos:
            continue

        capas.append(nombre)
        total += len(textos)
        bloques.append(jerarquia.titulo(nombre, 1))
        bloques.extend(jerarquia.fila(texto) for texto in textos)

    meta["capas"] = capas
    return bloques, total


def _ordenadas(features: list) -> list:
    """Ordena por ``fid`` cuando lo hay; si no, conserva el orden del archivo.

    El ``id`` que expone la librería vale 0 en todos los features de este
    corpus, así que no sirve para desempatar.
    """
    return sorted(
        features,
        key=lambda feature: _clave_de_orden(feature.get("properties", {})),
    )


def _clave_de_orden(propiedades: dict) -> tuple[int, str]:
    fid = propiedades.get("fid")
    if isinstance(fid, int):
        return (0, f"{fid:012d}")
    return (1, normalizar_texto(str(fid)) if fid is not None else "")


def _texto_de_feature(feature: dict) -> str:
    """Serializa las propiedades útiles de una feature. La geometría se ignora."""
    propiedades = feature.get("properties")
    if not isinstance(propiedades, dict):
        return ""

    campos = [
        f"{normalizar_texto(str(clave))}: {normalizar_texto(str(valor))}"
        for clave, valor in sorted(propiedades.items())
        if _es_util(clave, valor)
    ]
    return " | ".join(campos)


def _es_util(clave: Any, valor: Any) -> bool:
    if not isinstance(clave, str) or not clave.strip():
        return False
    if isinstance(valor, bool):
        return valor
    if es_valor_opaco(valor):
        return False
    return normalizar_texto(str(valor)).lower() not in VALORES_NEGATIVOS


# --- geografía del tile ---------------------------------------------------------


def _metadata_del_tile(path: Path) -> dict[str, Any]:
    """Deduce ``z/x/y`` de la ruta y calcula el bounding box exacto del tile.

    La convención de los directorios de tiles es ``.../{z}/{x}/{y}.pbf``. En
    este corpus el nombre del archivo lleva un prefijo de observatorio
    (``AMAZONUW_3.pbf``), así que la ``y`` se busca entre los dígitos del nombre.
    """
    zoom, tile_x, tile_y = _coordenadas_del_tile(path)
    if zoom is None:
        return {}

    meta: dict[str, Any] = {"zoom": zoom, "tile_x": tile_x}
    if tile_y is None:
        return meta

    meta["tile_y"] = tile_y
    meta["bbox"] = _bbox_de_tile(zoom, tile_x, tile_y)
    return meta


def _coordenadas_del_tile(path: Path) -> tuple[int | None, int | None, int | None]:
    partes = path.parent.parts
    if len(partes) < 2 or not (_NUMERO.match(partes[-1]) and _NUMERO.match(partes[-2])):
        return None, None, None

    zoom, tile_x = int(partes[-2]), int(partes[-1])
    digitos = re.findall(r"\d+", path.stem)
    tile_y = int(digitos[-1]) if digitos else None
    return zoom, tile_x, tile_y


def _bbox_de_tile(zoom: int, tile_x: int, tile_y: int) -> list[float]:
    """Bounding box geográfico del tile en la proyección Web Mercator.

    Se calcula del ``z/x/y`` y no de las geometrías porque así es exacto por
    definición: las coordenadas internas del tile están en su propio sistema y
    convertirlas mal produce datos espaciales verosímiles y equivocados.
    """
    total = 2**zoom
    oeste = tile_x / total * 360.0 - 180.0
    este = (tile_x + 1) / total * 360.0 - 180.0
    norte = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * tile_y / total))))
    sur = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (tile_y + 1) / total))))
    return [round(oeste, 6), round(sur, 6), round(este, 6), round(norte, 6)]
