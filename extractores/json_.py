"""Extractor de JSON. STUB: falta implementar.

El módulo se llama ``json_`` para no ensombrecer al ``json`` de la biblioteca
estándar dentro del paquete.

Estrategia
----------
El JSON no tiene orden de lectura ni jerarquía de títulos, pero sí una
jerarquía de claves, que es un breadcrumb natural.

1. Recorrer el árbol en profundidad y en orden estable: las claves de cada
   objeto, ordenadas alfabéticamente; los elementos de cada lista, en su orden.
   Ordenar las claves es lo que hace la salida reproducible entre corridas.
2. ``ruta`` = camino de claves ancestras (``["propiedades", "geometria"]``).
   Para los elementos de una lista, usar el índice en la ruta.
3. Las hojas de texto se emiten como ``tipo="parrafo"``. Los objetos que
   representan un registro completo (todas sus hojas son escalares) se emiten
   como un único bloque ``tipo="fila"`` con ``atomico=True``: partir un registro
   entre chunks distintos lo vuelve irrecuperable.
4. Descartar hojas que no son lenguaje natural: identificadores, UUID, URLs
   sueltas, marcas de tiempo y números. Van a ``meta`` si son descriptivas.

La trampa principal
-------------------
Un GeoJSON es un JSON, pero el 99 % de sus bytes son coordenadas numéricas. Un
recorrido ingenuo emite cientos de miles de bloques inútiles y ahoga el índice.
Hay que detectar la estructura GeoJSON (claves ``type``, ``features``,
``geometry``) y extraer solo ``properties`` de cada feature, dejando la
geometría fuera del texto y, si acaso, su bounding box en ``meta``.

Segunda trampa: la profundidad no está acotada. Un JSON con recursión profunda
revienta la pila si el recorrido es recursivo; conviene iterativo con límite de
profundidad explícito.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATO = "json"


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo JSON."""
    raise NotImplementedError("extractor de JSON pendiente: ver la estrategia en el docstring")
