"""Recorre un directorio de entrada, extrae cada documento y lo persiste.

Es el único módulo que escribe a disco. Los extractores son funciones puras.

Produce:

- ``{salida}/{doc_id}.json``: un ``Documento`` serializado por archivo.
- ``{salida}/manifiesto.jsonl``: una línea por documento, ordenada por
  ``(fuente, ruta_relativa)``.

El manifiesto existe para hacer ``diff`` entre corridas y detectar regresiones,
así que su estabilidad importa tanto como su contenido: claves ordenadas,
saltos de línea Unix y orden por ``(fuente, ruta_relativa)``. Se desempata por
ruta porque hay 59 nombres de archivo repetidos en 186 archivos del corpus de
ADL: ordenar solo por ``fuente`` dejaría el orden relativo de esos homónimos a
merced del sistema de archivos.

Uso::

    python orquestador.py --entrada fixtures --salida extraidos
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import ModuleType

from contrato import Documento, calcular_doc_id, documento_a_dict
from extractores import imagen, json_, pbf, pdf, tabular, texto
from indice import EntradaIndice, cargar_indice

NOMBRE_MANIFIESTO = "manifiesto.jsonl"

# Extensión -> (módulo extractor, formato declarado en el contrato).
# Una extensión que no esté aquí se ignora, pero no en silencio: el reporte de
# cobertura de `main()` la lista en stderr con su extensión.
# Sin `.html`/`.htm`: el corpus real de ADL no trae ese formato, así que no
# tiene extractor registrado.
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

    Se calcula para todos los archivos de golpe porque la ambigüedad de un
    nombre solo se sabe mirando el conjunto, y porque el choque de ``doc_id``
    hay que detectarlo antes de escribir el primer JSON, no a mitad.
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
    """Qué quedó fuera y por qué.

    Existe porque la versión anterior filtraba en silencio: una extensión sin
    extractor desaparecía sin dejar rastro y nadie se enteraba hasta el cierre.
    """

    sin_extractor: list[str]
    """Rutas relativas de archivos en disco cuya extensión no está en ``EXTRACTORES``."""

    huerfanos_del_indice: list[str]
    """Rutas que el índice lista pero que no existen en disco.

    Se calcula contra **todos** los archivos del disco, tengan o no extractor:
    uno indexado sin extractor sigue estando ahí y sale en ``sin_extractor``,
    no aquí. Confundir ambos casos mandaría a un operador a buscar un archivo
    que sí existe.
    """

    fuera_del_indice: list[str]
    """Rutas con extractor que están en disco pero el índice no lista."""

    por_origen_doc_id: dict[str, int]
    """Cuántos ``doc_id`` salieron de "indice" y cuántos de "derivado"."""

    por_origen_fenomeno: dict[str, int]
    """Cuántos fenómenos salieron de "indice", "carpeta" y "defecto"."""

    nombres_ambiguos: int
    """Nombres de archivo que aparecen en más de una ruta."""

    archivos_ambiguos: int
    """Archivos afectados por esos nombres."""


def procesar_corpus(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
) -> tuple[list[Documento], ReporteCobertura]:
    """Como :func:`procesar_directorio`, pero devolviendo también la cobertura.

    Cuando hay índice, el índice manda: solo se procesa lo que ADL lista. Un
    archivo en disco que el índice no menciona no es un documento de la entrega
    —en el corpus real son el enunciado, el propio índice y los catálogos de
    scraping—, así que se reporta y no se procesa.
    """
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

    documentos = [_extraer_documento(identidad) for identidad in identidades]
    documentos.sort(key=lambda doc: (doc.fuente, doc.meta["ruta_relativa"]))

    _escribir_salida(documentos, salida, limpiar=limpiar)

    reporte = ReporteCobertura(
        sin_extractor=sin_extractor,
        huerfanos_del_indice=huerfanos,
        fuera_del_indice=fuera_del_indice,
        por_origen_doc_id=_contar(i.origen_doc_id for i in identidades),
        por_origen_fenomeno=_contar(i.origen_fenomeno for i in identidades),
        nombres_ambiguos=len(colisiones),
        archivos_ambiguos=sum(len(rs) for rs in colisiones.values()),
    )
    return documentos, reporte


def procesar_directorio(
    entrada: Path,
    salida: Path,
    fenomeno_por_defecto: int = 1,
    limpiar: bool = False,
    indice: dict[str, EntradaIndice] | None = None,
) -> list[Documento]:
    """Extrae todos los documentos de ``entrada`` y los escribe en ``salida``.

    Devuelve los documentos ordenados por ``(fuente, ruta_relativa)``, el mismo
    orden en que se escribe el manifiesto. Se desempata por ruta porque hay
    nombres repetidos y ordenar solo por ``fuente`` dejaría su orden relativo a
    merced del sistema de archivos.

    Lanza ``ValueError`` únicamente si dos archivos acaban con el mismo
    ``doc_id``: entonces uno sobrescribiría al otro. Un nombre repetido ya no
    detiene nada, solo se marca en ``meta["fuente_ambigua"]``.
    """
    documentos, _ = procesar_corpus(
        entrada,
        salida,
        fenomeno_por_defecto=fenomeno_por_defecto,
        limpiar=limpiar,
        indice=indice,
    )
    return documentos


def _cruzar_con_indice(
    con_extractor: list[Path],
    todos: list[Path],
    entrada: Path,
    indice: dict[str, EntradaIndice] | None,
) -> tuple[list[Path], list[str], list[str]]:
    """Reparte los archivos entre los que el índice lista y los que no.

    Sin índice no hay nada que cruzar: se procesa todo lo que tenga extractor.
    Se compara con ``is None`` y no con verdad/falsedad: un índice vacío
    (``{}``) es un índice real —un xlsx con cabecera y sin filas— y debe seguir
    filtrando a "nada", no comportarse como si no se hubiera pasado ``--indice``.

    Los archivos que sí procesa el pipeline (``listadas``) y los que sobran del
    disco (``sueltas``) se calculan solo sobre ``con_extractor``, porque eso es
    lo único que se puede llegar a extraer. Los huérfanos, en cambio, se
    calculan contra ``todos`` los archivos del disco: un archivo que el índice
    lista y que existe en disco pero sin extractor registrado no es un
    huérfano, es un caso de ``sin_extractor``. Cruzarlo solo contra
    ``con_extractor`` lo reportaría como "no existe en disco" cuando sí existe.
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
    # Se recorre el índice, no un set, para que el orden sea el del archivo.
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
    """Añade una "s" si ``cantidad`` no es 1.

    El vocabulario del pipeline ("nombre", "archivo") es lo bastante simple
    como para no necesitar una librería de pluralización.
    """
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
        (ruta for ruta in entrada.rglob("*") if ruta.is_file()),
        key=lambda ruta: ruta.as_posix(),
    )


def _tiene_extractor(ruta: Path) -> bool:
    return ruta.suffix.lower() in EXTRACTORES


def _ruta_relativa(ruta: Path, raiz: Path) -> str:
    """Ruta POSIX relativa a la raíz del corpus. Es la clave de join con el índice."""
    return ruta.relative_to(raiz).as_posix()


def _agrupar_colisiones(rutas: list[Path], raiz: Path) -> dict[str, list[str]]:
    """Nombres de archivo que aparecen en más de una ruta.

    Ya no lanza: en el corpus de ADL hay 59 nombres repartidos en 186 archivos y
    son colisiones legítimas —el mismo informe archivado por tipo, el mismo tile
    en varios niveles de zoom—. Abortar por ellas dejaba el pipeline sin
    procesar nada. Se registran como ``fuente_ambigua`` y se sigue.

    El orden es determinista: ``rutas`` viene ordenada y los dict de Python
    conservan el orden de inserción.
    """
    por_nombre: dict[str, list[str]] = {}
    for ruta in rutas:
        por_nombre.setdefault(ruta.name, []).append(_ruta_relativa(ruta, raiz))
    return {nombre: rs for nombre, rs in por_nombre.items() if len(rs) > 1}


def _verificar_doc_ids(identidades: list[_Identidad]) -> None:
    """Única condición que sigue deteniendo la corrida.

    Un nombre repetido es un problema del corpus y se anota. Un ``doc_id``
    repetido es un problema de identidad: el JSON de un documento sobrescribiría
    al del otro y el manifiesto tendría dos líneas apuntando al mismo archivo.
    """
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

    Devuelve ``None`` en vez del valor por defecto para que quien llame pueda
    distinguir "lo dice la carpeta" de "no lo dice nadie" y registrarlo en
    ``origen_fenomeno``. Con la versión anterior los 1367 documentos de F2 y F3
    se etiquetaban como fenómeno 1 en silencio.
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

    ``doc_id`` sale del índice de ADL si el archivo está listado. Si no, se
    deriva de la **ruta relativa** y no del nombre: derivarlo del nombre le daba
    el mismo ``doc_id`` a los 7 PDF homónimos de CSET.
    """
    ruta_relativa = _ruta_relativa(ruta, raiz)
    # ``is not None`` y no verdad/falsedad: un índice vacío sigue siendo un
    # índice (nada listado, luego nada tiene entrada), no la ausencia de uno.
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

    ``observatorio`` no es decorativo: sirve de post-filtro y, más adelante, de
    prefijo para enriquecer el texto del chunk antes de codificarlo.
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


def _extraer_documento(identidad: _Identidad) -> Documento:
    """Invoca al extractor correspondiente, blindando el pipeline.

    Los stubs aún no implementados lanzan ``NotImplementedError``, y un
    extractor nuevo puede tener errores. Ninguno de los dos casos puede detener
    la corrida: se devuelve un documento válido con el motivo en ``errores``.
    """
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

    El extractor solo ve su archivo, así que no puede saber su ``DOC_ID`` de ADL
    ni si su nombre choca con otro. ``fuente`` no se toca nunca: es el campo de
    emparejamiento con el jurado.
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


def _escribir_salida(documentos: list[Documento], salida: Path, limpiar: bool) -> None:
    salida.mkdir(parents=True, exist_ok=True)
    if limpiar:
        _limpiar_salida(salida)

    for documento in documentos:
        _escribir_json(salida / f"{documento.doc_id}.json", documento_a_dict(documento))

    _escribir_manifiesto(documentos, salida / NOMBRE_MANIFIESTO)


def _limpiar_salida(salida: Path) -> None:
    """Borra los productos de una corrida anterior.

    Sin esto, los documentos de un corpus previo quedan como huérfanos y
    ensucian el diff. Se restringe por patrón (``*.json`` y
    ``manifiesto.jsonl``), no por autoría: no distingue un ``.json`` que
    escribió este módulo de uno ajeno que viva en el mismo directorio de
    salida, así que quien pase ``--salida`` debe usar un directorio dedicado
    al pipeline, no uno donde guarde otra cosa.
    """
    for archivo in sorted(salida.glob("*.json")):
        archivo.unlink()
    manifiesto = salida / NOMBRE_MANIFIESTO
    if manifiesto.exists():
        manifiesto.unlink()


def _escribir_json(ruta: Path, datos: dict) -> None:
    """Escribe JSON de forma reproducible byte a byte.

    ``sort_keys`` fija el orden de las claves, ``indent`` la forma, y
    ``newline="\\n"`` evita que Windows escriba CRLF y rompa el diff con las
    corridas hechas en Linux.
    """
    contenido = json.dumps(datos, ensure_ascii=False, sort_keys=True, indent=2)
    ruta.write_text(contenido + "\n", encoding="utf-8", newline="\n")


def _escribir_manifiesto(documentos: list[Documento], ruta: Path) -> None:
    lineas = [
        json.dumps(_entrada_de_manifiesto(documento), ensure_ascii=False, sort_keys=True)
        for documento in documentos
    ]
    contenido = "".join(f"{linea}\n" for linea in lineas)
    ruta.write_text(contenido, encoding="utf-8", newline="\n")


def _entrada_de_manifiesto(documento: Documento) -> dict:
    """Resumen de una línea del manifiesto.

    ``observatorio`` y ``fuente_ambigua`` están aquí y no solo en el JSON para
    poder filtrar y auditar el corpus entero sin abrir 1826 archivos.
    """
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)

    indice = cargar_indice(args.indice) if args.indice else None
    if indice is not None:
        print(f"índice: {len(indice)} entradas desde {args.indice}", file=sys.stderr)

    documentos, reporte = procesar_corpus(
        args.entrada,
        args.salida,
        fenomeno_por_defecto=args.fenomeno,
        limpiar=args.limpiar,
        indice=indice,
    )

    con_errores = [documento for documento in documentos if documento.errores]
    bloques = sum(len(documento.bloques) for documento in documentos)
    print(
        f"{len(documentos)} documentos, {bloques} bloques, "
        f"{len(con_errores)} con errores -> {args.salida}"
    )

    _informar_cobertura(reporte)

    for documento in con_errores:
        print(f"  [error] {documento.fuente}: {documento.errores[0]}", file=sys.stderr)

    return 0


def _informar_cobertura(reporte: ReporteCobertura, muestra: int = 10) -> None:
    """Vuelca el reporte a stderr.

    No devuelve código de error: los tres conteos en cero es lo deseable, pero
    en el corpus de ADL hay 13 archivos legítimos fuera del índice —el
    enunciado, el propio índice y los catálogos de scraping— y abortar por eso
    sería peor que informarlo.
    """
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
