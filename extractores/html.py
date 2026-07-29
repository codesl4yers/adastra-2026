"""Extractor de HTML. Implementación de referencia del resto de extractores.

Estrategia
----------
1. Leer bytes y decidir la codificación de forma explícita (no se delega en la
   autodetección de la librería, que puede variar entre versiones).
2. Podar el marcado que nunca es contenido: ``script``, ``style``, ``nav``,
   ``footer``, ``head`` y los elementos ocultos.
3. Recorrer ``h1..h6``, ``p`` y ``li`` en orden de documento manteniendo una
   pila de encabezados que produce el breadcrumb ``ruta``.
4. Descartar el boilerplate que sobrevive a la poda: textos cortos que se
   repiten entre secciones (menús, "volver al inicio") y numeraciones.

La trampa principal
-------------------
El texto anidado se duplica con facilidad. ``<p>`` dentro de ``<li>``, o
``<li>`` dentro de ``<li>``, aparecen dos veces si se usa ``get_text()`` sobre
cada elemento de interés: una en el hijo y otra en el ancestro. Por eso aquí se
usa :func:`_texto_propio`, que toma solo el texto que no pertenece a otro
elemento de interés descendiente.
"""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from contrato import Bloque, Documento, calcular_doc_id
from limpieza import (
    detectar_idioma,
    es_ruido_estructural,
    lineas_repetidas,
    normalizar_texto,
)

FORMATO = "html"

# Elementos cuyo texto nunca es contenido del documento.
ETIQUETAS_PODADAS = (
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "object",
    "embed",
    "svg",
    "nav",
    "footer",
    "head",
)

# Elementos que sí aportan texto, en el orden en que aparecen en el documento.
ETIQUETAS_TITULO = ("h1", "h2", "h3", "h4", "h5", "h6")
ETIQUETAS_INTERES = ETIQUETAS_TITULO + ("p", "li")

# Un texto se considera boilerplate si se repite entre secciones y además es
# corto: un párrafo largo repetido casi siempre es contenido real.
MAXIMO_CARACTERES_BOILERPLATE = 80

# Por debajo de esta longitud no se juzga el idioma de un bloque: "Abstract" no
# es evidencia de que la sección esté en inglés.
MINIMO_CARACTERES_IDIOMA_BLOQUE = 40

_CHARSET_DECLARADO = re.compile(rb"""charset\s*=\s*["']?\s*([\w\-]+)""", re.IGNORECASE)
_REGLA_CSS = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SELECTOR_SIMPLE = re.compile(r"^[.#][\w\-]+$")
_OCULTO_EN_CSS = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


class ExtraccionFallida(Exception):
    """El archivo no es HTML utilizable. Se registra en ``errores``."""


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo HTML.

    Nunca lanza: cualquier fallo se refleja en ``errores`` con ``bloques=[]``.
    """
    fuente = path.name
    errores: list[str] = []
    meta: dict = {}
    bloques: list[Bloque] = []

    try:
        crudo = _leer_bytes(path)
        texto, codificacion = _decodificar(crudo)
        sopa = BeautifulSoup(texto, _PARSER)

        meta = _extraer_meta(sopa)
        meta["codificacion"] = codificacion

        _podar(sopa)
        cuerpo = sopa.body or sopa
        bloques = _extraer_bloques(cuerpo)
    except ExtraccionFallida as exc:
        errores.append(str(exc))
    except Exception as exc:  # noqa: BLE001 - el pipeline no puede caerse
        errores.append(f"error inesperado ({type(exc).__name__}): {exc}")

    idioma = _idioma_del_documento(bloques)
    meta["bloques_en_otro_idioma"] = _bloques_discrepantes(bloques, idioma)

    return Documento(
        doc_id=calcular_doc_id(fuente),
        fuente=fuente,
        formato=FORMATO,
        fenomeno=fenomeno,
        idioma=idioma,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


# --- lectura y decodificación ------------------------------------------------


def _elegir_parser() -> str:
    """Fija el parser una sola vez: cambiar de parser cambia la salida."""
    try:
        import lxml  # noqa: F401
    except ImportError:  # pragma: no cover - entorno sin lxml
        return "html.parser"
    return "lxml"


_PARSER = _elegir_parser()


def _leer_bytes(path: Path) -> bytes:
    """Lee el archivo comprobando que sea un archivo de texto plausible."""
    if not path.exists():
        raise ExtraccionFallida(f"el archivo no existe: {path.name}")
    if not path.is_file():
        raise ExtraccionFallida(f"la ruta no es un archivo: {path.name}")

    crudo = path.read_bytes()
    if not crudo.strip():
        raise ExtraccionFallida("el archivo está vacío")
    if b"\x00" in crudo:
        # Un byte NUL en un archivo de texto significa corrupción o truncamiento:
        # el marcado que sobreviva no es fiable, así que se descarta entero.
        raise ExtraccionFallida("archivo corrupto: contiene bytes nulos, no es HTML de texto")
    return crudo


def _decodificar(crudo: bytes) -> tuple[str, str]:
    """Decodifica a str devolviendo también la codificación usada.

    Se prueba en orden fijo para que el resultado no dependa de heurísticas
    externas: BOM, charset declarado, UTF-8 y, como último recurso, cp1252 con
    reemplazo (que nunca falla).
    """
    if crudo.startswith(codecs.BOM_UTF8):
        return crudo.decode("utf-8-sig"), "utf-8-sig"

    candidatas = []
    declarada = _CHARSET_DECLARADO.search(crudo[:2048])
    if declarada:
        candidatas.append(declarada.group(1).decode("ascii", errors="ignore").lower())
    candidatas.append("utf-8")

    for codificacion in candidatas:
        if not codificacion:
            continue
        try:
            return crudo.decode(codificacion), codificacion
        except (UnicodeDecodeError, LookupError):
            continue

    return crudo.decode("cp1252", errors="replace"), "cp1252"


# --- poda del marcado no visible ---------------------------------------------


def _podar(sopa: BeautifulSoup) -> None:
    """Elimina del árbol todo lo que no es contenido visible.

    Debe llamarse *después* de :func:`_extraer_meta`, porque destruye el
    ``<head>``.
    """
    ocultas = _clases_ocultas_por_css(sopa)

    for etiqueta in sopa.find_all(ETIQUETAS_PODADAS):
        etiqueta.decompose()

    for etiqueta in sopa.find_all(True):
        if _esta_oculto(etiqueta, ocultas):
            etiqueta.decompose()


def _clases_ocultas_por_css(sopa: BeautifulSoup) -> frozenset[str]:
    """Selectores simples declarados como ocultos en un ``<style>`` embebido.

    Solo se interpretan selectores de una clase (``.oculto``) o un id
    (``#banner``). Un motor CSS completo está fuera del alcance: lo que no se
    reconoce, no se oculta.
    """
    ocultas: set[str] = set()
    for estilo in sopa.find_all("style"):
        for selectores, declaraciones in _REGLA_CSS.findall(estilo.get_text()):
            if not _OCULTO_EN_CSS.search(declaraciones):
                continue
            for selector in selectores.split(","):
                selector = selector.strip()
                if _SELECTOR_SIMPLE.match(selector):
                    ocultas.add(selector)
    return frozenset(ocultas)


def _esta_oculto(etiqueta: Tag, ocultas: frozenset[str]) -> bool:
    """``True`` si el elemento está marcado como no visible."""
    if etiqueta.has_attr("hidden"):
        return True
    if etiqueta.get("aria-hidden") == "true":
        return True
    if _OCULTO_EN_CSS.search(etiqueta.get("style", "")):
        return True

    if f"#{etiqueta.get('id', '')}" in ocultas and etiqueta.get("id"):
        return True
    # sorted() para no depender del orden de iteración de un conjunto.
    return any(f".{clase}" in ocultas for clase in sorted(etiqueta.get("class", [])))


# --- metadatos ---------------------------------------------------------------


def _extraer_meta(sopa: BeautifulSoup) -> dict:
    """Vuelca ``<title>``, meta-descripción y URL canónica.

    Estos valores describen el documento; van a ``meta`` y nunca al cuerpo, para
    que no compitan con el contenido durante la recuperación.
    """
    meta: dict = {"titulo": None, "descripcion": None, "url": None, "idioma_declarado": None}

    if sopa.title and sopa.title.string:
        meta["titulo"] = normalizar_texto(sopa.title.string) or None

    descripcion = sopa.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if descripcion and descripcion.get("content"):
        meta["descripcion"] = normalizar_texto(descripcion["content"]) or None

    canonica = sopa.find("link", attrs={"rel": re.compile(r"^canonical$", re.I)})
    if canonica and canonica.get("href"):
        meta["url"] = normalizar_texto(canonica["href"]) or None

    etiqueta_html = sopa.find("html")
    if etiqueta_html and etiqueta_html.get("lang"):
        meta["idioma_declarado"] = normalizar_texto(etiqueta_html["lang"]) or None

    return meta


# --- extracción de bloques ---------------------------------------------------


def _texto_propio(elemento: Tag) -> str:
    """Texto del elemento excluyendo el de descendientes de interés.

    Evita la duplicación de ``<p>`` dentro de ``<li>`` o de listas anidadas:
    cada fragmento de texto pertenece al elemento de interés más cercano que lo
    contiene.
    """
    fragmentos = []
    for nodo in elemento.descendants:
        if not isinstance(nodo, NavigableString):
            continue
        padre = nodo.parent
        pertenece_a_otro = False
        while padre is not None and padre is not elemento:
            if padre.name in ETIQUETAS_INTERES:
                pertenece_a_otro = True
                break
            padre = padre.parent
        if not pertenece_a_otro:
            fragmentos.append(str(nodo))
    return " ".join(fragmentos)


def _nivel_de_titulo(nombre: str) -> int | None:
    """Devuelve 1..6 para ``h1``..``h6``; ``None`` si no es un encabezado."""
    if nombre in ETIQUETAS_TITULO:
        return int(nombre[1])
    return None


def _extraer_bloques(cuerpo: Tag) -> list[Bloque]:
    """Recorre el cuerpo en orden de lectura construyendo los bloques.

    ``find_all`` con una lista de nombres devuelve los elementos en orden de
    documento, que es justo el orden de lectura que exige el contrato.
    """
    bloques: list[Bloque] = []
    pila: list[tuple[int, str]] = []  # (nivel, texto) de los títulos vigentes

    for elemento in cuerpo.find_all(ETIQUETAS_INTERES):
        texto = normalizar_texto(_texto_propio(elemento))
        if not texto or es_ruido_estructural(texto):
            continue

        nivel = _nivel_de_titulo(elemento.name)
        if nivel is not None:
            # Un título de nivel N cierra los títulos de nivel >= N.
            while pila and pila[-1][0] >= nivel:
                pila.pop()
            bloques.append(
                Bloque(
                    texto=texto,
                    tipo="titulo",
                    nivel=nivel,
                    ruta=[titulo for _, titulo in pila],
                    pagina=None,
                    atomico=False,
                )
            )
            pila.append((nivel, texto))
            continue

        bloques.append(
            Bloque(
                texto=texto,
                tipo="lista" if elemento.name == "li" else "parrafo",
                nivel=None,
                # Lista nueva en cada bloque: compartir la misma lista haría que
                # todos acabaran viendo el último breadcrumb.
                ruta=[titulo for _, titulo in pila],
                pagina=None,
                atomico=False,
            )
        )

    return _descartar_boilerplate(bloques)


def _descartar_boilerplate(bloques: list[Bloque]) -> list[Bloque]:
    """Elimina textos cortos que se repiten entre secciones del documento.

    La unidad de repetición en un HTML es la sección delimitada por encabezados:
    lo que reaparece en casi todas ellas ("volver al inicio", un aviso de
    cookies replicado) es navegación, no contenido. Los títulos nunca se
    descartan, así que el breadcrumb de los bloques restantes sigue siendo
    válido.
    """
    secciones = _partir_en_secciones(bloques)
    repetidos = set(lineas_repetidas(secciones))
    if not repetidos:
        return bloques

    return [
        bloque
        for bloque in bloques
        if bloque.tipo == "titulo"
        or bloque.texto not in repetidos
        or len(bloque.texto) > MAXIMO_CARACTERES_BOILERPLATE
    ]


def _partir_en_secciones(bloques: list[Bloque]) -> list[list[str]]:
    """Agrupa los textos no-título por la sección de encabezado que los contiene."""
    secciones: list[list[str]] = [[]]
    for bloque in bloques:
        if bloque.tipo == "titulo":
            secciones.append([])
        else:
            secciones[-1].append(bloque.texto)
    return [seccion for seccion in secciones if seccion]


# --- idioma ------------------------------------------------------------------


def _idioma_del_documento(bloques: list[Bloque]) -> str:
    """Idioma predominante, calculado sobre todo el texto del documento."""
    if not bloques:
        return "es"
    return detectar_idioma(" ".join(bloque.texto for bloque in bloques))


def _bloques_discrepantes(bloques: list[Bloque], idioma_documento: str) -> list[dict]:
    """Índices de bloques cuyo idioma difiere claramente del documento.

    Solo se juzgan bloques con texto suficiente: un título de dos palabras no es
    evidencia de cambio de idioma. La lista sale ordenada por índice.
    """
    discrepantes = []
    for indice, bloque in enumerate(bloques):
        if len(bloque.texto) < MINIMO_CARACTERES_IDIOMA_BLOQUE:
            continue
        idioma = detectar_idioma(bloque.texto, por_defecto=idioma_documento)
        if idioma != idioma_documento:
            discrepantes.append({"indice": indice, "idioma": idioma})
    return discrepantes
