"""Contrato de datos de la capa de extracción.

Define las dos únicas estructuras que cruzan la frontera entre extractores y el
resto del pipeline, y la función que verifica sus invariantes.

Regla de dependencias: este módulo depende de :mod:`limpieza` (para saber qué
es un texto normalizado) y de nada más. Los extractores dependen de ambos.

Nota sobre inmutabilidad: ambas clases son ``frozen``, pero contienen listas y
diccionarios. "Frozen" impide reasignar campos, no mutar su contenido, y hace
que las instancias no sean hashables. Trátalas como valores: constrúyelas de
una vez y no las modifiques.
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

# 8 bytes de blake2b => 16 caracteres hexadecimales. Suficiente para decenas de
# miles de documentos y corto para usarse como nombre de archivo.
BYTES_DOC_ID = 8


@dataclass(frozen=True)
class Bloque:
    """Unidad mínima de texto extraída de un documento."""

    texto: str
    """Texto limpio, sin marcado, normalizado según :func:`limpieza.normalizar_texto`."""

    tipo: str
    """Uno de :data:`TIPOS_BLOQUE`."""

    nivel: int | None
    """1..6 si y solo si ``tipo == "titulo"``; ``None`` en el resto."""

    ruta: list[str]
    """Breadcrumb de encabezados ancestros vigentes, del más externo al más interno."""

    pagina: int | None
    """Página de origen (1-based) si el formato la tiene; ``None`` si no."""

    atomico: bool
    """``True`` si es una unidad indivisible que no debe fusionarse con vecinas."""

    datos: dict[str, str] = field(default_factory=dict)
    """Campos del bloque que **no** entran en :attr:`texto`.

    Existe para los identificadores de los registros tabulares —PMID, DOI,
    PMCID— que se recuperan mal por semejanza pero se citan y se verifican.
    Sacarlos del texto mejora el vector; borrarlos perdería datos del corpus.
    Viajan hasta ``metadata.jsonl`` como campos extra, que §3.4 permite.

    Va con default porque los demás extractores no tienen nada que poner aquí:
    en un PDF, todo lo que hay es texto."""


@dataclass(frozen=True)
class Documento:
    """Un archivo del corpus, ya extraído."""

    doc_id: str
    """Identificador interno, estable entre corridas. Sale del ``DOC_ID`` del
    índice de ADL cuando lo hay; si no, se deriva de ``meta["ruta_relativa"]``
    o de :attr:`fuente`. Ver :func:`calcular_doc_id` y
    :func:`_doc_id_es_admisible` para las tres formas admitidas y el orden de
    preferencia entre ellas."""

    fuente: str
    """Nombre o URL EXACTA del archivo original. Campo inmutable de emparejamiento."""

    formato: str
    """Uno de :data:`FORMATOS`."""

    fenomeno: int
    """1, 2 o 3."""

    idioma: str
    """ISO 639-1 predominante: uno de :data:`IDIOMAS`."""

    bloques: list[Bloque]
    """En orden de lectura del documento original."""

    meta: dict[str, Any]
    """Campos descriptivos: url, fecha, autores, titulo, hoja..."""

    errores: list[str]
    """Vacía si la extracción fue limpia."""


def calcular_doc_id(fuente: str) -> str:
    """Identificador estable derivado de la fuente.

    Se usa blake2b y no ``hash()`` porque ``hash()`` de una cadena depende de
    ``PYTHONHASHSEED`` y cambiaría entre corridas. El resultado solo depende de
    los bytes UTF-8 de ``fuente``, así que dos corridas sobre el mismo corpus
    producen los mismos nombres de archivo.
    """
    digest = hashlib.blake2b(fuente.encode("utf-8"), digest_size=BYTES_DOC_ID)
    return digest.hexdigest()


def documento_a_dict(doc: Documento) -> dict[str, Any]:
    """Convierte el documento a estructuras primitivas listas para ``json.dumps``."""
    return asdict(doc)


def documento_desde_dict(datos: dict[str, Any]) -> Documento:
    """Inverso de :func:`documento_a_dict`.

    Existe porque la capa de fragmentación no vuelve a extraer nada: lee los
    ``extraidos/{doc_id}.json`` que dejó el orquestador. Sin esto, cada
    consumidor reconstruiría los ``Bloque`` a mano y el contrato dejaría de ser
    un único punto de verdad.

    Lanza ``ValueError`` si faltan campos: un JSON incompleto significa que el
    archivo no lo escribió este pipeline, y seguir adelante con valores
    inventados enmascararía el problema hasta el índice.
    """
    campos_documento = {campo.name for campo in fields(Documento)}
    faltan = sorted(campos_documento - datos.keys())
    if faltan:
        raise ValueError(f"al documento le faltan campos del contrato: {faltan}")

    return Documento(
        **{campo: datos[campo] for campo in campos_documento if campo != "bloques"},
        bloques=[_bloque_desde_dict(bloque) for bloque in datos["bloques"]],
    )


def _bloque_desde_dict(crudo: dict[str, Any]) -> Bloque:
    """Reconstruye un bloque. Los campos con default pueden faltar.

    Que puedan faltar no es laxitud: un ``extraidos/`` de una corrida anterior
    no tiene por qué traer los campos que se añadieron después, y hacerlo
    fallar obligaría a reextraer el corpus entero para leer un solo documento.
    """
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
    """Un ``doc_id`` vale si es trazable a algo estable, no si es cualquier cosa.

    Son tres formas, por orden de preferencia del pipeline:

    1. El ``DOC_ID`` que entrega ADL en su índice maestro. Es la identidad
       oficial del documento y la que el jurado puede rastrear.
    2. Derivado de ``meta["ruta_relativa"]``. Necesario porque 59 nombres de
       archivo se repiten en el corpus (186 archivos): derivarlo de ``fuente``
       le daría el mismo ``doc_id`` a documentos distintos y uno sobrescribiría
       al otro.
    3. Derivado de ``fuente``. El caso simple, sin índice y sin colisiones.
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

    # nivel es no nulo si y solo si el bloque es un título.
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

    Reconstruye la pila de encabezados recorriendo los bloques en orden y la
    compara con la ruta declarada. Un título de nivel N cierra todos los títulos
    de nivel >= N que estuvieran abiertos.
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
            # El nivel ya se reportó como inválido; sin él no se puede situar
            # el título en la jerarquía, así que no se arrastra el error.
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

    Pensada para los tests y para depurar un extractor nuevo. El pipeline en
    producción no la llama: un documento inválido debe ser imposible de
    construir, no algo que se detecte al final.
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
