"""Contrato de datos de la capa de extracción: ``Bloque``, ``Documento`` y el
validador de sus invariantes.

Depende solo de :mod:`limpieza`. Las dos clases son ``frozen`` pero contienen
listas y dicts: trátalas como valores, constrúyelas de una vez y no las mutes.

Las reglas de identidad —de dónde sale cada ``doc_id``, por qué ``fuente`` es
inmutable, qué detiene la corrida— están en
``docs/decisiones/orquestacion-y-determinismo.md``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import MISSING, asdict, dataclass, field, fields
from typing import Any

from limpieza import normalizar_texto

TIPOS_BLOQUE: tuple[str, ...] = ("titulo", "parrafo", "lista", "fila", "ocr")
FORMATOS: tuple[str, ...] = ("pdf", "json", "csv", "xlsx", "imagen", "pbf", "texto")
IDIOMAS: tuple[str, ...] = ("es", "en", "pt")
FENOMENOS: tuple[int, ...] = (1, 2, 3)

NIVEL_MINIMO, NIVEL_MAXIMO = 1, 6

# 8 bytes de blake2b => 16 caracteres hexadecimales.
BYTES_DOC_ID = 8


@dataclass(frozen=True)
class Bloque:
    """Unidad mínima de texto extraída de un documento."""

    texto: str          # normalizado según limpieza.normalizar_texto
    tipo: str           # uno de TIPOS_BLOQUE
    nivel: int | None   # 1..6 si y solo si tipo == "titulo"
    ruta: list[str]     # breadcrumb de encabezados ancestros vigentes
    pagina: int | None  # 1-based si el formato tiene páginas
    atomico: bool       # unidad indivisible: no se parte ni se fusiona

    # Campos del registro que no entran al texto (identificadores tabulares).
    # Viajan hasta metadata.jsonl como campos extra, que §3.4 permite.
    datos: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Documento:
    """Un archivo del corpus, ya extraído."""

    doc_id: str            # ver _doc_id_es_admisible: tres formas, por preferencia
    fuente: str            # nombre EXACTO del archivo. Inmutable: empareja el jurado
    formato: str           # uno de FORMATOS
    fenomeno: int          # 1, 2 o 3
    idioma: str            # uno de IDIOMAS
    bloques: list[Bloque]  # en orden de lectura
    meta: dict[str, Any]   # url, fecha, autores, titulo, hoja...
    errores: list[str]     # vacía si la extracción fue limpia


def calcular_doc_id(fuente: str) -> str:
    """Identificador estable derivado de la fuente.

    blake2b y no ``hash()``: el de Python depende de ``PYTHONHASHSEED``.
    """
    digest = hashlib.blake2b(fuente.encode("utf-8"), digest_size=BYTES_DOC_ID)
    return digest.hexdigest()


def documento_a_dict(doc: Documento) -> dict[str, Any]:
    """Convierte el documento a estructuras primitivas listas para ``json.dumps``."""
    return asdict(doc)


def documento_desde_dict(datos: dict[str, Any]) -> Documento:
    """Inverso de :func:`documento_a_dict`. Lanza si faltan campos del contrato."""
    campos_documento = {campo.name for campo in fields(Documento)}
    faltan = sorted(campos_documento - datos.keys())
    if faltan:
        raise ValueError(f"al documento le faltan campos del contrato: {faltan}")

    return Documento(
        **{campo: datos[campo] for campo in campos_documento if campo != "bloques"},
        bloques=[_bloque_desde_dict(bloque) for bloque in datos["bloques"]],
    )


def _bloque_desde_dict(crudo: dict[str, Any]) -> Bloque:
    """Reconstruye un bloque. Los campos con default pueden faltar: un
    ``extraidos/`` anterior no trae los que se añadieron después."""
    campos = fields(Bloque)
    obligatorios = {
        campo.name
        for campo in campos
        if campo.default is MISSING and campo.default_factory is MISSING
    }
    faltan = sorted(obligatorios - crudo.keys())
    if faltan:
        raise ValueError(f"al bloque le faltan campos del contrato: {faltan}")
    return Bloque(**{campo.name: crudo[campo.name] for campo in campos if campo.name in crudo})


# DOC_ID del índice maestro de ADL: "F1-AIINDEX-001", "F3-MAPP2-118".
_DOC_ID_ADL = re.compile(r"^F[123]-[A-Z0-9]+-\d+$")


def _doc_id_es_admisible(doc: Documento) -> bool:
    """Un ``doc_id`` vale si es trazable a algo estable.

    Tres formas, por orden de preferencia: el ``DOC_ID`` de ADL, derivado de
    ``meta["ruta_relativa"]`` o derivado de ``fuente``.
    """
    if _DOC_ID_ADL.match(doc.doc_id):
        return True
    if doc.doc_id == calcular_doc_id(doc.fuente):
        return True
    ruta_relativa = doc.meta.get("ruta_relativa") if isinstance(doc.meta, dict) else None
    return isinstance(ruta_relativa, str) and doc.doc_id == calcular_doc_id(ruta_relativa)


def _violaciones_de_bloque(indice: int, bloque: Bloque) -> list[str]:
    """Comprueba los invariantes locales de un bloque."""
    violaciones: list[str] = []
    prefijo = f"bloques[{indice}]"

    if not isinstance(bloque.texto, str):
        violaciones.append(f"{prefijo}: texto no es str")
    else:
        normalizado = normalizar_texto(bloque.texto)
        if not normalizado:
            violaciones.append(f"{prefijo}: texto vacío o solo espacios")
        elif bloque.texto != normalizado:
            violaciones.append(
                f"{prefijo}: texto sin normalizar (esperado {normalizado!r})"
            )

    if bloque.tipo not in TIPOS_BLOQUE:
        violaciones.append(f"{prefijo}: tipo {bloque.tipo!r} no está en {TIPOS_BLOQUE}")

    if bloque.tipo == "titulo":
        if bloque.nivel is None:
            violaciones.append(f"{prefijo}: un titulo debe llevar nivel")
        elif not isinstance(bloque.nivel, int) or isinstance(bloque.nivel, bool):
            violaciones.append(f"{prefijo}: nivel no es int")
        elif not NIVEL_MINIMO <= bloque.nivel <= NIVEL_MAXIMO:
            violaciones.append(
                f"{prefijo}: nivel {bloque.nivel} fuera del rango "
                f"{NIVEL_MINIMO}..{NIVEL_MAXIMO}"
            )
    elif bloque.nivel is not None:
        violaciones.append(f"{prefijo}: nivel solo se admite en bloques de tipo titulo")

    if not isinstance(bloque.ruta, list) or not all(isinstance(t, str) for t in bloque.ruta):
        violaciones.append(f"{prefijo}: ruta debe ser una lista de str")

    if bloque.pagina is not None:
        if not isinstance(bloque.pagina, int) or isinstance(bloque.pagina, bool):
            violaciones.append(f"{prefijo}: pagina no es int")
        elif bloque.pagina < 1:
            violaciones.append(f"{prefijo}: pagina {bloque.pagina} debe ser >= 1")

    if not isinstance(bloque.atomico, bool):
        violaciones.append(f"{prefijo}: atomico no es bool")

    if not isinstance(bloque.datos, dict) or not all(
        isinstance(clave, str) and isinstance(valor, str)
        for clave, valor in bloque.datos.items()
    ):
        violaciones.append(f"{prefijo}: datos debe ser un dict de str a str")

    return violaciones


def _violaciones_de_jerarquia(bloques: list[Bloque]) -> list[str]:
    """Comprueba que cada ``ruta`` sean los ancestros vigentes, no el histórico.

    Un título de nivel N cierra todos los de nivel >= N que estuvieran abiertos.
    """
    violaciones: list[str] = []
    pila: list[tuple[int, str]] = []  # (nivel, texto)

    for indice, bloque in enumerate(bloques):
        es_titulo_valido = (
            bloque.tipo == "titulo"
            and isinstance(bloque.nivel, int)
            and not isinstance(bloque.nivel, bool)
            and NIVEL_MINIMO <= bloque.nivel <= NIVEL_MAXIMO
        )

        if bloque.tipo == "titulo" and not es_titulo_valido:
            # El nivel ya se reportó inválido; sin él no se puede situar el título.
            continue

        if es_titulo_valido:
            while pila and pila[-1][0] >= bloque.nivel:
                pila.pop()

        esperada = [texto for _, texto in pila]
        if list(bloque.ruta) != esperada:
            violaciones.append(
                f"bloques[{indice}]: ruta {list(bloque.ruta)!r} no coincide con "
                f"los ancestros vigentes {esperada!r}"
            )

        if es_titulo_valido:
            pila.append((bloque.nivel, bloque.texto))

    return violaciones


def validar_documento(doc: Documento) -> list[str]:
    """Devuelve la lista de invariantes violados por ``doc``; vacía si está bien.

    Para los tests y para depurar un extractor nuevo. El pipeline no la llama.
    """
    violaciones: list[str] = []

    if not isinstance(doc.fuente, str) or not doc.fuente.strip():
        violaciones.append("fuente vacía: es el campo de emparejamiento, no puede faltar")
    elif not isinstance(doc.doc_id, str) or not doc.doc_id.strip():
        violaciones.append("doc_id vacío")
    elif not _doc_id_es_admisible(doc):
        violaciones.append(
            f"doc_id {doc.doc_id!r} no es trazable: no es un DOC_ID de ADL, "
            f"ni deriva de fuente {doc.fuente!r} "
            f"(esperado {calcular_doc_id(doc.fuente)!r}), "
            f"ni de meta['ruta_relativa']"
        )

    if doc.formato not in FORMATOS:
        violaciones.append(f"formato {doc.formato!r} no está en {FORMATOS}")

    if doc.fenomeno not in FENOMENOS:
        violaciones.append(f"fenomeno {doc.fenomeno!r} no está en {FENOMENOS}")

    if doc.idioma not in IDIOMAS:
        violaciones.append(f"idioma {doc.idioma!r} no está en {IDIOMAS}")

    if not isinstance(doc.meta, dict):
        violaciones.append("meta debe ser un dict")

    if not isinstance(doc.errores, list) or not all(isinstance(e, str) for e in doc.errores):
        violaciones.append("errores debe ser una lista de str")

    if not isinstance(doc.bloques, list):
        violaciones.append("bloques debe ser una lista")
        return violaciones

    for indice, bloque in enumerate(doc.bloques):
        violaciones.extend(_violaciones_de_bloque(indice, bloque))

    violaciones.extend(_violaciones_de_jerarquia(doc.bloques))

    return violaciones
