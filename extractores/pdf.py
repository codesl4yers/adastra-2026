"""Extractor de PDF: 760 documentos, ~31.000 páginas y 2,9 GB del corpus.

Se usa ``pdfplumber`` (MIT) y no un lector que devuelva una cadena por página,
porque hacen falta las coordenadas y el tamaño de fuente de cada palabra para
dos cosas que ningún ``extract_text`` da: distinguir un título de su cuerpo y
reconstruir el orden de lectura de una página a dos columnas.

Cómo se arma un documento
-------------------------
1. Se leen las palabras de cada página con su posición y su tamaño.
2. Se detecta si la página va a dos columnas buscando un **corredor vertical**
   sin palabras, y se lee columna por columna.
3. Las palabras se agrupan en líneas por su altura y las líneas en párrafos por
   el interlineado dominante del propio documento, no por una constante.
4. El **tamaño de fuente más frecuente** es el cuerpo; los tamaños mayores se
   ordenan en escalones y cada escalón es un nivel de título. El nivel sale del
   rango del escalón y no del valor absoluto en puntos, porque cada maquetación
   usa su propia escala.
5. Las líneas que se repiten en la mayoría de las páginas son cabeceras y pies
   (:func:`limpieza.lineas_repetidas`); la numeración se descarta con
   :func:`limpieza.es_ruido_estructural`.

La trampa principal
-------------------
El PDF no tiene orden de lectura: tiene instrucciones de dibujo. En un
documento a dos columnas, ordenar por altura intercala las dos columnas línea a
línea y produce un texto que parece correcto y es basura entreverada. De ahí la
detección del corredor.

Las palabras que **cruzan** el corredor —un título a ancho completo sobre dos
columnas— se asignan a la columna con la que más se solapan. Es un compromiso
consciente: mantiene íntegro el texto de cada columna y como mucho coloca mal un
titular, mientras que tratarlas aparte exigiría segmentar la página en franjas y
multiplicaría los modos de fallo.

La memoria
----------
Las palabras de una página no sobreviven a su página. Un atlas de 250 páginas
son millones de diccionarios de palabra; materializarlos todos antes de
procesar consume cientos de megabytes por documento, y con varios procesos en
paralelo mata el proceso con ``MemoryError``. Solo se conservan las líneas, que
ocupan dos órdenes de magnitud menos.

Segunda trampa
--------------
Un PDF escaneado devuelve cero caracteres sin lanzar ningún error. Se detecta
por densidad de caracteres por página y se manda a OCR
(:mod:`extractores.ocr`). Si no hay Tesseract instalado, el documento sale con
``bloques=[]``, ``meta["requiere_ocr"]`` y el motivo en ``errores``: nunca en
silencio, porque un documento perdido así no se nota hasta el final.
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

# pdfminer avisa por cada fuente cuyo descriptor no trae un FontBBox parseable
# —"Could not get FontBBox from font descriptor"— y en una corrida del corpus
# eso son decenas de líneas en stderr entre las que se pierden los errores de
# verdad. No afecta a la extracción: pdfminer sigue con un recuadro (0,0,0,0).
# Se silencia aquí, que es el único sitio del pipeline que lo usa.
logging.getLogger("pdfminer").setLevel(logging.ERROR)

FORMATO = "pdf"

# Dos palabras están en la misma línea si sus alturas difieren menos de esta
# fracción del tamaño de fuente. Las tildes y los subíndices desplazan el
# 'top' unas décimas y no deben partir la línea.
TOLERANCIA_LINEA = 0.25

# Un hueco vertical mayor que el interlineado dominante por este factor separa
# párrafos. Por debajo de 1.4 se parte dentro de un párrafo justificado.
FACTOR_PARRAFO = 1.5

# Un tamaño de fuente es título si supera al del cuerpo en este factor. 10.0 y
# 10.2 son la misma fuente con otro redondeo, no una jerarquía.
FACTOR_TITULO = 1.15

# Anchura mínima del corredor entre columnas, en fracción del ancho de página.
CORREDOR_MINIMO = 0.025

# El corredor tiene que caer en la zona central: un margen ancho no separa
# columnas.
ZONA_CENTRAL = (0.25, 0.75)

# Con menos palabras que esto, cualquier hueco parece un corredor.
MINIMO_PALABRAS_COLUMNAS = 40

# Cada columna debe llevarse al menos esta fracción de las palabras.
MINIMO_POR_COLUMNA = 0.2

# Por debajo de esta densidad de caracteres por página, el PDF está escaneado.
MINIMO_CARACTERES_POR_PAGINA = 40

# Tope de páginas que se mandan a OCR por documento. El OCR cuesta ~1 s por
# página; sin tope, un escaneado de 500 páginas bloquea la corrida entera.
#
# Era 30, que bastaba mientras el OCR solo atendía escaneados —los cuatro
# documentos truncados del corpus tenían entre 32 y 38 páginas—. Con el criterio
# de texto ilegible entran documentos mucho más largos: `F3-CEOBS-030` son 179
# páginas cuyo texto nativo es CID puro, y truncarlo en 30 tiraría 149 páginas
# de contenido recuperable. A ~1 s por página son unos 3 minutos, y el
# orquestador reparte los documentos entre procesos.
MAXIMO_PAGINAS_OCR = 200

# Resolución a la que se rasteriza una página antes de reconocerla.
RESOLUCION_OCR = 200

# Tamaño que se da a las líneas que vienen del OCR. No es un tamaño de fuente
# real —el reconocimiento no informa de eso— y nunca entra en el cálculo de
# niveles: existe para que `Linea` siga siendo una sola estructura.
TAMANO_SIN_FUENTE = 0.0

# Un encabezado es corto por definición. Un pie de autores o una cita destacada
# pueden ir en cuerpo mayor que el texto y no son encabezados de nada.
MAXIMO_PALABRAS_TITULO = 20

# Marcador que deja pdfplumber cuando la fuente embebida no trae ToUnicode y no
# puede mapear el glifo a un carácter.
_MARCA_CID = re.compile(r"\(cid:\d+\)")

# A partir de qué proporción del texto se manda el documento a OCR. Calibrados
# contra los 757 PDFs con texto del corpus.
#
# El de CID es limpio: "(cid:" no aparece nunca en texto legítimo, y la
# separación es nítida —0,99 y 0,38 en los ilegibles, 0,15 y menos en el resto—.
#
# El de letras sueltas cuenta solo las **latinas**, y eso es lo que lo hace
# utilizable. Contando cualquier carácter suelto, los documentos sanos y los
# rotos se solapaban: `F2-SWF-035` daba 0,48 y `F3-RESDAL-070` 0,50. Pero el
# 0,48 del primero son los caracteres del logotipo de la fundación repetido en
# cinco alfabetos —安 全 世 界, م, ФОНД— y el del segundo son letras latinas de
# palabras destrozadas: "1 ou t o f 9 c a nd i d a t e s". Restringido a
# latinas, la separación se abre:
#
#     rotos:  F3-RESDAL-070 0,44 · F2-CSIS-113 0,44 · F3-SIPRI-007 0,42
#     sanos:  F1-RUTAN-003 0,33 · F2-ESA-032 0,16 · F2-SWF-035 0,03 ·
#             F1-CENIA-002 0,01 · F2-SWF-074 0,00 · F2-UNOOSA-014 0,00
#
# Lo correcto sería decidir página a página en vez de por documento: tanto
# `F2-CSIS-113` como `F3-SIPRI-007` tienen la portada destrozada y el cuerpo
# perfectamente legible, así que mandarlos enteros a OCR arregla una parte y
# degrada la otra —el OCR pierde acentos: "análisis" sale "nalisis"—. Eso exige
# que `_extraer_por_ocr` sepa mezclar texto nativo y OCR, que es un cambio de
# más calado y queda pendiente.
MAXIMA_PROPORCION_CID = 0.30
MAXIMA_PROPORCION_SUELTAS = 0.40

# Una letra latina suelta. Los caracteres CJK, árabes o cirílicos aislados no
# cuentan: son alfabetos que se escriben sin separar palabras con espacios, y
# tomarlos por texto destrozado manda a OCR documentos que se leen bien.
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

    La decisión de usar OCR es **por página**, no por documento: se conserva el
    texto nativo donde se puede leer y se reconoce solo lo que no. Decidir por
    documento arreglaba una parte y estropeaba otra —``F2-CSIS-113`` y
    ``F3-SIPRI-007`` tienen la portada destrozada y el cuerpo perfectamente
    legible, y el OCR pierde acentos: "análisis" sale "nalisis"—.
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

    **Las palabras de una página no sobreviven a su página.** Un informe de 250
    páginas son millones de diccionarios de palabra; materializarlos todos antes
    de procesar consume cientos de megabytes por documento y, con varios
    procesos en paralelo, mata el proceso con ``MemoryError``. Las líneas ocupan
    dos órdenes de magnitud menos y son lo único que necesitan las fases
    siguientes.

    ``page.close()`` no es decorativo: pdfplumber cachea los objetos de cada
    página y sin vaciarlo el ahorro anterior se pierde igual.

    Admite tanto un objeto de pdfplumber como una ruta, para poder probarlo sin
    montar el andamiaje del ``with``.
    """
    if isinstance(documento_pdf, (str, Path)):
        with pdfplumber.open(str(documento_pdf)) as abierto:
            return lineas_del_documento(abierto)

    paginas: list[list[Linea]] = []
    for pagina in documento_pdf.pages:
        palabras = _palabras_de_pagina(pagina)
        # El ancho es el de la página física y no el del texto que haya en ella:
        # el corredor se busca solo dentro de la zona ocupada, así que un margen
        # ancho nunca se confunde con una separación de columnas.
        paginas.append(ordenar_por_columnas(palabras, float(pagina.width or 0)))
        pagina.close()
    return paginas


def _palabras_de_pagina(pagina) -> list[dict]:
    """Palabras de una página con tamaño de fuente y coordenadas."""
    return pagina.extract_words(extra_attrs=["size", "fontname"])


def _texto_ilegible(lineas_por_pagina: list[list[Linea]]) -> bool:
    """``True`` si hay capa de texto pero no se puede leer.

    Dos formas de que un PDF tenga texto y aun así no sirva:

    1. **Fuente sin ``ToUnicode``**: pdfplumber no puede mapear los glifos a
       caracteres y devuelve ``(cid:NN)`` por cada uno. En el corpus real,
       ``F3-CEOBS-030`` —179 páginas— sale así entero.
    2. **Letras dibujadas sueltas**: el PDF coloca cada carácter como un objeto
       de texto independiente y la reconstrucción de líneas las separa con
       espacios: ``L i f e c y c l e  c o s t``.

    Ninguna la detecta :func:`_parece_escaneado`, que mira densidad de
    caracteres: ``(cid:47)`` son nueve caracteres por letra, así que la densidad
    de estos documentos es altísima. Y el OCR sí los recupera, porque el texto
    está dibujado en la página: sobre el documento de Sudán devuelve
    "Minamata Convention Initial Assessment in Sudan" con un 96 % de confianza.

    Los umbrales son deliberadamente altos. El texto nativo, aunque tenga
    defectos, es mejor que el OCR siempre que se pueda leer: un PDF con una
    fórmula en fuente rara, o una tabla con columnas de una letra, no se manda
    a OCR.
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
    """Densidad de caracteres por página por debajo del mínimo.

    Se mira la media y no el total: un informe escaneado con una portada
    vectorial tiene texto en la primera página y nada en las otras cien.
    """
    if not lineas_por_pagina:
        return True
    caracteres = sum(
        len(linea.texto) for lineas in lineas_por_pagina for linea in lineas
    )
    return caracteres / len(lineas_por_pagina) < MINIMO_CARACTERES_POR_PAGINA


# --- líneas, columnas y párrafos ------------------------------------------------


def agrupar_en_lineas(palabras: list[dict]) -> list[Linea]:
    """Agrupa palabras por altura y las ordena por posición horizontal.

    El orden de dibujo del PDF no es el de lectura tampoco dentro de una línea,
    así que se ordena explícitamente por ``x0``.
    """
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
        # El tamaño dominante y no el máximo: una versalita suelta no convierte
        # un párrafo en un título.
        tamano=tamanos.most_common(1)[0][0],
        top=min(w["top"] for w in palabras),
        bottom=max(w["bottom"] for w in palabras),
        x0=palabras[0]["x0"],
    )


def detectar_corte_de_columnas(palabras: list[dict], ancho: float) -> float | None:
    """Coordenada X del corredor vertical que separa dos columnas, o ``None``.

    Se proyectan todas las palabras sobre el eje horizontal y se busca la racha
    de bandas vacías más ancha dentro de la zona central. Que caiga en el centro
    es lo que distingue un corredor de un margen.
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
    """La racha de bandas libres más ancha que empieza y acaba dentro del texto.

    Se recorta a la zona ocupada para no confundir los márgenes izquierdo y
    derecho de la página con un corredor entre columnas.
    """
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
    """El tamaño de fuente más frecuente del documento.

    Se mide sobre el documento entero y no por página: una portada o una página
    de tablas tienen su propia moda y no representan al cuerpo.
    """
    if not tamanos:
        return 0.0
    return Counter(round(t, 1) for t in tamanos).most_common(1)[0][0]


def niveles_por_tamano(tamanos: list[float], cuerpo: float) -> dict[float, int]:
    """Asigna un nivel de título a cada tamaño mayor que el cuerpo.

    Los tamaños se ordenan de mayor a menor y cada escalón es un nivel. El nivel
    sale del orden y no del valor en puntos, porque 14 pt puede ser un título de
    primer nivel en un documento y de tercero en otro.
    """
    if cuerpo <= 0:
        return {}

    mayores = sorted(
        {round(t, 1) for t in tamanos if round(t, 1) > cuerpo * FACTOR_TITULO}, reverse=True
    )
    return {tamano: min(nivel, NIVEL_MAXIMO) for nivel, tamano in enumerate(mayores, start=1)}


def parece_titulo(texto: str) -> bool:
    """Un encabezado es corto.

    El tamaño de fuente solo no basta: en los informes del corpus, los pies de
    autores y las citas destacadas van en cuerpo mayor que el texto. Tomarlos
    por títulos mete un párrafo entero en el breadcrumb de todo lo que venga
    detrás.
    """
    return 0 < len(normalizar_texto(texto).split()) <= MAXIMO_PALABRAS_TITULO


def agrupar_en_parrafos(lineas: list[Linea]) -> list[Parrafo]:
    """Une líneas consecutivas en párrafos.

    Abre párrafo nuevo cuando el hueco vertical supera al interlineado
    dominante o cuando cambia el tamaño de fuente. Sin la segunda condición, un
    título pegado a su primer párrafo saldría como un único bloque y el
    documento perdería su jerarquía justo donde la tenía.
    """
    if not lineas:
        return []

    interlineado = _interlineado(lineas)
    parrafos: list[Parrafo] = []
    actual = [lineas[0]]

    for anterior, linea in zip(lineas, lineas[1:]):
        hueco = linea.top - anterior.top
        # El umbral se mide contra el mayor entre el interlineado dominante del
        # documento y el tamaño de la propia línea: un titular de 40 pt salta
        # 45 pt entre sus líneas y no por eso son párrafos distintos.
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

    Es la moda de los huecos redondeados a punto entero y, en caso de empate, el
    menor. La media no sirve: la arrastran los saltos entre párrafos, que son
    justo lo que se quiere detectar.
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

    Las páginas reconocidas se tratan aparte por dos razones. La primera es que
    sus líneas **no entran en el cálculo del tamaño del cuerpo ni de los
    niveles**: no tienen tamaño de fuente real, y colarlas ahí desplazaría la
    moda y cambiaría qué se considera título en el resto del documento. La
    segunda es que el contrato tiene un tipo propio para el texto reconocido, y
    usarlo conserva la trazabilidad de qué salió de dónde.
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
            # El OCR no da tamaños de fuente, así que no hay jerarquía que
            # deducir: cada línea reconocida es un bloque, como en la ruta de
            # los escaneados.
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

    Modifica ``lineas_por_pagina`` en el sitio y devuelve los números de página
    reconocidos (1-based). Sin Tesseract no reconoce nada, pero deja
    ``meta["requiere_ocr"]`` para que el reporte pueda listar el documento y una
    corrida posterior lo recupere sin reprocesar el corpus entero.
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

    Detectar que una página está rota no basta para reemplazarla. Hay páginas
    mixtas —la 64 de ``F1-AIINDEX-014`` es el caso— donde conviven texto chino
    perfectamente legible y las etiquetas rotadas de un gráfico, que salen letra
    a letra y disparan el criterio de ilegibilidad. El diagnóstico es correcto,
    pero el OCR de esa página solo devuelve los porcentajes del gráfico: 605
    caracteres de contenido se convertirían en 85 de ruido.

    La comparación es sobre **texto útil**, no sobre caracteres en bruto. Los
    marcadores CID abultan nueve caracteres por letra sin aportar nada, así que
    medir en bruto dejaría sin reconocer justo las páginas que más lo necesitan.
    """
    return len(_texto_util(reconocidas)) > len(_texto_util(nativas))


def _texto_util(lineas: list[Linea]) -> str:
    """El texto de unas líneas sin lo que no es contenido: marcadores CID y
    letras latinas sueltas."""
    sin_cid = _MARCA_CID.sub(" ", " ".join(linea.texto for linea in lineas))
    return " ".join(
        palabra for palabra in sin_cid.split() if not _LETRA_LATINA_SUELTA.match(palabra)
    )


def _errores_de(meta: dict[str, Any], bloques: list[Bloque | None]) -> list[str]:
    """Traduce el estado de la extracción a la lista de ``errores``.

    Que no se reconociera ninguna página tiene **dos causas muy distintas** y
    confundirlas hace daño: que falte Tesseract, que se arregla instalándolo, o
    que el OCR no mejorara lo que ya había, que no se arregla con nada y es el
    comportamiento correcto de :func:`_mejora`. Mezclarlas llenaba el reporte de
    corrida con decenas de documentos mandando a instalar algo ya instalado, y
    escondía cuáles eran los recuperables de verdad.
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

    Los dos criterios se aplican con granularidad distinta a propósito:

    - **Texto roto** (CID o letras sueltas), página a página. Un informe puede
      tener la portada destrozada y el cuerpo perfectamente legible.
    - **Falta de texto**, solo si lo es del documento entero. Cualquier informe
      tiene portadas, separadores de capítulo y páginas de figuras que salen
      casi vacías, y tratarlas como rotas mandaba a OCR 298 de los 759 PDFs del
      corpus —1 764 páginas, media hora de cómputo— para recuperar, en el mejor
      caso, el título de una portada. Cuando la falta de texto es de todo el
      documento sí significa lo que parece: un escaneado sin capa de texto.
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
    """Rasteriza y reconoce una página. Devuelve líneas sintéticas.

    El tamaño es :data:`TAMANO_SIN_FUENTE` y la geometría, un contador: el OCR
    no informa de cuerpos de letra, y estas líneas quedan fuera del cálculo de
    niveles en :func:`_bloques_de_lineas`, así que ninguno de los dos se usa.
    """
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
