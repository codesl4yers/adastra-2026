"""Extractores por formato: pdf, json, tabular (csv/xlsx), imagen, pbf y texto.

Todos exponen la misma firma, sin estado global y sin escribir a disco::

    def extraer(path: Path, fenomeno: int) -> Documento

Ninguno propaga excepciones: ante un archivo corrupto devuelve un ``Documento``
válido con ``bloques=[]`` y el motivo en ``errores``.

:mod:`extractores.comun` (pila de encabezados, filtros de ruido, construcción del
``Documento``) y :mod:`extractores.ocr` (Tesseract) no son extractores.

Qué se extrae de cada formato y qué se descarta:
``docs/decisiones/extraccion-por-formato.md``.
"""
