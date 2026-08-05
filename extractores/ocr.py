"""Reconocimiento óptico de caracteres, compartido por imagen y PDF escaneado.

Lo usan dos extractores: :mod:`extractores.imagen` para los 9 archivos de
imagen del corpus y :mod:`extractores.pdf` para el ~3 % de PDF que vienen
escaneados y devuelven cero caracteres sin dar ningún error.

Tesseract es un **binario del sistema**, no un paquete de Python. Si no está
instalado, este módulo no revienta: informa. Ese es todo el contrato de
:func:`hay_ocr`, y por eso se comprueba antes de cada uso en vez de dejar que
salte una excepción a mitad de una corrida de 1826 documentos.

Determinismo
------------
Tesseract no es determinista de fábrica entre versiones: la misma imagen puede
dar texto ligeramente distinto con otro binario o con otros ``tessdata``. Fijar
semillas en Python no sirve de nada porque el proceso es externo. Lo que sí se
hace: fijar idiomas y configuración (:data:`IDIOMAS`, :data:`CONFIGURACION`) y
registrar la versión usada en ``meta``, de modo que un cambio de versión se
pueda tratar por lo que es —un cambio de corpus que obliga a reindexar— en vez
de como una diferencia inexplicable entre dos corridas.

El filtro de confianza
----------------------
El OCR nunca falla: ante una imagen sin texto devuelve basura plausible
(``"|| ,-. l1"``). Sin filtro, esa basura entra al índice indistinguible del
texto bueno. Se descarta por línea, no por palabra, porque una palabra dudosa
dentro de una frase legible casi siempre es correcta y quitarla dejaría un
hueco en mitad de la oración.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

IDIOMAS = "spa+eng+por"
"""Los tres idiomas del contrato. Cambiarlos cambia el texto reconocido."""

CONFIGURACION = "--oem 3 --psm 3"
"""Motor LSTM y segmentación automática de página. Fijos en todas las corridas."""

UMBRAL_CONFIANZA = 60.0
"""Confianza media mínima de una línea, en la escala 0-100 de Tesseract."""

# Rutas donde el instalador oficial de Tesseract para Windows deja el binario.
# ``pytesseract`` busca "tesseract" por PATH y nada más; en Windows eso falla
# seguido porque el instalador no siempre lo agrega, o queda una entrada de
# PATH de una instalación anterior en otra carpeta que ya no existe. Probar
# estas rutas por defecto evita depender de que el PATH esté bien configurado.
_RUTAS_TESSERACT_WINDOWS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


@lru_cache(maxsize=1)
def hay_ocr() -> bool:
    """``True`` si se puede reconocer texto ahora mismo.

    Comprueba las dos mitades: el paquete de Python y el binario. Tener
    ``pytesseract`` instalado sin Tesseract detrás es el caso habitual y el que
    más despista, porque el import funciona y el fallo aparece mucho después.

    Primero prueba el PATH tal cual (lo normal en Linux/macOS, donde el
    instalador de paquetes sí lo deja accesible); si falla, prueba las rutas de
    instalación por defecto de Windows antes de rendirse.
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

    Sin OCR disponible devuelve ``("", 0.0)`` en vez de lanzar: quien llama ya
    tiene que manejar el caso de una imagen sin texto reconocible, y son el
    mismo caso desde el punto de vista del documento resultante.
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
    """Escala de grises antes de reconocer.

    Es el preprocesado que más aporta y el único que no puede empeorar el
    resultado. Binarizar con un umbral fijo sí lo empeora en mapas y gráficos,
    que es la mitad de las imágenes de este corpus.
    """
    try:
        return imagen.convert("L")
    except Exception:  # noqa: BLE001 - si no se puede convertir, se usa tal cual
        return imagen


def lineas_fiables(datos: dict, umbral: float) -> list[tuple[str, float]]:
    """Reagrupa la salida de ``image_to_data`` en líneas y descarta las dudosas.

    ``image_to_data`` devuelve una fila por palabra con su confianza y su
    posición en la jerarquía bloque/párrafo/línea. Las filas con ``conf`` -1 son
    separadores estructurales, no palabras.
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
