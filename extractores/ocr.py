"""Reconocimiento óptico de caracteres, compartido por imagen y PDF escaneado.

Tesseract es un binario del sistema, no un paquete de Python: si falta, este
módulo informa en vez de reventar, y por eso se comprueba antes de cada uso.

Dos cosas que conviene tener presentes: no es determinista entre versiones —de
ahí que se fijen idiomas y configuración y se registre la versión en ``meta``— y
nunca falla, porque ante una imagen sin texto devuelve basura plausible. De ahí
el filtro de confianza por línea.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

IDIOMAS = "spa+eng+por"

# Motor LSTM y segmentación automática de página. Cambiarlos cambia el texto.
CONFIGURACION = "--oem 3 --psm 3"

# Confianza media mínima de una línea, en la escala 0-100 de Tesseract.
UMBRAL_CONFIANZA = 60.0

# pytesseract solo busca "tesseract" por PATH, y en Windows el instalador no
# siempre lo agrega.
_RUTAS_TESSERACT_WINDOWS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@lru_cache(maxsize=1)
def hay_ocr() -> bool:
    """``True`` si se puede reconocer texto ahora mismo.

    Comprueba las dos mitades, el paquete y el binario: tener ``pytesseract`` sin
    Tesseract detrás es el caso habitual y el que más despista. Prueba el PATH y
    luego las rutas de instalación por defecto de Windows.
    """
    try:
        import pytesseract
    except Exception:  # noqa: BLE001 - sin el paquete, no hay nada que probar
        return False

    for candidato in (None, *_RUTAS_TESSERACT_WINDOWS):
        if candidato is not None:
            if not Path(candidato).is_file():
                continue
            pytesseract.pytesseract.tesseract_cmd = candidato
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001 - se prueba el siguiente candidato
            continue
    return False


def motivo_sin_ocr() -> str:
    """Explicación para el campo ``errores`` del documento."""
    return (
        "no hay OCR disponible: falta el binario de Tesseract o el paquete "
        "pytesseract. Instalar Tesseract con los idiomas spa+eng+por y "
        "re-extraer para recuperar estos documentos."
    )


def version() -> str | None:
    """Versión del binario, para dejarla registrada en ``meta``."""
    if not hay_ocr():
        return None
    import pytesseract

    return str(pytesseract.get_tesseract_version())


def texto_de_imagen(imagen: Any) -> tuple[str, float]:
    """Reconoce el texto de una imagen PIL. Devuelve ``(texto, confianza media)``.

    Sin OCR disponible devuelve ``("", 0.0)``: para el documento resultante es el
    mismo caso que una imagen sin texto reconocible.
    """
    if not hay_ocr():
        return "", 0.0

    import pytesseract

    try:
        datos = pytesseract.image_to_data(
            _preparar(imagen),
            lang=IDIOMAS,
            config=CONFIGURACION,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:  # noqa: BLE001 - un fallo del binario no tumba la corrida
        return "", 0.0

    lineas = lineas_fiables(datos, UMBRAL_CONFIANZA)
    if not lineas:
        return "", 0.0

    texto = "\n".join(texto for texto, _ in lineas)
    confianza = sum(conf for _, conf in lineas) / len(lineas)
    return texto, round(confianza, 2)


def _preparar(imagen: Any) -> Any:
    """Escala de grises: el único preprocesado que no puede empeorar el resultado."""
    try:
        return imagen.convert("L")
    except Exception:  # noqa: BLE001 - si no se puede convertir, se usa tal cual
        return imagen


def lineas_fiables(datos: dict, umbral: float) -> list[tuple[str, float]]:
    """Reagrupa la salida de ``image_to_data`` en líneas y descarta las dudosas.

    Viene una fila por palabra, con su posición en bloque/párrafo/línea. Las de
    ``conf`` -1 son separadores estructurales, no palabras.
    """
    palabras: dict[tuple[int, int, int], list[tuple[str, float]]] = {}

    for indice, texto in enumerate(datos.get("text", [])):
        limpio = str(texto).strip()
        if not limpio:
            continue
        try:
            confianza = float(datos["conf"][indice])
        except (TypeError, ValueError):
            continue
        if confianza < 0:
            continue

        clave = (
            datos["block_num"][indice],
            datos["par_num"][indice],
            datos["line_num"][indice],
        )
        palabras.setdefault(clave, []).append((limpio, confianza))

    lineas = []
    for clave in sorted(palabras):
        contenido = palabras[clave]
        media = sum(confianza for _, confianza in contenido) / len(contenido)
        if media >= umbral:
            lineas.append((" ".join(texto for texto, _ in contenido), media))
    return lineas
