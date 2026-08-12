"""Extractor de PDF: 760 documentos, ~31.000 páginas y 2,9 GB del corpus.

Se usa ``pdfplumber`` porque hacen falta coordenadas y tamaños de fuente, no solo
el texto. El recorrido de una página es: palabras → columnas (por el corredor
vertical vacío) → líneas → párrafos (por el interlineado dominante) → títulos
(por el tamaño relativo al cuerpo del documento).

Dos cosas que no se ven en el código y conviene saber: las palabras de una página
no sobreviven a su página, porque materializarlas todas mata al worker con
``MemoryError``; y las páginas ilegibles o escaneadas se sustituyen por OCR una a
una, solo si el reconocimiento aporta más texto útil que el original.

El detalle está en ``docs/decisiones/extraccion-por-formato.md`` §3 y, para el
OCR y sus umbrales, en ``docs/decisiones/fragmentos-fuera-de-norma.md`` §7.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber

from contrato import NIVEL_MAXIMO, Bloque, Documento
from extractores import ocr
from extractores.comun import Jerarquia, construir_documento, documento_fallido
from limpieza import es_ruido_estructural, lineas_repetidas, normalizar_texto

# pdfminer avisa por cada fuente sin FontBBox parseable: decenas de líneas en
# stderr por corrida, entre las que se pierden los errores de verdad.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

FORMATO = "pdf"

# Misma línea si las alturas difieren menos de esta fracción del tamaño: las
# tildes y los subíndices desplazan el 'top' unas décimas.
TOLERANCIA_LINEA = 0.25

# Separa párrafos un hueco mayor que el interlineado por este factor. Por debajo
# de 1.4 se parte dentro de un párrafo justificado.
FACTOR_PARRAFO = 1.5

# Es título si supera al cuerpo por este factor: 10.0 y 10.2 son la misma fuente.
FACTOR_TITULO = 1.15

# Corredor entre columnas: ancho mínimo en fracción de página, zona en la que
# tiene que caer (un margen ancho no separa columnas) y reparto mínimo.
CORREDOR_MINIMO = 0.025
ZONA_CENTRAL = (0.25, 0.75)
MINIMO_PALABRAS_COLUMNAS = 40
MINIMO_POR_COLUMNA = 0.2

# Por debajo de esta densidad de caracteres por página, el PDF está escaneado.
MINIMO_CARACTERES_POR_PAGINA = 40

# El OCR cuesta ~1 s por página. El tope es alto porque el documento roto más
# largo del corpus tiene 179 páginas, todas recuperables.
MAXIMO_PAGINAS_OCR = 200
RESOLUCION_OCR = 200

# El OCR no informa de tamaños de fuente; estas líneas quedan fuera del cálculo
# de niveles y el valor solo existe para que `Linea` sea una sola estructura.
TAMANO_SIN_FUENTE = 0.0

# Un encabezado es corto: un pie de autores en cuerpo grande no es un título.
MAXIMO_PALABRAS_TITULO = 20

# Lo que deja pdfplumber cuando la fuente embebida no trae ToUnicode.
_MARCA_CID = re.compile(r"\(cid:\d+\)")

# Proporciones a partir de las que una página se considera ilegible. Calibradas
# contra los 757 PDF con texto del corpus; las mediciones están en el doc de
# fragmentos fuera de norma.
MAXIMA_PROPORCION_CID = 0.30
MAXIMA_PROPORCION_SUELTAS = 0.40

# Solo letras latinas: los alfabetos que se escriben sin separar palabras con
# espacios darían falsos positivos y mandarían a OCR documentos legibles.
_LETRA_LATINA_SUELTA = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñÇç]$")


@dataclass(frozen=True)
class Linea:
    """Una línea de texto reconstruida, con lo justo para poder agruparla."""

    texto: str
    tamano: float
    top: float
    bottom: float
    x0: float


@dataclass(frozen=True)
class Parrafo:
    """Líneas consecutivas del mismo tamaño y sin huecos entre ellas."""

    texto: str
    tamano: float


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un PDF. Nunca lanza.

    La decisión de usar OCR es por página, no por documento.
    """
    path = Path(path)
    try:
        with pdfplumber.open(str(path)) as documento_pdf:
            lineas_por_pagina = lineas_del_documento(documento_pdf)
            meta: dict[str, Any] = {"n_paginas": len(lineas_por_pagina)}
            paginas_ocr = _reconocer_paginas_ilegibles(documento_pdf, lineas_por_pagina, meta)
            bloques = _bloques_de_lineas(lineas_por_pagina, paginas_ocr)
    except Exception as exc:  # noqa: BLE001 - un PDF corrupto no tumba la corrida
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"PDF ilegible ({type(exc).__name__}): {exc}",
        )

    errores = _errores_de(meta, bloques)
    return construir_documento(
        fuente=path.name,
        formato=FORMATO,
        fenomeno=fenomeno,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


def lineas_del_documento(documento_pdf) -> list[list[Linea]]:
    """Líneas de cada página, resolviendo las columnas página a página.

    Las palabras de una página no sobreviven a su página, y ``page.close()`` no
    es decorativo: pdfplumber cachea los objetos y sin vaciarlo el ahorro se
    pierde. Admite un objeto de pdfplumber o una ruta, para poder probarlo suelto.
    """
    if isinstance(documento_pdf, (str, Path)):
        with pdfplumber.open(str(documento_pdf)) as abierto:
            return lineas_del_documento(abierto)

    paginas: list[list[Linea]] = []
    for pagina in documento_pdf.pages:
        palabras = _palabras_de_pagina(pagina)
        # El ancho es el de la página física: el corredor se busca dentro de la
        # zona ocupada, así que un margen ancho no se confunde con una columna.
        paginas.append(ordenar_por_columnas(palabras, float(pagina.width or 0)))
        pagina.close()
    return paginas


def _palabras_de_pagina(pagina) -> list[dict]:
    """Palabras de una página con tamaño de fuente y coordenadas."""
    return pagina.extract_words(extra_attrs=["size", "fontname"])


def _texto_ilegible(lineas_por_pagina: list[list[Linea]]) -> bool:
    """``True`` si hay capa de texto pero no se puede leer.

    Dos formas: fuente sin ``ToUnicode``, que devuelve ``(cid:NN)`` por carácter,
    y letras dibujadas sueltas (``L i f e c y c l e  c o s t``). Ninguna la ve
    :func:`_parece_escaneado`, que mide densidad. Los umbrales son altos a
    propósito: el texto nativo con defectos es mejor que el OCR si se puede leer.
    """
    texto = " ".join(linea.texto for lineas in lineas_por_pagina for linea in lineas)
    if not texto.strip():
        return False  # sin texto no hay nada que juzgar: eso es un escaneado

    caracteres_cid = sum(len(marca.group()) for marca in _MARCA_CID.finditer(texto))
    if caracteres_cid / len(texto) > MAXIMA_PROPORCION_CID:
        return True

    palabras = texto.split()
    sueltas = sum(1 for palabra in palabras if _LETRA_LATINA_SUELTA.match(palabra))
    return bool(palabras) and sueltas / len(palabras) > MAXIMA_PROPORCION_SUELTAS


def _parece_escaneado(lineas_por_pagina: list[list[Linea]]) -> bool:
    """Densidad media de caracteres por página por debajo del mínimo."""
    if not lineas_por_pagina:
        return True
    caracteres = sum(
        len(linea.texto) for lineas in lineas_por_pagina for linea in lineas
    )
    return caracteres / len(lineas_por_pagina) < MINIMO_CARACTERES_POR_PAGINA


# --- líneas, columnas y párrafos ------------------------------------------------


def agrupar_en_lineas(palabras: list[dict]) -> list[Linea]:
    """Agrupa palabras por altura y las ordena por ``x0``: el orden de dibujo del
    PDF tampoco es el de lectura dentro de una línea."""
    if not palabras:
        return []

    ordenadas = sorted(palabras, key=lambda w: (round(w["top"], 1), w["x0"]))
    grupos: list[list[dict]] = [[ordenadas[0]]]

    for palabra in ordenadas[1:]:
        referencia = grupos[-1][0]
        tolerancia = max(2.0, float(referencia.get("size", 10.0)) * TOLERANCIA_LINEA)
        if abs(palabra["top"] - referencia["top"]) <= tolerancia:
            grupos[-1].append(palabra)
        else:
            grupos.append([palabra])

    return [_linea_de(grupo) for grupo in grupos]


def _linea_de(grupo: list[dict]) -> Linea:
    palabras = sorted(grupo, key=lambda w: w["x0"])
    tamanos = Counter(round(float(w.get("size", 0.0)), 1) for w in palabras)
    return Linea(
        texto=" ".join(w["text"] for w in palabras),
        # El dominante y no el máximo: una versalita no hace título a un párrafo.
        tamano=tamanos.most_common(1)[0][0],
        top=min(w["top"] for w in palabras),
        bottom=max(w["bottom"] for w in palabras),
        x0=palabras[0]["x0"],
    )


def detectar_corte_de_columnas(palabras: list[dict], ancho: float) -> float | None:
    """Coordenada X del corredor vertical que separa dos columnas, o ``None``.

    Se proyectan las palabras sobre el eje horizontal y se busca la racha vacía
    más ancha; que caiga en el centro es lo que distingue un corredor de un margen.
    """
    if len(palabras) < MINIMO_PALABRAS_COLUMNAS or ancho <= 0:
        return None

    bandas = 200
    paso = ancho / bandas
    ocupadas = [False] * bandas
    for palabra in palabras:
        desde = max(0, int(palabra["x0"] / paso))
        hasta = min(bandas - 1, int(palabra["x1"] / paso))
        for indice in range(desde, hasta + 1):
            ocupadas[indice] = True

    racha = _racha_vacia_mas_ancha(ocupadas, bandas)
    if racha is None:
        return None

    inicio, fin = racha
    if (fin - inicio + 1) * paso < CORREDOR_MINIMO * ancho:
        return None

    corte = (inicio + fin + 1) / 2 * paso
    if not ZONA_CENTRAL[0] * ancho <= corte <= ZONA_CENTRAL[1] * ancho:
        return None

    izquierda = sum(1 for w in palabras if _centro(w) < corte)
    minimo = MINIMO_POR_COLUMNA * len(palabras)
    if izquierda < minimo or len(palabras) - izquierda < minimo:
        return None

    return corte


def _racha_vacia_mas_ancha(ocupadas: list[bool], bandas: int) -> tuple[int, int] | None:
    """La racha de bandas libres más ancha dentro de la zona ocupada: recortarla
    es lo que evita confundir los márgenes de la página con un corredor."""
    if not any(ocupadas):
        return None

    primero = ocupadas.index(True)
    ultimo = bandas - 1 - ocupadas[::-1].index(True)

    mejor: tuple[int, int] | None = None
    inicio: int | None = None
    for indice in range(primero, ultimo + 1):
        if not ocupadas[indice]:
            if inicio is None:
                inicio = indice
            continue
        if inicio is not None:
            if mejor is None or (indice - 1 - inicio) > (mejor[1] - mejor[0]):
                mejor = (inicio, indice - 1)
            inicio = None
    return mejor


def _centro(palabra: dict) -> float:
    return (palabra["x0"] + palabra["x1"]) / 2


def ordenar_por_columnas(palabras: list[dict], ancho: float) -> list[Linea]:
    """Líneas de la página en orden de lectura, respetando las columnas."""
    corte = detectar_corte_de_columnas(palabras, ancho)
    if corte is None:
        return agrupar_en_lineas(palabras)

    izquierda = [w for w in palabras if _centro(w) < corte]
    derecha = [w for w in palabras if _centro(w) >= corte]
    return agrupar_en_lineas(izquierda) + agrupar_en_lineas(derecha)


def tamano_del_cuerpo(tamanos: list[float]) -> float:
    """El tamaño de fuente más frecuente del documento entero: por página, una
    portada o una página de tablas tendrían su propia moda."""
    if not tamanos:
        return 0.0
    return Counter(round(t, 1) for t in tamanos).most_common(1)[0][0]


def niveles_por_tamano(tamanos: list[float], cuerpo: float) -> dict[float, int]:
    """Asigna un nivel de título a cada tamaño mayor que el cuerpo.

    Del orden de los escalones y no del valor en puntos: 14 pt puede ser nivel 1
    en un documento y nivel 3 en otro.
    """
    if cuerpo <= 0:
        return {}

    mayores = sorted(
        {round(t, 1) for t in tamanos if round(t, 1) > cuerpo * FACTOR_TITULO}, reverse=True
    )
    return {tamano: min(nivel, NIVEL_MAXIMO) for nivel, tamano in enumerate(mayores, start=1)}


def parece_titulo(texto: str) -> bool:
    """Un encabezado es corto: el tamaño solo no basta, y tomar un párrafo por
    título lo mete en el breadcrumb de todo lo que venga detrás."""
    return 0 < len(normalizar_texto(texto).split()) <= MAXIMO_PALABRAS_TITULO


def agrupar_en_parrafos(lineas: list[Linea]) -> list[Parrafo]:
    """Une líneas consecutivas en párrafos.

    Abre párrafo cuando el hueco supera al interlineado o cuando cambia el tamaño
    de fuente; sin lo segundo, un título pegado a su párrafo saldría de una pieza.
    """
    if not lineas:
        return []

    interlineado = _interlineado(lineas)
    parrafos: list[Parrafo] = []
    actual = [lineas[0]]

    for anterior, linea in zip(lineas, lineas[1:]):
        hueco = linea.top - anterior.top
        # Contra el mayor entre interlineado y tamaño de la línea: un titular de
        # 40 pt salta 45 pt entre sus líneas y no son párrafos distintos.
        umbral = max(interlineado, anterior.tamano) * FACTOR_PARRAFO
        corta = (
            linea.tamano != anterior.tamano
            or hueco > umbral
            or hueco < 0  # cambio de columna: la segunda arranca arriba del todo
        )
        if corta:
            parrafos.append(_parrafo_de(actual))
            actual = []
        actual.append(linea)

    parrafos.append(_parrafo_de(actual))
    return parrafos


def _interlineado(lineas: list[Linea]) -> float:
    """Separación vertical más habitual entre líneas consecutivas.

    La moda y no la media: a la media la arrastran los saltos entre párrafos, que
    son justo lo que se quiere detectar.
    """
    huecos = [
        round(siguiente.top - anterior.top)
        for anterior, siguiente in zip(lineas, lineas[1:])
        if 0 < siguiente.top - anterior.top < 200
    ]
    if not huecos:
        return 12.0

    frecuencias = Counter(huecos)
    mayor = max(frecuencias.values())
    return float(min(hueco for hueco, veces in frecuencias.items() if veces == mayor))


def _parrafo_de(lineas: list[Linea]) -> Parrafo:
    return Parrafo(
        texto=" ".join(linea.texto for linea in lineas),
        tamano=statistics.mode([linea.tamano for linea in lineas]),
    )


# --- ensamblado del documento ---------------------------------------------------


def _bloques_de_lineas(
    lineas_por_pagina: list[list[Linea]], paginas_ocr: frozenset[int] = frozenset()
) -> list[Bloque | None]:
    """Convierte las líneas en bloques, sabiendo cuáles vienen del OCR.

    Las páginas reconocidas quedan fuera del cálculo de tamaños —no tienen fuente
    real y desplazarían la moda— y se emiten con el tipo ``ocr`` del contrato.
    """
    descartables = _descartables(lineas_por_pagina)
    tamanos = [
        linea.tamano
        for numero, lineas in enumerate(lineas_por_pagina, start=1)
        if numero not in paginas_ocr
        for linea in lineas
    ]
    cuerpo = tamano_del_cuerpo(tamanos)
    niveles = niveles_por_tamano(tamanos, cuerpo)

    jerarquia = Jerarquia()
    bloques: list[Bloque | None] = []

    for numero, lineas in enumerate(lineas_por_pagina, start=1):
        utiles = [
            linea
            for linea in lineas
            if normalizar_texto(linea.texto) not in descartables
            and not es_ruido_estructural(linea.texto)
        ]

        if numero in paginas_ocr:
            # Sin tamaños de fuente no hay jerarquía que deducir: una línea, un bloque.
            for linea in utiles:
                bloques.append(jerarquia.ocr(linea.texto, pagina=numero))
            continue

        for parrafo in agrupar_en_parrafos(utiles):
            nivel = niveles.get(parrafo.tamano)
            if nivel is not None and parece_titulo(parrafo.texto):
                bloques.append(jerarquia.titulo(parrafo.texto, nivel, pagina=numero))
            else:
                bloques.append(jerarquia.parrafo(parrafo.texto, pagina=numero))

    return bloques


def _descartables(lineas_por_pagina: list[list[Linea]]) -> set[str]:
    """Cabeceras, pies y marcas de agua: lo que se repite página tras página."""
    unidades = [[normalizar_texto(linea.texto) for linea in lineas] for lineas in lineas_por_pagina]
    return set(lineas_repetidas(unidades))


# --- páginas que hay que reconocer -----------------------------------------------


def _reconocer_paginas_ilegibles(
    documento_pdf, lineas_por_pagina: list[list[Linea]], meta: dict[str, Any]
) -> frozenset[int]:
    """Sustituye por OCR las páginas cuyo texto nativo no se puede leer.

    Modifica ``lineas_por_pagina`` en el sitio y devuelve las páginas reconocidas
    (1-based). Sin Tesseract deja ``meta["requiere_ocr"]`` para poder recuperarlas
    después con ``--reintentar-errores``.
    """
    ilegibles = _paginas_a_reconocer(lineas_por_pagina)
    if not ilegibles:
        return frozenset()

    meta["requiere_ocr"] = True
    meta["ocr_disponible"] = ocr.hay_ocr()
    if not meta["ocr_disponible"]:
        return frozenset()

    meta["version_tesseract"] = ocr.version()
    if len(ilegibles) > MAXIMO_PAGINAS_OCR:
        meta["ocr_truncado_en"] = MAXIMO_PAGINAS_OCR
        ilegibles = ilegibles[:MAXIMO_PAGINAS_OCR]

    reconocidas: list[int] = []
    sin_recuperar: list[int] = []
    confianzas: list[float] = []

    for numero in ilegibles:
        lineas, confianza = _lineas_por_ocr(documento_pdf.pages[numero - 1])
        if not _mejora(lineas, lineas_por_pagina[numero - 1]):
            sin_recuperar.append(numero)
            continue
        lineas_por_pagina[numero - 1] = lineas
        reconocidas.append(numero)
        confianzas.append(confianza)

    if confianzas:
        meta["confianza_ocr"] = round(sum(confianzas) / len(confianzas), 2)
    if reconocidas:
        meta["paginas_ocr"] = reconocidas
    if sin_recuperar:
        meta["paginas_sin_recuperar"] = sin_recuperar

    return frozenset(reconocidas)


def _mejora(reconocidas: list[Linea], nativas: list[Linea]) -> bool:
    """``True`` si el OCR aporta más contenido del que ya había en la página.

    Detectar que una página está rota no basta para reemplazarla: en las páginas
    mixtas el diagnóstico es correcto y el OCR devolvería menos de lo que hay. Se
    compara texto útil y no caracteres en bruto, porque los CID abultan nueve
    caracteres por letra sin aportar nada.
    """
    return len(_texto_util(reconocidas)) > len(_texto_util(nativas))


def _texto_util(lineas: list[Linea]) -> str:
    """El texto sin lo que no es contenido: marcadores CID y letras sueltas."""
    sin_cid = _MARCA_CID.sub(" ", " ".join(linea.texto for linea in lineas))
    return " ".join(
        palabra for palabra in sin_cid.split() if not _LETRA_LATINA_SUELTA.match(palabra)
    )


def _errores_de(meta: dict[str, Any], bloques: list[Bloque | None]) -> list[str]:
    """Traduce el estado de la extracción a la lista de ``errores``.

    Que no se reconociera ninguna página tiene dos causas distintas —falta
    Tesseract, o el OCR no mejoraba— y solo la primera se arregla instalando algo.
    """
    if meta.get("requiere_ocr") and not meta.get("ocr_disponible"):
        return [
            f"PDF con páginas sin capa de texto legible y sin OCR disponible. "
            f"{ocr.motivo_sin_ocr()}"
        ]
    if not any(bloques):
        return ["el PDF no produjo ningún bloque de texto"]

    sin_recuperar = meta.get("paginas_sin_recuperar") or []
    if sin_recuperar:
        return [
            f"{len(sin_recuperar)} página(s) con texto ilegible que el OCR no "
            f"mejoró; se conserva el texto original: {sin_recuperar[:10]}"
        ]
    return []


def _paginas_a_reconocer(lineas_por_pagina: list[list[Linea]]) -> list[int]:
    """Números de página (1-based) cuyo texto nativo no sirve.

    Los dos criterios tienen granularidad distinta a propósito: el texto roto se
    juzga página a página, y la falta de texto solo cuenta si es del documento
    entero —cualquier informe tiene portadas y separadores casi vacíos—.
    """
    escaneado = _parece_escaneado(lineas_por_pagina)
    return [
        numero
        for numero, lineas in enumerate(lineas_por_pagina, start=1)
        if _texto_ilegible([lineas]) or (escaneado and _pagina_sin_texto(lineas))
    ]


def _pagina_sin_texto(lineas: list[Linea]) -> bool:
    return sum(len(linea.texto) for linea in lineas) < MINIMO_CARACTERES_POR_PAGINA


def _lineas_por_ocr(pagina) -> tuple[list[Linea], float]:
    """Rasteriza y reconoce una página. Devuelve líneas sintéticas: el tamaño y la
    geometría son de relleno, porque el OCR no informa de cuerpos de letra."""
    try:
        imagen = pagina.to_image(resolution=RESOLUCION_OCR).original
    except Exception:  # noqa: BLE001 - una página que no rasteriza no para el resto
        return [], 0.0

    texto, confianza = ocr.texto_de_imagen(imagen)
    lineas = [
        Linea(
            texto=renglon,
            tamano=TAMANO_SIN_FUENTE,
            top=float(indice),
            bottom=float(indice) + 1.0,
            x0=0.0,
        )
        for indice, renglon in enumerate(texto.splitlines())
        if renglon.strip()
    ]
    return lineas, confianza
