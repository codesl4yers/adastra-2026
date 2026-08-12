"""Extractor de imágenes: metadata primero, OCR después.

Nueve archivos del corpus, ocho JPEG y un AVIF. El orden importa: lo que se sabe
sin mirar los píxeles —dimensiones, formato, EXIF— se obtiene siempre, aunque no
haya Tesseract o la imagen no tenga una letra, y en material satelital suele
valer más que el texto reconocido.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contrato import Bloque, Documento
from extractores import ocr
from extractores.comun import Jerarquia, construir_documento, documento_fallido
from limpieza import es_ruido_estructural

FORMATO = "imagen"

# .avif necesita el plugin `pillow-avif-plugin`; sin él, el documento sale con el
# motivo en `errores`. Un solo archivo del corpus lo usa (F2-SWF-065).
EXTENSIONES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".avif")

# Por número, que es lo que devuelve Pillow sin depender de su tabla de nombres.
_EXIF_FECHA = 36867  # DateTimeOriginal
_EXIF_GPS = 34853


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde una imagen. Nunca lanza."""
    path = Path(path)

    try:
        import PIL.Image
    except ImportError:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo="falta Pillow: no se puede abrir la imagen",
        )

    _registrar_plugin_avif()

    try:
        with PIL.Image.open(path) as imagen:
            meta = _metadata_de(imagen)
            bloques, errores = _texto_de(imagen, meta)
    except Exception as exc:  # noqa: BLE001 - una imagen corrupta no tumba la corrida
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"imagen ilegible ({type(exc).__name__}): {exc}",
        )

    return construir_documento(
        fuente=path.name,
        formato=FORMATO,
        fenomeno=fenomeno,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


def _registrar_plugin_avif() -> None:
    """Habilita AVIF si el plugin está instalado. Su ausencia no es un error aquí."""
    try:
        import pillow_avif  # noqa: F401  - el import es el que registra el formato
    except ImportError:
        pass


def _metadata_de(imagen) -> dict[str, Any]:
    """Lo que se sabe de la imagen sin mirar un solo píxel."""
    meta: dict[str, Any] = {
        "ancho": imagen.width,
        "alto": imagen.height,
        "formato_imagen": imagen.format,
        "modo": imagen.mode,
    }

    try:
        exif = imagen.getexif()
    except Exception:  # noqa: BLE001 - un EXIF corrupto no invalida la imagen
        return meta

    if not exif:
        return meta

    fecha = exif.get(_EXIF_FECHA)
    if isinstance(fecha, str) and fecha.strip():
        meta["fecha_captura"] = fecha.strip()
    if exif.get(_EXIF_GPS):
        meta["tiene_gps"] = True

    return meta


def _texto_de(imagen, meta: dict[str, Any]) -> tuple[list[Bloque | None], list[str]]:
    """Reconoce el texto, o explica por qué no puede."""
    if not ocr.hay_ocr():
        meta["requiere_ocr"] = True
        return [], [ocr.motivo_sin_ocr()]

    meta["version_tesseract"] = ocr.version()
    texto, confianza = ocr.texto_de_imagen(imagen)
    if not texto:
        return [], ["el OCR no reconoció texto fiable en la imagen"]

    meta["confianza_ocr"] = confianza
    jerarquia = Jerarquia()
    bloques: list[Bloque | None] = [
        jerarquia.ocr(linea) for linea in texto.splitlines() if not es_ruido_estructural(linea)
    ]

    errores = [] if any(bloques) else ["el OCR solo reconoció ruido estructural"]
    return bloques, errores
