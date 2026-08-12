"""Extractor de JSON: artículos scrapeados, catálogos y GeoJSON.

Se llama ``json_`` para no ensombrecer al ``json`` de la estándar dentro del
paquete. Son 954 documentos del índice, el 52 % del corpus, y casi todos son
artículos con un esquema estable (``title``, ``body_paragraphs``, ``sections``),
más 363 alertas tempranas con su ``alerta_meta``.

Hay tres caminos, por orden: GeoJSON —solo ``properties``, la geometría se
resume en un bbox—, esquema de artículo, y un recorrido genérico iterativo y con
tope de profundidad para lo demás.

Por qué no un recorrido genérico para todo, y en qué orden se emite:
``docs/decisiones/extraccion-por-formato.md`` §5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contrato import Bloque, Documento
from extractores.comun import (
    Jerarquia,
    construir_documento,
    documento_fallido,
    es_texto_natural,
    serializar_registro,
)
from limpieza import normalizar_texto

FORMATO = "json"

# Tope del recorrido genérico. Ningún documento del corpus se acerca.
PROFUNDIDAD_MAXIMA = 40

# Claves del esquema de artículo, por función.
_CLAVE_TITULO = "title"
_CLAVES_RESUMEN = ("abstract", "excerpt")
_CLAVE_CUERPO = "body_paragraphs"
_CLAVE_CUERPO_PLANO = "body_text"
_CLAVE_SECCIONES = "sections"
_CLAVE_LISTAS = "lists"
_CLAVE_ALERTA = "alerta_meta"

# Metadata descriptiva: se conserva, pero fuera del texto indexable.
_META_SIMPLE: dict[str, str] = {
    "url": "url",
    "date": "fecha",
    "doi": "doi",
    "issue": "numero",
    "pdf_url": "pdf_url",
}
_META_LISTAS: dict[str, str] = {
    "authors": "autores",
    "keywords": "etiquetas",
    "tags": "etiquetas",
    "topics": "etiquetas",
    "categories": "etiquetas",
}

# Colecciones de enlaces e imágenes: solo interesa cuántas hay.
_CLAVES_ENLACES = (
    "images",
    "links",
    "pdf_links",
    "doc_links",
    "science_links",
    "external_links",
    "internal_links",
)


def extraer(path: Path, fenomeno: int) -> Documento:
    """Extrae un ``Documento`` desde un archivo JSON. Nunca lanza."""
    path = Path(path)
    try:
        # utf-8-sig: con utf-8, un BOM se colaría como primer carácter del título.
        datos = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return documento_fallido(
            fuente=path.name, formato=FORMATO, fenomeno=fenomeno, motivo=f"JSON ilegible: {exc}"
        )
    except (OSError, UnicodeDecodeError) as exc:
        return documento_fallido(
            fuente=path.name,
            formato=FORMATO,
            fenomeno=fenomeno,
            motivo=f"no se pudo leer el archivo ({type(exc).__name__}): {exc}",
        )

    jerarquia = Jerarquia()
    meta: dict[str, Any] = {}

    if _es_geojson(datos):
        bloques = _bloques_de_geojson(datos, jerarquia, meta)
    elif isinstance(datos, dict) and _parece_articulo(datos):
        bloques = _bloques_de_articulo(datos, jerarquia, meta)
    elif isinstance(datos, list):
        bloques = _bloques_de_registros(datos, jerarquia)
    else:
        bloques = _bloques_genericos(datos, meta)

    errores = [] if any(bloques) else [_motivo_de_vacio(datos)]
    return construir_documento(
        fuente=path.name,
        formato=FORMATO,
        fenomeno=fenomeno,
        bloques=bloques,
        meta=meta,
        errores=errores,
    )


def _motivo_de_vacio(datos: Any) -> str:
    """Por qué no salió ni un bloque. Nunca se devuelve un vacío en silencio."""
    if isinstance(datos, list) and not datos:
        return "el JSON es una lista vacía: no hay nada que extraer"
    return (
        "el JSON no contiene lenguaje natural indexable "
        "(catálogo de enlaces, hashes o identificadores)"
    )


# --- artículo -----------------------------------------------------------------


def _parece_articulo(datos: dict) -> bool:
    """Hay título o hay cuerpo. Con eso ya se puede armar una jerarquía."""
    claves = (
        _CLAVE_TITULO,
        _CLAVE_CUERPO,
        _CLAVE_SECCIONES,
        _CLAVE_CUERPO_PLANO,
        *_CLAVES_RESUMEN,
    )
    return any(datos.get(clave) for clave in claves)


def _bloques_de_articulo(datos: dict, jerarquia: Jerarquia, meta: dict) -> list[Bloque | None]:
    _volcar_metadata(datos, meta)
    bloques: list[Bloque | None] = []

    titulo = datos.get(_CLAVE_TITULO)
    if isinstance(titulo, str) and titulo.strip():
        meta["titulo"] = normalizar_texto(titulo)
        bloques.append(jerarquia.titulo(titulo, 1))

    for clave in _CLAVES_RESUMEN:
        valor = datos.get(clave)
        if es_texto_natural(valor):
            bloques.append(jerarquia.parrafo(valor))

    bloques.extend(_bloques_de_alerta(datos.get(_CLAVE_ALERTA), jerarquia, meta))
    bloques.extend(_bloques_de_cuerpo(datos, jerarquia))

    for elemento in _lista_de(datos.get(_CLAVE_LISTAS)):
        if es_texto_natural(elemento):
            bloques.append(jerarquia.lista(elemento))

    bloques.extend(_bloques_de_secciones(datos.get(_CLAVE_SECCIONES), jerarquia))
    return bloques


def _bloques_de_cuerpo(datos: dict, jerarquia: Jerarquia) -> list[Bloque | None]:
    """``body_paragraphs`` si existe; ``body_text`` solo como respaldo.

    Son el mismo cuerpo: indexar los dos duplicaría el documento en el índice.
    """
    parrafos = [
        jerarquia.parrafo(parrafo)
        for parrafo in _lista_de(datos.get(_CLAVE_CUERPO))
        if es_texto_natural(parrafo)
    ]
    if parrafos:
        return parrafos

    plano = datos.get(_CLAVE_CUERPO_PLANO)
    return [jerarquia.parrafo(plano)] if es_texto_natural(plano) else []


def _bloques_de_secciones(secciones: Any, jerarquia: Jerarquia) -> list[Bloque | None]:
    bloques: list[Bloque | None] = []
    for seccion in _lista_de(secciones):
        if not isinstance(seccion, dict):
            continue
        encabezado = seccion.get("heading")
        if isinstance(encabezado, str) and encabezado.strip():
            bloques.append(jerarquia.titulo(encabezado, 2))
        for parrafo in _lista_de(seccion.get("paragraphs")):
            if es_texto_natural(parrafo):
                bloques.append(jerarquia.parrafo(parrafo))
    return bloques


def _bloques_de_alerta(alerta: Any, jerarquia: Jerarquia, meta: dict) -> list[Bloque | None]:
    """``alerta_meta`` de las 363 alertas tempranas de la Defensoría.

    Se reparte por lo que es cada campo y no por dónde está: ``tema_clave``
    describe el riesgo y va al índice, ``detail_id`` es interno y va a ``meta``.
    """
    if not isinstance(alerta, dict):
        return []

    meta["alerta_meta"] = {
        clave: valor
        for clave, valor in sorted(alerta.items())
        if isinstance(valor, (str, int, float, bool))
    }
    return [
        jerarquia.parrafo(valor) for _, valor in sorted(alerta.items()) if es_texto_natural(valor)
    ]


def _volcar_metadata(datos: dict, meta: dict) -> None:
    for clave, destino in _META_SIMPLE.items():
        valor = datos.get(clave)
        if isinstance(valor, str) and valor.strip():
            meta[destino] = valor.strip()

    for clave, destino in _META_LISTAS.items():
        valores = [
            v.strip() for v in _lista_de(datos.get(clave)) if isinstance(v, str) and v.strip()
        ]
        if valores:
            actuales = meta.setdefault(destino, [])
            actuales.extend(v for v in valores if v not in actuales)

    for clave in _CLAVES_ENLACES:
        valor = datos.get(clave)
        if isinstance(valor, list) and valor:
            meta[f"n_{clave}"] = len(valor)


# --- listas de registros -------------------------------------------------------


def _bloques_de_registros(datos: list, jerarquia: Jerarquia) -> list[Bloque | None]:
    """Cada elemento de la lista es un registro completo, como una fila de CSV."""
    bloques: list[Bloque | None] = []
    for elemento in datos:
        if isinstance(elemento, dict):
            bloques.append(
                jerarquia.fila(
                    serializar_registro(
                        (clave, valor)
                        for clave, valor in elemento.items()
                        if isinstance(valor, (str, int, float, bool))
                    )
                )
            )
        elif es_texto_natural(elemento):
            bloques.append(jerarquia.parrafo(elemento))
    return bloques


# --- GeoJSON -------------------------------------------------------------------


def _es_geojson(datos: Any) -> bool:
    return (
        isinstance(datos, dict)
        and datos.get("type") in {"FeatureCollection", "Feature"}
        and ("features" in datos or "geometry" in datos)
    )


def _bloques_de_geojson(datos: dict, jerarquia: Jerarquia, meta: dict) -> list[Bloque | None]:
    """Solo las ``properties``; la geometría se resume en un bounding box."""
    features = datos.get("features") if "features" in datos else [datos]
    bloques: list[Bloque | None] = []
    puntos: list[tuple[float, float]] = []

    for feature in _lista_de(features):
        if not isinstance(feature, dict):
            continue
        propiedades = feature.get("properties")
        if isinstance(propiedades, dict):
            bloques.append(
                jerarquia.fila(
                    serializar_registro(
                        (clave, valor)
                        for clave, valor in sorted(propiedades.items())
                        if isinstance(valor, (str, int, float, bool))
                    )
                )
            )
        puntos.extend(_coordenadas(feature.get("geometry")))

    if puntos:
        longitudes = [x for x, _ in puntos]
        latitudes = [y for _, y in puntos]
        meta["bbox"] = [min(longitudes), min(latitudes), max(longitudes), max(latitudes)]

    return bloques


def _coordenadas(geometria: Any) -> list[tuple[float, float]]:
    """Aplana ``coordinates`` sin importar cuántos niveles de lista tenga."""
    if not isinstance(geometria, dict):
        return []

    puntos: list[tuple[float, float]] = []
    pila: list[Any] = [geometria.get("coordinates")]
    while pila:
        nodo = pila.pop()
        if not isinstance(nodo, list):
            continue
        if len(nodo) >= 2 and all(isinstance(v, (int, float)) for v in nodo[:2]):
            puntos.append((float(nodo[0]), float(nodo[1])))
        else:
            pila.extend(nodo)
    return puntos


# --- recorrido genérico ---------------------------------------------------------


def _bloques_genericos(datos: Any, meta: dict) -> list[Bloque | None]:
    """Último recurso: recorre el árbol emitiendo las hojas que sean texto.

    El camino de claves se materializa como títulos, porque el contrato exige que
    la ``ruta`` esté respaldada por ellos. Se abren de forma perezosa, para que
    una rama entera de URL no deje detrás un vector que solo dice "enlaces".
    """
    jerarquia = Jerarquia()
    bloques: list[Bloque | None] = []
    abierto: list[str] = []

    for texto, camino in _hojas(datos, meta):
        comunes = _prefijo_comun(abierto, camino)
        for profundidad in range(comunes, len(camino)):
            bloques.append(jerarquia.titulo(camino[profundidad], profundidad + 1))
        abierto = list(camino)

        bloques.append(jerarquia.parrafo(texto))

    return bloques


def _hojas(datos: Any, meta: dict):
    """Genera ``(texto, camino_de_claves)`` en orden estable y sin recursión.

    Las claves se recorren ordenadas, y se apilan al revés porque la pila
    invierte. Iterativo y con tope de profundidad: la recursión sobre un JSON
    arbitrario es una forma cómoda de reventar la pila con un archivo de entrada.
    """
    pila: list[tuple[Any, list[str]]] = [(datos, [])]
    while pila:
        nodo, camino = pila.pop()

        if len(camino) > PROFUNDIDAD_MAXIMA:
            meta["profundidad_excedida"] = True
            continue

        if isinstance(nodo, dict):
            for clave in sorted(nodo, key=str, reverse=True):
                pila.append((nodo[clave], [*camino, str(clave)]))
        elif isinstance(nodo, list):
            for indice in range(len(nodo) - 1, -1, -1):
                pila.append((nodo[indice], [*camino, str(indice)]))
        elif es_texto_natural(nodo):
            yield normalizar_texto(nodo), camino


def _prefijo_comun(uno: list[str], otro: list[str]) -> int:
    """Cuántos niveles de título siguen abiertos entre un camino y el siguiente."""
    comunes = 0
    for izquierda, derecha in zip(uno, otro):
        if izquierda != derecha:
            break
        comunes += 1
    return comunes


def _lista_de(valor: Any) -> list:
    """Normaliza a lista lo que el scraper pudo dejar como escalar o como nulo."""
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]
