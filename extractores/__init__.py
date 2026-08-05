"""Extractores por formato.

Todos exponen exactamente la misma firma::

    def extraer(path: Path, fenomeno: int) -> Documento

Sin estado global, sin efectos secundarios y sin escribir a disco: la
persistencia es responsabilidad de :mod:`orquestador`.

Ningún extractor puede propagar una excepción. Ante un archivo corrupto,
ilegible o de un formato que no entiende, devuelve un ``Documento`` válido con
``bloques=[]`` y el motivo en ``errores``.

Formatos registrados: pdf, json, tabular (csv/xlsx), imagen (con OCR), pbf
(tiles vectoriales) y texto (txt/md). No hay extractor de HTML: el corpus real
de ADL no trae archivos de ese formato.

Dos módulos no son extractores y no exponen ``extraer``:

- :mod:`extractores.comun` — la pila de encabezados que exige el contrato, el
  filtro de lo que no es lenguaje natural y la construcción del ``Documento``.
  Tres cosas que se repetían en PDF, JSON y texto y que es fácil implementar
  mal de forma distinta en cada sitio.
- :mod:`extractores.ocr` — Tesseract, compartido por imagen y PDF escaneado.
"""
