"""Recorre un directorio de entrada, extrae cada documento y lo persiste.

Es el único módulo que escribe a disco; los extractores son funciones puras.
Produce un ``{doc_id}.json`` por archivo y un ``manifiesto.jsonl`` ordenado por
``(fuente, ruta_relativa)``, que es la herramienta de regresión: si el diff entre
dos corridas sale vacío, no cambió nada.

Identidad, precedencia del fenómeno, paralelismo y determinismo:
``docs/decisiones/orquestacion-y-determinismo.md``.

Uso::

    python orquestador.py --entrada fixtures --salida extraidos
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import ModuleType

import psutil

# El pipeline entregable vive en la raíz del repo y esto en auxiliar/: sin la
# raíz en el path, ejecutar este archivo directamente no encuentra `contrato`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contrato import Documento, calcular_doc_id, documento_a_dict  # noqa: E402
from extractores import imagen, json_, pbf, pdf, tabular, texto  # noqa: E402
from indice import EntradaIndice, cargar_indice  # noqa: E402

NOMBRE_MANIFIESTO = "manifiesto.jsonl"

# Documentos por worker antes de reciclar el pool entero. pdfminer no devuelve
# toda la memoria que reserva por PDF y la única forma de recuperarla es cerrar
# el proceso. Se recicla el pool completo y no el worker porque
# max_tasks_per_child tiene un deadlock de CPython sin corregir hasta 3.14.
DOCUMENTOS_POR_RECICLAJE = 25

# MB reservados por proceso al calcular el valor por defecto de --procesos.
RAM_MB_POR_PROCESO = 600

# Extensión -> (módulo extractor, formato del contrato). Una extensión que no
# esté aquí se ignora, pero no en silencio: sale en el reporte de cobertura.
EXTRACTORES: dict[str, tuple[ModuleType, str]] = {
    ".pdf": (pdf, "pdf"),
    ".json": (json_, "json"),
    ".geojson": (json_, "json"),
    ".csv": (tabular, "csv"),
    ".xlsx": (tabular, "xlsx"),
    ".png": (imagen, "imagen"),
    ".jpg": (imagen, "imagen"),
    ".jpeg": (imagen, "imagen"),
    ".tif": (imagen, "imagen"),
    ".tiff": (imagen, "imagen"),
    ".bmp": (imagen, "imagen"),
    ".webp": (imagen, "imagen"),
    ".avif": (imagen, "imagen"),
    ".pbf": (pbf, "pbf"),
    ".mvt": (pbf, "pbf"),
    ".txt": (texto, "texto"),
    ".md": (texto, "texto"),
}

# Carpetas raíz del corpus de ADL: "F1_IA_y_Capacidades_Estrategicas",
# "F2_Seguridad_Entorno_Espacial", "F3_Dinamicas_Territoriales".
_CARPETA_FENOMENO_ADL = re.compile(r"^F([123])[_\s\-]", re.IGNORECASE)

# Convención anterior: "fenomeno_2", "fenomeno-3", "Fenomeno 1". Se mantiene por
# si ADL reorganiza el corpus con la nomenclatura que esperaba el orquestador.
_CARPETA_FENOMENO_LEGADO = re.compile(r"^fen[oó]meno[\s_\-]?([123])$", re.IGNORECASE)

_PATRONES_FENOMENO = (_CARPETA_FENOMENO_ADL, _CARPETA_FENOMENO_LEGADO)


@dataclass(frozen=True)
class _Identidad:
    """Quién es un archivo, resuelto antes de extraerlo.

    Se calcula para todos de golpe: la ambigüedad de un nombre solo se ve mirando
    el conjunto, y el choque de ``doc_id`` hay que detectarlo antes de escribir.
    """

    ruta: Path
    ruta_relativa: str
    doc_id: str
    fenomeno: int
    origen_doc_id: str
    origen_fenomeno: str
    observatorio: str | None
    codigo_observatorio: str | None
    fuente_ambigua: bool


@dataclass(frozen=True)
class ReporteCobertura:
    """Qué quedó fuera y por qué. Existe porque la versión anterior filtraba en
    silencio y nadie se enteraba hasta el cierre."""

    sin_extractor: list[str]        # en disco, con una extensión que nadie extrae
    huerfanos_del_indice: list[str]  # el índice los lista y no están en disco
    fuera_del_indice: list[str]      # están en disco y el índice no los lista
    por_origen_doc_id: dict[str, int]      # "indice" / "derivado"
    por_origen_fenomeno: dict[str, int]    # "indice" / "carpeta" / "defecto"
    nombres_ambiguos: int   # nombres que aparecen en más de una ruta
    archivos_ambiguos: int  # archivos afectados por esos nombres


def procesar_corpus(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
    procesos: int = 1,
    reciclar_cada: int = DOCUMENTOS_POR_RECICLAJE,
    solo_doc_ids: set[str] | None = None,
) -> tuple[list[Documento], ReporteCobertura]:
    """Como :func:`procesar_directorio`, pero devolviendo también la cobertura.

    Cuando hay índice, el índice manda: lo que no lista se reporta y no se
    procesa.

    ``solo_doc_ids`` restringe la extracción a esos ``doc_id`` y deja el resto del
    corpus intacto —JSON y líneas del manifiesto—, para no repetir una corrida
    entera por unos pocos fallos ya corregidos. En ese modo devuelve solo los
    documentos reprocesados, exige un manifiesto previo y no admite ``limpiar``.
    """
    if solo_doc_ids is not None and limpiar:
        raise ValueError(
            "solo_doc_ids y limpiar son incompatibles: limpiar borraría los "
            "documentos que solo_doc_ids necesita conservar"
        )

    entrada = Path(entrada)
    salida = Path(salida)

    archivos = _listar_archivos(entrada)
    con_extractor = [ruta for ruta in archivos if _tiene_extractor(ruta)]
    sin_extractor = [
        _ruta_relativa(ruta, entrada) for ruta in archivos if not _tiene_extractor(ruta)
    ]

    rutas, fuera_del_indice, huerfanos = _cruzar_con_indice(
        con_extractor, archivos, entrada, indice
    )

    colisiones = _agrupar_colisiones(rutas, entrada)
    ambiguos = set(colisiones)
    _avisar_colisiones(colisiones)

    identidades = [
        _identidad_de(ruta, entrada, indice, ambiguos, fenomeno_por_defecto)
        for ruta in rutas
    ]
    _verificar_doc_ids(identidades)

    # Antes de extraer: cada documento se escribe en cuanto termina.
    salida.mkdir(parents=True, exist_ok=True)
    if limpiar:
        _limpiar_salida(salida)

    reporte = ReporteCobertura(
        sin_extractor=sin_extractor,
        huerfanos_del_indice=huerfanos,
        fuera_del_indice=fuera_del_indice,
        por_origen_doc_id=_contar(i.origen_doc_id for i in identidades),
        por_origen_fenomeno=_contar(i.origen_fenomeno for i in identidades),
        nombres_ambiguos=len(colisiones),
        archivos_ambiguos=sum(len(rs) for rs in colisiones.values()),
    )

    if solo_doc_ids is None:
        documentos = _extraer_todos(identidades, procesos, salida, reciclar_cada)
        documentos.sort(key=lambda doc: (doc.fuente, doc.meta["ruta_relativa"]))
        _escribir_manifiesto(
            [_entrada_de_manifiesto(d) for d in documentos], salida / NOMBRE_MANIFIESTO
        )
        return documentos, reporte

    ruta_manifiesto = salida / NOMBRE_MANIFIESTO
    if not ruta_manifiesto.exists():
        raise ValueError(
            f"no hay manifiesto previo en {salida}: solo_doc_ids necesita una "
            "corrida completa antes para saber qué conservar"
        )

    objetivo = [i for i in identidades if i.doc_id in solo_doc_ids]
    faltantes = solo_doc_ids - {i.doc_id for i in objetivo}
    if faltantes:
        print(
            f"[aviso] {len(faltantes)} doc_id pedidos ya no están en el corpus "
            f"o el índice actual, se omiten: {sorted(faltantes)[:5]}"
            + ("..." if len(faltantes) > 5 else ""),
            file=sys.stderr,
        )

    documentos = _extraer_todos(objetivo, procesos, salida, reciclar_cada)

    manifiesto_previo = cargar_manifiesto(ruta_manifiesto)
    ruta_por_doc_id = {i.doc_id: i.ruta_relativa for i in identidades}
    mantenidas = [e for e in manifiesto_previo if e["doc_id"] not in solo_doc_ids]
    entradas = mantenidas + [_entrada_de_manifiesto(d) for d in documentos]
    entradas.sort(key=lambda e: (e["fuente"], ruta_por_doc_id.get(e["doc_id"], "")))
    _escribir_manifiesto(entradas, ruta_manifiesto)

    return documentos, reporte


def procesar_directorio(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
    procesos: int = 1,
    reciclar_cada: int = DOCUMENTOS_POR_RECICLAJE,
    solo_doc_ids: set[str] | None = None,
) -> list[Documento]:
    """Extrae todos los documentos de ``entrada`` y los escribe en ``salida``.

    Devuelve los documentos ordenados por ``(fuente, ruta_relativa)``, el mismo
    orden del manifiesto. Lanza ``ValueError`` solo si dos archivos acaban con el
    mismo ``doc_id``; un nombre repetido se marca en ``meta["fuente_ambigua"]``.
    """
    documentos, _ = procesar_corpus(
        entrada,
        salida,
        fenomeno_por_defecto=fenomeno_por_defecto,
        limpiar=limpiar,
        indice=indice,
        procesos=procesos,
        reciclar_cada=reciclar_cada,
        solo_doc_ids=solo_doc_ids,
    )
    return documentos


def _cruzar_con_indice(
    con_extractor: list[Path],
    todos: list[Path],
    entrada: Path,
    indice: dict[str, EntradaIndice] | None,
) -> tuple[list[Path], list[str], list[str]]:
    """Reparte los archivos entre los que el índice lista y los que no.

    Se compara con ``is None``: un índice vacío es un índice real y debe filtrar
    a "nada". Los huérfanos se calculan contra **todos** los archivos del disco y
    no solo los extraíbles, o uno sin extractor se reportaría como inexistente.
    """
    if indice is None:
        return con_extractor, [], []

    listadas, sueltas = [], []
    for ruta in con_extractor:
        relativa = _ruta_relativa(ruta, entrada)
        if relativa in indice:
            listadas.append(ruta)
        else:
            sueltas.append(relativa)

    existentes = {_ruta_relativa(ruta, entrada) for ruta in todos}
    # Se recorre el índice y no el set, para que el orden sea el del archivo.
    huerfanos = [relativa for relativa in indice if relativa not in existentes]
    return listadas, sueltas, huerfanos


def _contar(valores) -> dict[str, int]:
    """Cuenta ocurrencias con las claves ordenadas, para que el reporte sea estable."""
    conteo: dict[str, int] = {}
    for valor in valores:
        conteo[valor] = conteo.get(valor, 0) + 1
    return dict(sorted(conteo.items()))


def _avisar_colisiones(colisiones: dict[str, list[str]]) -> None:
    """Resumen a stderr. No detiene nada: es información, no un fallo."""
    if not colisiones:
        return
    n_nombres = len(colisiones)
    n_archivos = sum(len(rutas) for rutas in colisiones.values())
    verbo = "se repite" if n_nombres == 1 else "se repiten"
    print(
        f"[aviso] {n_nombres} {_pluralizar('nombre', n_nombres)} de archivo "
        f"{verbo} en {n_archivos} {_pluralizar('archivo', n_archivos)}; se "
        f"desambiguan por ruta y se marcan con meta['fuente_ambigua']",
        file=sys.stderr,
    )


def _pluralizar(sustantivo: str, cantidad: int) -> str:
    """Añade una "s" si ``cantidad`` no es 1."""
    return sustantivo if cantidad == 1 else f"{sustantivo}s"


# --- recorrido ----------------------------------------------------------------


def _listar_archivos(entrada: Path) -> list[Path]:
    """Todos los archivos del corpus, en orden estable.

    Se ordena explícitamente: el orden de ``rglob`` depende del sistema de
    archivos y bastaría para que dos corridas difieran.
    """
    if not entrada.is_dir():
        raise ValueError(f"el directorio de entrada no existe: {entrada}")

    return sorted(
        (
            ruta
            for ruta in entrada.rglob("*")
            if ruta.is_file() and not _es_ruido_del_sistema(ruta)
        ),
        key=lambda ruta: ruta.as_posix(),
    )


# Metadatos que dejan Finder y el Explorador. El corpus viene de un Mac.
_RUIDO_DEL_SISTEMA = frozenset({".ds_store", "thumbs.db", "desktop.ini"})


def _es_ruido_del_sistema(ruta: Path) -> bool:
    """``True`` para los metadatos que deja el sistema de archivos.

    No son "formatos sin extractor" —eso es un `.docx`— y contarlos como tales
    escondería a los que sí hay que mirar. ``._nombre`` es el gemelo AppleDouble.
    """
    return ruta.name.lower() in _RUIDO_DEL_SISTEMA or ruta.name.startswith("._")


def _tiene_extractor(ruta: Path) -> bool:
    return ruta.suffix.lower() in EXTRACTORES


def _ruta_relativa(ruta: Path, raiz: Path) -> str:
    """Ruta POSIX relativa a la raíz del corpus. Es la clave de join con el índice."""
    return ruta.relative_to(raiz).as_posix()


def _agrupar_colisiones(rutas: list[Path], raiz: Path) -> dict[str, list[str]]:
    """Nombres de archivo que aparecen en más de una ruta.

    No lanza: en el corpus hay 59 nombres en 186 archivos y son colisiones
    legítimas. Se marcan como ``fuente_ambigua`` y se sigue.
    """
    por_nombre: dict[str, list[str]] = {}
    for ruta in rutas:
        por_nombre.setdefault(ruta.name, []).append(_ruta_relativa(ruta, raiz))
    return {nombre: rs for nombre, rs in por_nombre.items() if len(rs) > 1}


def _verificar_doc_ids(identidades: list[_Identidad]) -> None:
    """Única condición que sigue deteniendo la corrida: con dos ``doc_id``
    iguales, el JSON de uno sobrescribe al del otro."""
    vistos: dict[str, str] = {}
    for identidad in identidades:
        anterior = vistos.get(identidad.doc_id)
        if anterior is not None:
            raise ValueError(
                f"doc_id duplicado {identidad.doc_id!r}: lo comparten "
                f"{anterior!r} y {identidad.ruta_relativa!r}. "
                f"Un documento sobrescribiría al otro."
            )
        vistos[identidad.doc_id] = identidad.ruta_relativa


def _fenomeno_de_carpeta(ruta_relativa: str) -> int | None:
    """Fenómeno declarado por alguna carpeta del camino, o ``None``.

    ``None`` y no el valor por defecto, para que quien llame pueda distinguir "lo
    dice la carpeta" de "no lo dice nadie" y anotarlo en ``origen_fenomeno``.
    """
    for parte in PurePosixPath(ruta_relativa).parts[:-1]:
        for patron in _PATRONES_FENOMENO:
            coincidencia = patron.match(parte)
            if coincidencia:
                return int(coincidencia.group(1))
    return None


def _identidad_de(
    ruta: Path,
    raiz: Path,
    indice: dict[str, EntradaIndice] | None,
    ambiguos: set[str],
    fenomeno_por_defecto: int,
) -> _Identidad:
    """Resuelve identidad y fenómeno, del más fiable al menos.

    ``doc_id`` sale del índice si el archivo está listado; si no, de la **ruta
    relativa** y no del nombre, que se repite.
    """
    ruta_relativa = _ruta_relativa(ruta, raiz)
    # ``is not None``: un índice vacío sigue siendo un índice.
    entrada = indice.get(ruta_relativa) if indice is not None else None

    if entrada is not None:
        return _Identidad(
            ruta=ruta,
            ruta_relativa=ruta_relativa,
            doc_id=entrada.doc_id,
            fenomeno=entrada.fenomeno,
            origen_doc_id="indice",
            origen_fenomeno="indice",
            observatorio=entrada.observatorio,
            codigo_observatorio=entrada.codigo_observatorio,
            fuente_ambigua=ruta.name in ambiguos,
        )

    de_carpeta = _fenomeno_de_carpeta(ruta_relativa)
    return _Identidad(
        ruta=ruta,
        ruta_relativa=ruta_relativa,
        doc_id=calcular_doc_id(ruta_relativa),
        fenomeno=de_carpeta if de_carpeta is not None else fenomeno_por_defecto,
        origen_doc_id="derivado",
        origen_fenomeno="carpeta" if de_carpeta is not None else "defecto",
        observatorio=None,
        codigo_observatorio=None,
        fuente_ambigua=ruta.name in ambiguos,
    )


def _meta_de(identidad: _Identidad) -> dict:
    """Metadata que el orquestador añade a la que traiga el extractor.

    ``observatorio`` no es decorativo: es post-filtro y prefijo de contexto.
    """
    meta = {
        "ruta_relativa": identidad.ruta_relativa,
        "fuente_ambigua": identidad.fuente_ambigua,
        "origen_doc_id": identidad.origen_doc_id,
        "origen_fenomeno": identidad.origen_fenomeno,
    }
    if identidad.observatorio is not None:
        meta["observatorio"] = identidad.observatorio
        meta["codigo_observatorio"] = identidad.codigo_observatorio
    return meta


# --- extracción ----------------------------------------------------------------


def _extraer_todos(
    identidades: list[_Identidad],
    procesos: int,
    salida: Path,
    reciclar_cada: int,
) -> list[Documento]:
    """Extrae todos los documentos, repartiéndolos entre procesos si se pide.

    El paralelismo no cambia el resultado: cada extracción es independiente y la
    lista se ordena después. Cada documento se escribe en cuanto termina —de ahí
    ``as_completed`` y no ``pool.map``— y el pool entero se recicla cada
    ``procesos * reciclar_cada`` documentos para recuperar la memoria que
    pdfminer retiene. Con ``procesos=1`` no se arranca ningún pool.
    """
    if procesos <= 1 or len(identidades) < 2:
        documentos = []
        for identidad in identidades:
            documento = _extraer_documento(identidad)
            _escribir_json(salida / f"{documento.doc_id}.json", documento_a_dict(documento))
            documentos.append(documento)
        return documentos

    documentos = []
    tamano_lote = procesos * reciclar_cada
    for inicio in range(0, len(identidades), tamano_lote):
        lote = identidades[inicio : inicio + tamano_lote]
        with ProcessPoolExecutor(max_workers=procesos) as pool:
            futuros = [pool.submit(_extraer_documento, identidad) for identidad in lote]
            for futuro in as_completed(futuros):
                documento = futuro.result()
                _escribir_json(salida / f"{documento.doc_id}.json", documento_a_dict(documento))
                documentos.append(documento)
    return documentos


def _extraer_documento(identidad: _Identidad) -> Documento:
    """Invoca al extractor correspondiente, blindando el pipeline: cualquier
    fallo se convierte en un documento válido con el motivo en ``errores``."""
    modulo, formato = EXTRACTORES[identidad.ruta.suffix.lower()]
    try:
        documento = modulo.extraer(identidad.ruta, identidad.fenomeno)
    except NotImplementedError as exc:
        documento = _documento_fallido(
            identidad, formato, f"extractor de {formato} no implementado: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - blindaje deliberado
        documento = _documento_fallido(
            identidad,
            formato,
            f"fallo del extractor de {formato} ({type(exc).__name__}): {exc}",
        )
    return _con_identidad(documento, identidad)


def _con_identidad(documento: Documento, identidad: _Identidad) -> Documento:
    """Impone la identidad resuelta sobre lo que devolvió el extractor.

    El extractor solo ve su archivo: no sabe su ``DOC_ID`` ni si su nombre choca
    con otro. ``fuente`` no se toca nunca.
    """
    meta = dict(documento.meta)
    meta.update(_meta_de(identidad))
    return replace(
        documento, doc_id=identidad.doc_id, fenomeno=identidad.fenomeno, meta=meta
    )


def _documento_fallido(identidad: _Identidad, formato: str, motivo: str) -> Documento:
    """Documento válido que representa una extracción que no se pudo hacer."""
    return Documento(
        doc_id=identidad.doc_id,
        fuente=identidad.ruta.name,
        formato=formato,
        fenomeno=identidad.fenomeno,
        idioma="es",
        bloques=[],
        meta={},
        errores=[motivo],
    )


# --- persistencia --------------------------------------------------------------


def _limpiar_salida(salida: Path) -> None:
    """Borra los productos de una corrida anterior.

    Por patrón y no por autoría: no distingue un ``.json`` propio de uno ajeno,
    así que ``--salida`` tiene que ser un directorio dedicado al pipeline.
    """
    for archivo in sorted(salida.glob("*.json")):
        archivo.unlink()
    manifiesto = salida / NOMBRE_MANIFIESTO
    if manifiesto.exists():
        manifiesto.unlink()


def _escribir_json(ruta: Path, datos: dict) -> None:
    """Escribe JSON reproducible byte a byte: claves ordenadas y saltos Unix
    aunque se corra en Windows."""
    contenido = json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2)
    ruta.write_text(contenido + "\n", encoding="utf-8", newline="\n")


def _escribir_manifiesto(entradas: list[dict], ruta: Path) -> None:
    """Escribe entradas ya resueltas, una por línea.

    Toma dicts y no ``Documento`` porque ``solo_doc_ids`` mezcla entradas recién
    extraídas con otras leídas tal cual del manifiesto anterior.
    """
    lineas = [json.dumps(entrada, ensure_ascii=False, sort_keys=True) for entrada in entradas]
    contenido = "".join(f"{linea}\n" for linea in lineas)
    ruta.write_text(contenido, encoding="utf-8", newline="\n")


def _entrada_de_manifiesto(documento: Documento) -> dict:
    """Resumen de una línea del manifiesto: lo justo para auditar el corpus
    entero sin abrir 1826 archivos."""
    return {
        "doc_id": documento.doc_id,
        "fuente": documento.fuente,
        "formato": documento.formato,
        "fenomeno": documento.fenomeno,
        "idioma": documento.idioma,
        "n_bloques": len(documento.bloques),
        "n_chars": sum(len(bloque.texto) for bloque in documento.bloques),
        "n_errores": len(documento.errores),
        "observatorio": documento.meta.get("observatorio"),
        "fuente_ambigua": documento.meta.get("fuente_ambigua", False),
    }


def cargar_manifiesto(ruta: Path) -> list[dict]:
    """Lee un manifiesto JSONL. Útil para comparar corridas."""
    contenido = Path(ruta).read_text(encoding="utf-8")
    return [json.loads(linea) for linea in contenido.splitlines() if linea.strip()]


# --- interfaz de línea de comandos ---------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entrada", required=True, type=Path, help="directorio del corpus")
    parser.add_argument("--salida", required=True, type=Path, help="directorio de resultados")
    parser.add_argument(
        "--fenomeno",
        type=int,
        default=1,
        choices=(1, 2, 3),
        help="fenómeno por defecto cuando no se deduce del directorio",
    )
    parser.add_argument(
        "--indice",
        type=Path,
        default=None,
        help=(
            "ruta al Indice_Datos_Codefest.xlsx. Opcional: sin él se deduce el "
            "fenómeno de la carpeta y el doc_id de la ruta relativa"
        ),
    )
    parser.add_argument(
        "--limpiar",
        action="store_true",
        help="borra los resultados de la corrida anterior antes de escribir",
    )
    parser.add_argument(
        "--procesos",
        type=int,
        default=None,
        help=(
            "procesos de extracción en paralelo. El resultado es idéntico al "
            "secuencial; solo cambia el tiempo. Sin este flag se calcula según "
            "la RAM disponible (ver --reciclar-cada)"
        ),
    )
    parser.add_argument(
        "--reciclar-cada",
        type=int,
        default=DOCUMENTOS_POR_RECICLAJE,
        help=(
            "documentos por worker antes de reciclar el pool entero (se "
            "multiplica por --procesos), para que el sistema operativo "
            "recupere la memoria que pdfminer no libera solo "
            f"(por defecto {DOCUMENTOS_POR_RECICLAJE})"
        ),
    )
    parser.add_argument(
        "--reintentar-errores",
        action="store_true",
        help=(
            "reextrae solo los doc_id que quedaron con errores en el "
            "manifiesto de --salida; el resto del corpus no se toca. "
            "Requiere una corrida completa previa en --salida; incompatible "
            "con --limpiar"
        ),
    )
    return parser


def _procesos_por_defecto() -> int:
    """Procesos que caben en la RAM libre ahora mismo, sin superar los núcleos."""
    disponible_mb = psutil.virtual_memory().available / (1024 * 1024)
    por_ram = max(1, int(disponible_mb // RAM_MB_POR_PROCESO))
    return max(1, min(por_ram, os.cpu_count() or 1))


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)

    if args.reintentar_errores and args.limpiar:
        parser.error("--reintentar-errores y --limpiar son incompatibles")

    solo_doc_ids = None
    if args.reintentar_errores:
        ruta_manifiesto = args.salida / NOMBRE_MANIFIESTO
        if not ruta_manifiesto.exists():
            parser.error(
                f"--reintentar-errores necesita un manifiesto previo en "
                f"{args.salida}, y no se encontró {ruta_manifiesto}"
            )
        solo_doc_ids = {
            entrada["doc_id"]
            for entrada in cargar_manifiesto(ruta_manifiesto)
            if entrada["n_errores"] > 0
        }
        if not solo_doc_ids:
            print("--reintentar-errores: el manifiesto no tiene documentos con error", file=sys.stderr)
            return 0
        print(
            f"--reintentar-errores: {len(solo_doc_ids)} documentos con error en "
            "el manifiesto anterior, el resto se conserva tal cual",
            file=sys.stderr,
        )

    indice = cargar_indice(args.indice) if args.indice else None
    if indice is not None:
        print(f"índice: {len(indice)} entradas desde {args.indice}", file=sys.stderr)

    if args.procesos is not None:
        procesos = args.procesos
    else:
        procesos = _procesos_por_defecto()
        print(
            f"--procesos no indicado: se usan {procesos} según la RAM disponible "
            f"({RAM_MB_POR_PROCESO} MB reservados por proceso)",
            file=sys.stderr,
        )

    documentos, reporte = procesar_corpus(
        args.entrada,
        args.salida,
        fenomeno_por_defecto=args.fenomeno,
        limpiar=args.limpiar,
        indice=indice,
        procesos=procesos,
        reciclar_cada=args.reciclar_cada,
        solo_doc_ids=solo_doc_ids,
    )

    con_errores = [documento for documento in documentos if documento.errores]
    bloques = sum(len(documento.bloques) for documento in documentos)
    etiqueta = "reprocesados" if solo_doc_ids is not None else "documentos"
    print(
        f"{len(documentos)} {etiqueta}, {bloques} bloques, "
        f"{len(con_errores)} con errores -> {args.salida}"
    )

    if solo_doc_ids is None:
        _informar_cobertura(reporte)

    for documento in con_errores:
        print(f"  [error] {documento.fuente}: {documento.errores[0]}", file=sys.stderr)

    return 0


def _informar_cobertura(reporte: ReporteCobertura, muestra: int = 10) -> None:
    """Vuelca el reporte a stderr. No devuelve error: en el corpus hay 13
    archivos legítimos fuera del índice y abortar por eso sería peor."""
    print("--- cobertura ---", file=sys.stderr)
    print(f"  doc_id por origen:   {reporte.por_origen_doc_id}", file=sys.stderr)
    print(f"  fenomeno por origen: {reporte.por_origen_fenomeno}", file=sys.stderr)
    print(
        f"  fuentes ambiguas:    {reporte.nombres_ambiguos} nombres, "
        f"{reporte.archivos_ambiguos} archivos",
        file=sys.stderr,
    )

    for titulo, rutas in (
        ("archivos sin extractor registrado", reporte.sin_extractor),
        ("entradas del índice sin archivo en disco", reporte.huerfanos_del_indice),
        ("archivos en disco fuera del índice (omitidos)", reporte.fuera_del_indice),
    ):
        print(f"  {titulo}: {len(rutas)}", file=sys.stderr)
        for ruta in rutas[:muestra]:
            print(f"      {ruta}", file=sys.stderr)
        if len(rutas) > muestra:
            print(f"      ... y {len(rutas) - muestra} más", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
