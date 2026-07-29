"""Extractor de imágenes por OCR. STUB: falta implementar.

Estrategia
----------
1. Abrir con ``Pillow`` y volcar a ``meta`` lo que se sepa sin mirar los
   píxeles: dimensiones, EXIF con fecha de captura y, si existen, las etiquetas
   de geolocalización. En un corpus satelital eso suele valer más que el propio
   OCR.
2. Preprocesar antes de reconocer: escala de grises, binarización y corrección
   de inclinación. El OCR sobre la imagen cruda pierde mucho en mapas escaneados.
3. Ejecutar Tesseract (``pytesseract``) con los idiomas del contrato
   (``spa+eng+por``) y **fijar la configuración**: el mismo ``--psm`` y el mismo
   ``--oem`` en todas las corridas, porque cambiarlos cambia el texto.
4. Emitir bloques con ``tipo="ocr"``. Descartar los que tengan confianza media
   por debajo de un umbral explícito: el OCR de baja confianza es ruido que
   contamina el índice y no se distingue del texto bueno una vez indexado.
5. Guardar la confianza media en ``meta`` para poder auditar después qué
   documentos entraron con mala calidad.

La trampa principal
-------------------
Tesseract no es determinista de fábrica en todas las versiones ni entre
plataformas: la misma imagen puede dar texto ligeramente distinto con otra
versión del binario o de los datos de idioma. Como es una dependencia externa
al proceso de Python, no basta con fijar semillas. Hay que anclar la versión de
Tesseract y de los ``tessdata``, dejarlas registradas en ``meta`` y tratar
cualquier cambio de versión como un cambio de corpus que obliga a reindexar.

Segunda trampa: el OCR nunca falla, siempre devuelve algo. Una imagen sin texto
produce cadenas de basura plausible ("|| ,-. l1"). Sin el filtro de confianza,
esa basura acaba indexada como si fuera contenido.
"""

from __future__ import annotations

from pathlib import Path

from contrato import Documento

FORMATO = "imagen"

EXTENSIONES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde una imagen, vía OCR."""
    raise NotImplementedError("extractor de imágenes pendiente: ver la estrategia en el docstring")
