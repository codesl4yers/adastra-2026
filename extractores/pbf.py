"""Extractor de PBF (mapas vectoriales). STUB: falta implementar.

Ojo con el nombre: ``.pbf`` designa dos formatos distintos que se parsean con
librerías distintas. Lo primero que debe hacer este extractor es distinguirlos.

- **Mapbox Vector Tile** (``.pbf`` / ``.mvt``): un tile de mapa vectorial. Se
  lee con ``mapbox-vector-tile``. Trae capas, cada una con features que llevan
  ``properties``.
- **OpenStreetMap PBF** (``.osm.pbf``): un volcado de OSM comprimido en
  Protocol Buffers. Se lee con ``osmium`` o ``esy-osmfilter``. Trae nodos, vías
  y relaciones con etiquetas ``key=value``.

Estrategia
----------
1. Detectar cuál de los dos es, por la extensión compuesta y por la cabecera
   del archivo (los OSM PBF empiezan con un bloque ``OSMHeader``).
2. El contenido con valor textual son las **propiedades**, no la geometría: los
   topónimos (``name``), la clasificación (``landuse``, ``natural``,
   ``highway``) y las referencias administrativas.
3. Emitir un bloque ``tipo="fila"`` con ``atomico=True`` por feature, con sus
   propiedades serializadas como ``"clave: valor"``. Una feature es un registro
   indivisible, igual que una fila de CSV.
4. ``ruta`` = ``[nombre_de_la_capa]``. Volcar a ``meta`` la extensión geográfica
   (bounding box), el nivel de zoom y las coordenadas del tile: es lo que
   permite responder preguntas espaciales sin indexar la geometría.
5. Recorrer capas y features en orden explícito y ordenado por identificador.
   El orden en que la librería devuelve las features no está garantizado entre
   versiones, y sin ordenar la salida deja de ser reproducible.

La trampa principal
-------------------
Un tile vectorial casi no contiene lenguaje natural: son coordenadas
codificadas en delta y enteros. Intentar convertirlo a texto plano produce
megabytes de ruido numérico que destruyen la recuperación sin aportar nada. La
geometría no debe entrar al índice de texto; solo las propiedades y, como
metadato, la extensión geográfica.

Segunda trampa: las coordenadas de un tile son relativas al propio tile, no
geográficas. Sin convertirlas a latitud/longitud usando ``z/x/y``, cualquier
dato espacial que se derive de ellas es incorrecto aunque parezca razonable.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATO = "pbf"

EXTENSIONES = (".pbf", ".mvt")


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un tile vectorial o un volcado OSM."""
    raise NotImplementedError("extractor de PBF pendiente: ver la estrategia en el docstring")
