"""Extractores por formato.

Todos exponen exactamente la misma firma::

    def extraer(path: Path, fenomeno: int) -> Documento

Sin estado global, sin efectos secundarios y sin escribir a disco: la
persistencia es responsabilidad de :mod:`orquestador`.

Ningún extractor puede propagar una excepción. Ante un archivo corrupto,
ilegible o de un formato que no entiende, devuelve un ``Documento`` válido con
``bloques=[]`` y el motivo en ``errores``.

:mod:`extractores.html` es la implementación de referencia: si vas a completar
uno de los stubs, léelo primero.

Formatos registrados: html, pdf, json, tabular (csv/xlsx), imagen (con OCR),
pbf (mapas vectoriales) y texto (txt/md).
"""
