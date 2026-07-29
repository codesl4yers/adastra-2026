"""Reglas transversales de limpieza, compartidas por todos los extractores.

Este módulo no conoce el contrato de datos: trabaja solo con cadenas. Cualquier
extractor puede usarlo sin arrastrar dependencias.

Todo lo que hay aquí es determinista: mismas entradas, mismas salidas, sin
importar el orden de ejecución ni el valor de ``PYTHONHASHSEED``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from functools import lru_cache

# Idiomas admitidos por el contrato. El orden fija el desempate del detector.
IDIOMAS_SOPORTADOS: tuple[str, ...] = ("es", "en", "pt")

# Por debajo de esta longitud no hay evidencia suficiente para detectar idioma.
MINIMO_CARACTERES_IDIOMA = 20

# Categorías Unicode de espacio que se colapsan a un espacio simple.
_CATEGORIAS_ESPACIO = {"Zs", "Zl", "Zp"}

# Categorías Unicode que se eliminan por completo: controles (Cc) y caracteres
# de formato invisibles (Cf: zero-width space, BOM, marcas de dirección...).
_CATEGORIAS_INVISIBLES = {"Cc", "Cf"}

# Espacios en blanco ASCII que deben convertirse en espacio, no eliminarse,
# aunque su categoría Unicode sea Cc.
_ESPACIOS_ASCII = "\t\n\r\f\v"


def normalizar_texto(texto: str) -> str:
    """Devuelve el texto en la forma canónica que exige el contrato.

    - Normaliza a Unicode NFC.
    - Convierte cualquier espacio (incluido el duro) en espacio simple.
    - Elimina caracteres de control y de ancho cero.
    - Colapsa espacios repetidos y recorta los extremos.

    Es idempotente: ``normalizar_texto(normalizar_texto(x)) == normalizar_texto(x)``.
    """
    if not texto:
        return ""

    caracteres = []
    for caracter in texto:
        if caracter in _ESPACIOS_ASCII:
            caracteres.append(" ")
            continue
        categoria = unicodedata.category(caracter)
        if categoria in _CATEGORIAS_ESPACIO:
            caracteres.append(" ")
        elif categoria in _CATEGORIAS_INVISIBLES:
            continue  # invisible: no aporta y rompe las comparaciones
        else:
            caracteres.append(caracter)

    # split() sin argumentos colapsa espacios y recorta los extremos de una vez.
    return unicodedata.normalize("NFC", " ".join("".join(caracteres).split()))


def es_texto_util(texto: str) -> bool:
    """``True`` si el texto aporta algo una vez normalizado.

    Los extractores deben descartar los bloques para los que devuelve ``False``:
    el contrato prohíbe bloques vacíos.
    """
    return bool(normalizar_texto(texto))


# Patrones de líneas que solo aportan estructura de impresión, nunca contenido.
_PATRONES_RUIDO = (
    # "12", "- 7 -", "— 3 —", "_4_"
    re.compile(r"^[\s\-–—_.]*\d+[\s\-–—_.]*$"),
    # "Página 3", "Pag. 3 de 12", "Page 4 of 20"
    re.compile(
        r"^p[aá]g(?:ina)?\.?\s*\d+(?:\s*(?:de|of|/)\s*\d+)?$",
        re.IGNORECASE,
    ),
    re.compile(r"^page\s*\d+(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE),
    # "3 / 12"
    re.compile(r"^\d+\s*/\s*\d+$"),
)


def es_ruido_estructural(texto: str) -> bool:
    """``True`` si la línea es numeración de página o similar, sin contenido.

    Complementa a :func:`lineas_repetidas`: esta función juzga una línea aislada
    por su forma; aquella juzga un conjunto de líneas por su frecuencia.
    """
    limpio = normalizar_texto(texto)
    if not limpio:
        return True
    return any(patron.match(limpio) for patron in _PATRONES_RUIDO)


def lineas_repetidas(
    unidades: Sequence[Iterable[str]],
    umbral: float = 0.6,
    minimo_unidades: int = 3,
) -> list[str]:
    """Detecta líneas que se repiten entre unidades: cabeceras, pies, menús.

    Cada "unidad" es una página de un PDF, una hoja de un XLSX o una sección de
    un HTML: lo que en ese formato se repite cuando algo es boilerplate. Una
    línea se considera repetida si aparece en al menos ``umbral`` de las
    unidades. Se cuenta *en cuántas unidades* aparece, no cuántas veces en
    total: tres repeticiones dentro de una misma página son énfasis, no cabecera.

    Devuelve la lista **ordenada alfabéticamente** para que el resultado sea
    estable entre corridas. Con menos de ``minimo_unidades`` devuelve lista
    vacía: no hay evidencia suficiente para hablar de repetición.
    """
    materializadas = [list(unidad) for unidad in unidades]
    if len(materializadas) < minimo_unidades:
        return []

    apariciones: Counter[str] = Counter()
    for unidad in materializadas:
        # sorted() sobre el set: el conteo no depende del orden, pero el
        # recorrido explícito documenta que no hay iteración sobre un set.
        for linea in sorted(set(unidad)):
            apariciones[linea] += 1

    minimo = umbral * len(materializadas)
    return sorted(linea for linea, veces in apariciones.items() if veces >= minimo)


# --- detección de idioma -----------------------------------------------------

try:  # pragma: no cover - depende del entorno
    from langdetect import DetectorFactory, LangDetectException, detect

    # Sin esta semilla langdetect es no determinista entre corridas.
    DetectorFactory.seed = 0
    _HAY_LANGDETECT = True
except ImportError:  # pragma: no cover - entorno sin la dependencia opcional
    _HAY_LANGDETECT = False

# Palabras funcionales que discriminan entre los tres idiomas del contrato.
# Se usan como respaldo cuando langdetect no está o devuelve un idioma ajeno
# al contrato (p. ej. francés o italiano en un documento suelto).
_PALABRAS_FUNCIONALES: dict[str, frozenset[str]] = {
    "es": frozenset(
        """el la los las un una unos unas de del que y en por con para se su sus al lo
        como más pero ya este esta estos estas entre cuando muy sin sobre también hasta
        hay donde desde todo todos nos ni contra ese esa eso antes después mientras
        porque aunque cada otro otra otros otras fue son ser está están había"""
        .split()
    ),
    "en": frozenset(
        """the of and to in a is that for it with as was on are by this from or an which
        you have has not but at were their they we can all been more when there if no one
        its also over last three years during each other than then them these those"""
        .split()
    ),
    "pt": frozenset(
        """o a os as um uma uns umas de do da dos das que e em no na nos nas por com para
        se ao aos à às não mais mas como ou pelo pela pelos pelas seu sua seus suas foi
        são ser está estão havia entre quando muito sem sobre também até onde desde cada"""
        .split()
    ),
}

_PALABRAS = re.compile(r"[^\W\d_]+", re.UNICODE)


def _idioma_por_palabras_funcionales(texto: str, por_defecto: str) -> str:
    """Respaldo determinista: cuenta palabras funcionales de cada idioma."""
    palabras = [p.lower() for p in _PALABRAS.findall(texto)]
    if not palabras:
        return por_defecto

    puntajes = {
        idioma: sum(1 for palabra in palabras if palabra in vocabulario)
        for idioma, vocabulario in _PALABRAS_FUNCIONALES.items()
    }
    # max() sobre una tupla fija: en caso de empate gana siempre el mismo
    # idioma, así que el resultado no depende del orden del diccionario.
    mejor = max(IDIOMAS_SOPORTADOS, key=lambda idioma: puntajes[idioma])
    return mejor if puntajes[mejor] > 0 else por_defecto


@lru_cache(maxsize=2048)
def detectar_idioma(texto: str, por_defecto: str = "es") -> str:
    """Detecta el idioma del texto y lo restringe a los del contrato.

    Devuelve siempre uno de ``IDIOMAS_SOPORTADOS``. Con texto demasiado corto
    para decidir, devuelve ``por_defecto``.

    El resultado es determinista: langdetect lleva la semilla fijada y el
    respaldo por palabras funcionales no usa aleatoriedad. El caché solo evita
    recalcular; no cambia el resultado.
    """
    limpio = normalizar_texto(texto)
    if len(limpio) < MINIMO_CARACTERES_IDIOMA:
        return por_defecto

    if _HAY_LANGDETECT:
        try:
            detectado = detect(limpio)
        except LangDetectException:
            detectado = None
        if detectado in IDIOMAS_SOPORTADOS:
            return detectado

    return _idioma_por_palabras_funcionales(limpio, por_defecto)
