"""Construye el índice vectorial y responde las consultas contra él.

Es la etapa que el enunciado exige poder reproducir (§1.4): mismo corpus y
misma configuración → mismo índice. Por eso no hay ni un solo punto de
aleatoriedad aquí dentro, el orden de los vectores es el del archivo de
entrada, y el modelo corre en ``eval()`` y float32.

Escribe tres archivos en el directorio de salida:

- ``index.faiss`` — ``IndexFlatIP`` con los vectores **normalizados**, que es
  lo que §5.2 del enunciado pide para que el producto interno sea el coseno.
- ``metadata.jsonl`` — una línea por vector, **en el mismo orden que el
  índice**: la fila ``i`` del índice es la línea ``i`` de este archivo. Lleva
  los ocho campos obligatorios de la Tabla 1 y no lleva ``texto_enriquecido``:
  el enriquecimiento entra al encoder, no a lo que se le reporta al jurado.
- ``reporte_indice.json`` — con qué se construyó y qué salió. Material directo
  para el informe técnico, y la evidencia de las dos comprobaciones que fallan
  en silencio: cuántos fragmentos se truncaron (debe ser cero) y qué norma
  tienen los vectores (debe ser 1).

Lo que se codifica es ``texto_enriquecido`` —observatorio, título y breadcrumb
de secciones por delante del texto—, no ``texto``. La justificación está en
``docs/decisiones/enriquecimiento-de-contexto.md``.

La segunda etapa consume ese índice y escribe el entregable ``resultados.jsonl``:
una línea por consulta con los tres documentos del top-3 (§8.6). El índice se
carga de disco, así que responder no obliga a reconstruir nada.

Uso::

    python generador.py --entrada fragmentos --salida indice
    python generador.py --entrada fragmentos --salida indice --desarrollo

    python generador.py --indice indice \\
        --consultas base_documental/Extracto_Preguntas_50_v2.pdf \\
        --resultados entrega/resultados.jsonl

Las dos etapas seguidas, si se quiere construir y responder en una sola corrida::

    python generador.py --entrada fragmentos --salida indice \\
        --consultas base_documental/Extracto_Preguntas_50_v2.pdf \\
        --resultados entrega/resultados.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from encoder import (
    CONFIG_POR_DEFECTO,
    NOMBRE_MODELO_DESARROLLO,
    POOLING,
    ConfigEncoder,
    normalizar,
    texto_de_consulta,
    texto_de_fragmento,
    truncar_dimension,
)

NOMBRE_ENTRADA = "fragmentos.jsonl"
NOMBRE_INDICE = "index.faiss"
NOMBRE_METADATA = "metadata.jsonl"
NOMBRE_REPORTE = "reporte_indice.json"
NOMBRE_RESULTADOS = "resultados.jsonl"

# Documentos por consulta en el entregable. Es el top-3 de §8.6, no un
# parámetro de gusto: F1@3 se mide exactamente sobre tres.
TOP_DOCUMENTOS = 3

# Fragmentos que se piden a FAISS antes de agregar a documento. Tiene que
# sobrar por encima de TOP_DOCUMENTOS: el top-k viene en fragmentos y varios
# caen al mismo doc_id, así que pedir 3 fragmentos puede dar un solo documento.
K_FRAGMENTOS = 50

CAMPO_A_CODIFICAR = "texto_enriquecido"

# Se excluye de ``metadata.jsonl`` y de ninguna otra parte: el prefijo de
# contexto es una decisión del indexador, no un dato del corpus, y devolverlo
# como si fuera el texto del fragmento falsearía lo que se reporta (§2.2 del
# spec del fragmentador).
CAMPOS_EXCLUIDOS: tuple[str, ...] = (CAMPO_A_CODIFICAR,)

# Cada cuántos vectores se avisa por stderr. Codificar 140k fragmentos son
# minutos u horas según el hardware: sin avance no se distingue de un cuelgue.
PROGRESO_CADA = 5_000


@dataclass(frozen=True)
class ReporteIndice:
    """Qué se indexó y con qué. Se serializa tal cual a ``reporte_indice.json``."""

    n_vectores: int
    dimension: int
    modelo: str
    pooling: str
    prefijo_fragmento: str
    n_truncados: int
    """Fragmentos cuyo texto excede la ventana del encoder. **Debe ser cero**:
    uno solo significa un fragmento indexado a medias sin que nada avise."""

    tokens_max: int
    tokens_p95: int
    norma_min: float
    norma_max: float
    """Verificación explícita de §14.2. Ambas deben dar 1,0 ± 1e-5."""

    vectores_por_fenomeno: dict[str, int]
    vectores_por_formato: dict[str, int]

    n_reutilizados: int = 0
    """Vectores que salieron de la caché en vez de un pase del encoder.

    Son fragmentos con texto idéntico: el corpus trae lit-covid en CSV y en
    XLSX. Cada uno conserva su fila y su ``fuente``; lo único que se ahorra es
    volver a calcular un vector que ya se calculó."""


def generar_indice(
    entrada: Path,
    salida: Path,
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
    codificar: Callable[[list[str]], np.ndarray] | None = None,
    contar_tokens: Callable[[str], int] | None = None,
    on_progreso: Callable[[int, int], None] | None = None,
) -> ReporteIndice:
    """Codifica los fragmentos de ``entrada`` y escribe el índice en ``salida``.

    ``entrada`` puede ser el ``fragmentos.jsonl`` o el directorio que lo
    contiene. ``codificar`` y ``contar_tokens`` se inyectan: por defecto salen
    del encoder configurado, y las pruebas los sustituyen para no descargar
    1,2 GB de pesos por cada aserción sobre el orden de las filas.
    """
    import faiss

    registros = _cargar_fragmentos(_ruta_de_entrada(entrada))
    codificar = codificar or _codificador(config)
    contar_tokens = contar_tokens or _contador(config)

    indice = faiss.IndexFlatIP(config.dimension)
    total = len(registros)

    # Los tokens se cuentan de una vez y antes de codificar nada: hacen falta
    # para dimensionar los lotes, y contarlos sobre la marcha obligaría a fijar
    # el tamaño del lote sin saber cuánto mide lo que viene dentro.
    tokens = [
        contar_tokens(texto_de_fragmento(r[CAMPO_A_CODIFICAR], config)) for r in registros
    ]
    truncados = sum(1 for cuenta in tokens if cuenta > config.ventana_modelo)

    # El corpus trae el mismo dataset en dos formatos (lit-covid, en CSV y en
    # XLSX): mismas filas, dos ``fuente`` distintos. Cada uno necesita su fila
    # en el índice —si a uno le falta, no puede aparecer en el top-3 y pierde
    # F1@3 (§10.2.1)— pero el pase del encoder es idéntico y basta con uno.
    a_codificar = [texto_de_fragmento(r[CAMPO_A_CODIFICAR], config) for r in registros]
    repetidos = _textos_repetidos(a_codificar)
    cache: dict[str, np.ndarray] = {}
    reutilizados = 0

    norma_min, norma_max = float("inf"), 0.0
    procesados = 0

    for posiciones in lotes_por_presupuesto(tokens, config):
        textos = [a_codificar[i] for i in posiciones]
        procesados += len(posiciones)

        crudos, ahorrados = _codificar_reutilizando(textos, codificar, cache, repetidos)
        reutilizados += ahorrados
        vectores = normalizar(truncar_dimension(crudos, config.dimension))
        normas = np.linalg.norm(vectores, axis=1)
        norma_min = min(norma_min, float(normas.min()))
        norma_max = max(norma_max, float(normas.max()))

        indice.add(vectores)
        if on_progreso is not None:
            on_progreso(procesados, total)

    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    faiss.write_index(indice, str(salida / NOMBRE_INDICE))
    _escribir_metadata(registros, salida / NOMBRE_METADATA)

    reporte = ReporteIndice(
        n_vectores=indice.ntotal,
        dimension=config.dimension,
        modelo=config.modelo,
        pooling=POOLING,
        prefijo_fragmento=config.prefijo_fragmento,
        n_truncados=truncados,
        tokens_max=max(tokens),
        tokens_p95=_percentil(tokens, 95),
        norma_min=norma_min,
        norma_max=norma_max,
        vectores_por_fenomeno=_contar(str(r.get("fenomeno")) for r in registros),
        vectores_por_formato=_contar(str(r.get("formato")) for r in registros),
        n_reutilizados=reutilizados,
    )
    (salida / NOMBRE_REPORTE).write_text(
        json.dumps(asdict(reporte), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return reporte


# --- entrada ---------------------------------------------------------------------


def _ruta_de_entrada(entrada: Path) -> Path:
    ruta = Path(entrada)
    return ruta / NOMBRE_ENTRADA if ruta.is_dir() else ruta


def _cargar_fragmentos(ruta: Path) -> list[dict[str, Any]]:
    """Lee el JSONL entero a memoria.

    140k registros con su texto son del orden de 350 MB: cabe, y tenerlos todos
    permite escribir la metadata en el mismo orden en que se indexaron sin
    releer el archivo ni fiarse de que dos recorridos coincidan.
    """
    if not ruta.is_file():
        raise ValueError(f"no existe el archivo de fragmentos: {ruta}")

    registros: list[dict[str, Any]] = []
    with ruta.open(encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError as error:
                raise ValueError(f"{ruta}:{numero} no es JSON válido: {error}") from error
            if not registro.get(CAMPO_A_CODIFICAR):
                raise ValueError(
                    f"{ruta}:{numero} no trae {CAMPO_A_CODIFICAR!r}, que es lo que "
                    f"se codifica. ¿Viene de una versión antigua del fragmentador?"
                )
            registros.append(registro)

    if not registros:
        raise ValueError(f"{ruta} está sin fragmentos: no hay nada que indexar")
    return registros


def _textos_repetidos(textos: list[str]) -> set[str]:
    """Los textos que aparecen más de una vez. Solo esos merecen caché.

    Cachear todo costaría un vector por fragmento en RAM —1 GB largo en el
    corpus completo— para no reutilizar ninguno.
    """
    vistos: set[str] = set()
    repetidos: set[str] = set()
    for texto in textos:
        if texto in vistos:
            repetidos.add(texto)
        else:
            vistos.add(texto)
    return repetidos


def _codificar_reutilizando(
    textos: list[str],
    codificar: Callable[[list[str]], np.ndarray],
    cache: dict[str, np.ndarray],
    repetidos: set[str],
) -> tuple[np.ndarray, int]:
    """Codifica solo lo que no se haya codificado ya, y devuelve el lote entero.

    El lote sale completo y en su orden pase lo que pase: el ahorro está en lo
    que se le pide al encoder, nunca en lo que se le entrega al índice.
    """
    pendientes: list[str] = []
    for texto in textos:
        if texto not in cache and texto not in pendientes:
            pendientes.append(texto)

    frescos: dict[str, np.ndarray] = {}
    if pendientes:
        matriz = codificar(pendientes)
        frescos = {texto: matriz[fila] for fila, texto in enumerate(pendientes)}
        for texto, vector in frescos.items():
            if texto in repetidos:
                cache[texto] = vector

    ahorrados = len(textos) - len(pendientes)
    return np.stack([cache[t] if t in cache else frescos[t] for t in textos]), ahorrados


def lotes_por_presupuesto(
    longitudes: list[int], config: ConfigEncoder
) -> Iterator[list[int]]:
    """Agrupa índices en lotes que caben en memoria, en el orden de entrada.

    Dos topes a la vez: ``config.lote`` textos y ``config.presupuesto_atencion``
    en ``lote × longitud²``. El segundo es el que importa, porque el coste de la
    máscara de atención de ModernBERT no lo fija el número de textos sino el
    cuadrado del más largo del lote, multiplicado por el lote entero.

    Un texto que por sí solo excede el presupuesto viaja en su propio lote: no
    se puede partir sin romper el fragmento, así que lo único que cabe hacer es
    no arrastrar a nadie con él (y, si ni así entra en la GPU, el encoder lo
    reintenta en CPU).

    El orden de salida es el de entrada, sin excepción: la fila ``i`` del índice
    tiene que seguir siendo la línea ``i`` de la metadata.
    """
    lote: list[int] = []
    mayor = 0

    for indice, longitud in enumerate(longitudes):
        candidato = max(mayor, longitud)
        if lote and (
            len(lote) >= config.lote
            or (len(lote) + 1) * candidato * candidato > config.presupuesto_atencion
        ):
            yield lote
            lote, mayor, candidato = [], 0, longitud

        lote.append(indice)
        mayor = candidato

    if lote:
        yield lote


# --- salida ----------------------------------------------------------------------


def _escribir_metadata(registros: list[dict[str, Any]], ruta: Path) -> None:
    ruta.write_text(
        "".join(
            json.dumps(metadata_de(r), ensure_ascii=False, sort_keys=True) + "\n"
            for r in registros
        ),
        encoding="utf-8",
        newline="\n",
    )


def metadata_de(registro: dict[str, Any]) -> dict[str, Any]:
    """El registro tal cual, sin los campos que no salen del indexador."""
    return {k: v for k, v in registro.items() if k not in CAMPOS_EXCLUIDOS}


def _contar(valores: Iterator[str]) -> dict[str, int]:
    conteo: dict[str, int] = {}
    for valor in valores:
        conteo[valor] = conteo.get(valor, 0) + 1
    return dict(sorted(conteo.items()))


def _percentil(valores: list[int], percentil: int) -> int:
    """Percentil por rango, sin interpolar: el resultado es un conteo real."""
    ordenados = sorted(valores)
    indice = round((percentil / 100) * len(ordenados) + 0.5) - 1
    return ordenados[max(0, min(indice, len(ordenados) - 1))]


# --- consultas -------------------------------------------------------------------


@dataclass(frozen=True)
class Consulta:
    """Una consulta de ADL, con el identificador que ella misma trae."""

    id: str
    texto: str


@dataclass(frozen=True)
class Candidato:
    """Un fragmento del top-k, antes de agregar a documento."""

    fila: int
    """Fila del índice, que es la línea de ``metadata.jsonl``."""

    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Recuperado:
    """Un documento del top-3, con el fragmento que lo puso ahí."""

    doc_id: str
    score: float
    n_fragmentos: int
    """Cuántos fragmentos suyos entraron en el top-k. Diagnóstico, no criterio:
    el puesto lo decide el mejor fragmento, no cuántos aporte."""

    metadata: dict[str, Any]


@dataclass(frozen=True)
class ReporteConsultas:
    """Qué se respondió y con qué. Se imprime; no se serializa a disco."""

    n_consultas: int
    n_vectores: int
    modelo: str
    prefijo_consulta: str
    k_fragmentos: int
    top_documentos: int
    idioma: str | None
    n_duplicados_descartados: int
    consultas_sin_top_completo: list[str]
    """Consultas que no llegaron a ``top`` documentos distintos. Cada una es un
    F1@3 mermado por construcción, así que salen nombradas y no como un total."""


def responder_consultas(
    indice: Path,
    consultas: Path | list[Consulta],
    salida: Path,
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
    codificar: Callable[[list[str]], np.ndarray] | None = None,
    k: int = K_FRAGMENTOS,
    top: int = TOP_DOCUMENTOS,
    idioma: str | None = None,
) -> ReporteConsultas:
    """Responde las consultas contra el índice de ``indice`` y escribe ``salida``.

    ``indice`` es el directorio que escribió :func:`generar_indice` (o el propio
    ``index.faiss``, y entonces se usa su carpeta). El índice se lee de disco:
    responder no reconstruye nada. ``codificar`` se inyecta igual que en la
    indexación, para que las pruebas no descarguen los pesos.

    La consulta se codifica con el **mismo** encoder y la misma dimensión con
    los que se construyó el índice. No se comprueba que el modelo sea el mismo
    —el índice no guarda de quién es cada vector—, así que responder con un
    modelo distinto del que indexó da resultados sin sentido y sin aviso: por
    eso ``reporte_indice.json`` deja escrito cuál fue.
    """
    import faiss

    directorio = Path(indice)
    if directorio.is_file():
        directorio = directorio.parent
    ruta_indice = directorio / NOMBRE_INDICE
    ruta_metadata = directorio / NOMBRE_METADATA

    for ruta in (ruta_indice, ruta_metadata):
        if not ruta.is_file():
            raise ValueError(f"no existe {ruta}: ¿se construyó el índice ahí?")

    banco = faiss.read_index(str(ruta_indice))
    desplazamientos = _desplazamientos_de_lineas(ruta_metadata)

    # La correspondencia fila ↔ línea es la única forma que tiene el buscador
    # de saber qué documento devolvió. Si no cuadra, todo lo que salga a
    # continuación es metadata de otro fragmento, y con muy buena pinta.
    if banco.ntotal != len(desplazamientos):
        raise ValueError(
            f"{ruta_indice.name} tiene {banco.ntotal} vectores y "
            f"{ruta_metadata.name} {len(desplazamientos)} líneas: no se "
            f"corresponden. Reconstruye el índice; los dos archivos salen de "
            f"la misma corrida o no valen."
        )
    if banco.d != config.dimension:
        raise ValueError(
            f"el índice es de {banco.d} dimensiones y se está consultando con "
            f"{config.dimension}: pasa --dimension {banco.d}"
        )

    pedidos = list(consultas) if isinstance(consultas, list) else cargar_consultas(consultas)
    if not pedidos:
        raise ValueError("no hay ni una consulta que responder")

    codificar = codificar or _codificador(config)
    vectores = _codificar_consultas(pedidos, codificar, config)

    # Pedir más de lo que hay hace que FAISS rellene con filas -1.
    k_efectivo = max(1, min(max(k, top), banco.ntotal))
    scores, filas = banco.search(vectores, k_efectivo)

    descartados = 0
    incompletas: list[str] = []
    lineas: list[str] = []

    with ruta_metadata.open("rb") as archivo:
        for orden, consulta in enumerate(pedidos):
            candidatos = [
                Candidato(
                    fila=int(fila),
                    score=float(score),
                    metadata=_metadata_en(archivo, desplazamientos[int(fila)]),
                )
                for fila, score in zip(filas[orden], scores[orden])
                if fila >= 0  # -1 = hueco de relleno cuando k supera lo indexado
            ]

            candidatos = filtrar_por_idioma(candidatos, idioma)
            candidatos, repetidos = deduplicar_por_texto(candidatos)
            descartados += repetidos

            documentos = agregar_por_documento(candidatos, top)
            if len(documentos) < top:
                incompletas.append(consulta.id)

            lineas.append(
                json.dumps(
                    registro_de_resultado(consulta, documentos), ensure_ascii=False
                )
            )

    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")

    return ReporteConsultas(
        n_consultas=len(pedidos),
        n_vectores=banco.ntotal,
        modelo=config.modelo,
        prefijo_consulta=config.prefijo_consulta,
        k_fragmentos=k_efectivo,
        top_documentos=top,
        idioma=idioma,
        n_duplicados_descartados=descartados,
        consultas_sin_top_completo=incompletas,
    )


def _codificar_consultas(
    consultas: list[Consulta],
    codificar: Callable[[list[str]], np.ndarray],
    config: ConfigEncoder,
) -> np.ndarray:
    """Vectores de consulta, normalizados igual que los del índice.

    Se agrupa solo por ``config.lote``, sin el presupuesto de atención de la
    indexación: una consulta son dos líneas, no un fragmento de 450 tokens, y
    el cuadrado de la longitud aquí no llega a apretar.
    """
    textos = [texto_de_consulta(c.texto, config) for c in consultas]
    crudos = np.vstack(
        [codificar(textos[i : i + config.lote]) for i in range(0, len(textos), config.lote)]
    )
    return normalizar(truncar_dimension(crudos, config.dimension))


def filtrar_por_idioma(candidatos: list[Candidato], idioma: str | None) -> list[Candidato]:
    """Post-filtro por idioma (§8.7). Sin ``idioma``, no filtra nada.

    Apagado por defecto a propósito: las consultas vienen en español y la mayor
    parte del corpus está en inglés, así que filtrar a ``es`` no afina la
    respuesta, la vacía. El encoder es multilingüe justamente para no necesitar
    esto. Queda expuesto porque el enunciado lo pide como capacidad.
    """
    if idioma is None:
        return candidatos
    return [c for c in candidatos if c.metadata.get("idioma") == idioma]


def deduplicar_por_texto(candidatos: list[Candidato]) -> tuple[list[Candidato], int]:
    """Quita del top-k los fragmentos repetidos **dentro de un mismo documento**.

    Se queda el primero, que por venir ya ordenado es el de mejor score.

    La clave incluye el ``doc_id`` a propósito, y esto es lo delicado: el corpus
    trae el mismo dataset en dos formatos (lit-covid, ``F1-AIINDEX-041`` en CSV
    y ``F1-AIINDEX-042`` en XLSX) con texto idéntico y vectores idénticos.
    Deduplicar solo por texto parece más limpio y cuesta un acierto: el jurado
    empareja por ``fuente`` (§10.2.1), así que son dos documentos distintos, y
    al descartar uno se le quita el único vector con el que podía llegar al
    top-3. Si la consulta va sobre lit-covid, lo más probable es que los dos
    estén en el ground truth y que compitiendo por separado se lleven dos de
    los tres puestos.

    Dentro de un mismo documento sí es ruido: §8.6 lo puntúa con su mejor
    fragmento, así que el repetido no cambia su score y solo ocupa un puesto
    del top-k que otro documento podría usar.
    """
    vistos: set[tuple[str, str]] = set()
    unicos: list[Candidato] = []
    for candidato in candidatos:
        clave = (
            str(candidato.metadata.get("doc_id")),
            " ".join(str(candidato.metadata.get("texto", "")).split()).casefold(),
        )
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(candidato)
    return unicos, len(candidatos) - len(unicos)


def agregar_por_documento(
    candidatos: list[Candidato], top: int = TOP_DOCUMENTOS
) -> list[Recuperado]:
    """Del top-k de fragmentos al top-N de documentos (§8.6).

    El score del documento es el de su **mejor** fragmento, no la suma de los
    suyos: sumar premia al documento largo por ser largo —más fragmentos, más
    ocasiones de rozar la consulta— cuando lo que se evalúa es si el documento
    responde.

    El desempate por ``doc_id`` no es cosmético: dos documentos con el mismo
    score tienen que salir siempre en el mismo orden o la corrida deja de ser
    reproducible (§1.4).
    """
    mejor: dict[str, Candidato] = {}
    conteo: dict[str, int] = {}

    for candidato in candidatos:
        doc_id = str(candidato.metadata.get("doc_id"))
        conteo[doc_id] = conteo.get(doc_id, 0) + 1
        anterior = mejor.get(doc_id)
        if anterior is None or candidato.score > anterior.score:
            mejor[doc_id] = candidato

    ordenados = sorted(mejor.items(), key=lambda par: (-par[1].score, par[0]))
    return [
        Recuperado(
            doc_id=doc_id,
            score=candidato.score,
            n_fragmentos=conteo[doc_id],
            metadata=candidato.metadata,
        )
        for doc_id, candidato in ordenados[:top]
    ]


def registro_de_resultado(consulta: Consulta, documentos: list[Recuperado]) -> dict[str, Any]:
    """Una línea de ``resultados.jsonl``, con los campos de la Tabla 2.

    **Este es el único sitio que hay que tocar si ADL fija otros nombres de
    campo.** Todo lo de arriba trabaja con ``Recuperado``, no con el JSON.

    Cada documento va con el fragmento que lo metió en el top-3: es la
    evidencia de por qué está ahí, y sin ella el jurado tiene un ``doc_id`` y
    nada con que comprobarlo.
    """
    return {
        "query_id": consulta.id,
        "consulta": consulta.texto,
        "documentos": [
            {
                "puesto": puesto,
                "doc_id": documento.doc_id,
                "fuente": documento.metadata.get("fuente"),
                "ruta_relativa": documento.metadata.get("ruta_relativa"),
                "fenomeno": documento.metadata.get("fenomeno"),
                "observatorio": documento.metadata.get("observatorio"),
                "score": round(documento.score, 6),
                "n_fragmentos": documento.n_fragmentos,
                "chunk_id": documento.metadata.get("chunk_id"),
                "pagina": documento.metadata.get("pagina"),
                "texto": documento.metadata.get("texto"),
            }
            for puesto, documento in enumerate(documentos, start=1)
        ],
    }


# --- lectura del índice ----------------------------------------------------------


def _desplazamientos_de_lineas(ruta: Path) -> list[int]:
    """Byte de arranque de cada línea de ``metadata.jsonl``.

    El archivo son 200 MB para el corpus completo: cargarlo entero a dicts
    costaría del orden de un giga de RAM para leer, de cada consulta, cincuenta
    líneas. Con los desplazamientos se lee solo lo que el índice devuelve, y lo
    que queda en memoria son 140k enteros.
    """
    desplazamientos: list[int] = []
    posicion = 0
    with ruta.open("rb") as archivo:
        for linea in archivo:
            desplazamientos.append(posicion)
            posicion += len(linea)
    return desplazamientos


def _metadata_en(archivo: Any, desplazamiento: int) -> dict[str, Any]:
    archivo.seek(desplazamiento)
    return json.loads(archivo.readline().decode("utf-8"))


# --- lectura de las consultas ----------------------------------------------------


# ADL numera las consultas ``q001``, ``q002``… y el identificador es suyo: el
# entregable se casa con el de ellos, así que se lee, no se inventa.
_MARCA_CONSULTA = re.compile(r"(?im)^[^\S\n]*(q\s?\d{1,4})[\s.:)\-]+")

_CLAVES_ID = ("query_id", "id", "consulta_id", "id_consulta")
_CLAVES_TEXTO = ("consulta", "texto", "pregunta", "query", "text")


def cargar_consultas(ruta: Path) -> list[Consulta]:
    """Las consultas de ADL, vengan como vengan.

    Se acepta el PDF tal cual lo entregan (``Extracto_Preguntas_50_v2.pdf``),
    un JSONL, o un texto plano con una consulta por línea. El formato del día
    de la prueba no está prometido, y quedarse esperando al que sea es perder
    la corrida entera por un parser.
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ValueError(f"no existe el archivo de consultas: {ruta}")

    if ruta.suffix.lower() == ".jsonl":
        return _consultas_de_jsonl(ruta)

    texto = (
        _texto_de_pdf(ruta)
        if ruta.suffix.lower() == ".pdf"
        else ruta.read_text(encoding="utf-8")
    )
    consultas = _consultas_de_texto(texto)
    if not consultas:
        raise ValueError(f"{ruta} no trae ninguna consulta reconocible")
    return consultas


def _texto_de_pdf(ruta: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(ruta)) as pdf:
        return "\n".join(pagina.extract_text() or "" for pagina in pdf.pages)


def _consultas_de_texto(texto: str) -> list[Consulta]:
    """Parte el texto por las marcas ``q001``.

    Las consultas del PDF vienen partidas en varias líneas, así que el corte lo
    marca el identificador siguiente y no el salto de línea: todo lo que hay
    entre dos marcas es el cuerpo de la primera.

    Sin marcas, se cae a una consulta por línea con identificadores generados,
    que es lo que hace falta para probar con un ``.txt`` a mano.
    """
    partes = _MARCA_CONSULTA.split(texto)
    if len(partes) < 3:
        return [
            Consulta(id=f"q{numero:03d}", texto=" ".join(linea.split()))
            for numero, linea in enumerate(
                (l for l in texto.splitlines() if l.strip()), start=1
            )
        ]

    consultas: list[Consulta] = []
    vistos: set[str] = set()
    # partes[0] es lo que precede a la primera marca (cabeceras, título); de ahí
    # en adelante van en pares (identificador, cuerpo).
    for identificador, cuerpo in zip(partes[1::2], partes[2::2]):
        identificador = identificador.replace(" ", "").lower()
        if identificador in vistos:
            raise ValueError(
                f"la consulta {identificador} aparece dos veces: con el "
                f"identificador duplicado, resultados.jsonl saldría con dos "
                f"líneas para la misma y sin saber cuál vale"
            )
        vistos.add(identificador)
        consultas.append(Consulta(id=identificador, texto=" ".join(cuerpo.split())))
    return consultas


def _consultas_de_jsonl(ruta: Path) -> list[Consulta]:
    consultas: list[Consulta] = []
    with ruta.open(encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            linea = linea.strip()
            if not linea:
                continue
            registro = json.loads(linea)
            identificador = _primera_clave(registro, _CLAVES_ID)
            texto = _primera_clave(registro, _CLAVES_TEXTO)
            if texto is None:
                raise ValueError(
                    f"{ruta}:{numero} no trae el texto de la consulta "
                    f"(se buscó en {', '.join(_CLAVES_TEXTO)})"
                )
            consultas.append(
                Consulta(
                    id=str(identificador if identificador is not None else f"q{numero:03d}"),
                    texto=" ".join(str(texto).split()),
                )
            )
    return consultas


def _primera_clave(registro: dict[str, Any], claves: tuple[str, ...]) -> Any:
    for clave in claves:
        if registro.get(clave) is not None:
            return registro[clave]
    return None


# --- encoder por defecto ---------------------------------------------------------


def _codificador(config: ConfigEncoder) -> Callable[[list[str]], np.ndarray]:
    from encoder import codificar_textos

    return lambda textos: codificar_textos(textos, config)


def _contador(config: ConfigEncoder) -> Callable[[str], int]:
    from encoder import contador_de_tokens

    return contador_de_tokens(config)


# --- interfaz de línea de comandos -----------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--entrada",
        type=Path,
        help="directorio de fragmentos o el propio fragmentos.jsonl; construye el índice",
    )
    parser.add_argument("--salida", type=Path, help="directorio del índice a construir")
    parser.add_argument(
        "--indice",
        type=Path,
        help=(
            "directorio de un índice ya construido, para responder sin "
            "reconstruirlo. Con --entrada se responde contra el recién hecho"
        ),
    )
    parser.add_argument(
        "--consultas",
        type=Path,
        help="archivo de consultas: .pdf de ADL, .jsonl o texto con una por línea",
    )
    parser.add_argument(
        "--resultados",
        type=Path,
        help=f"ruta del entregable; por defecto {NOMBRE_RESULTADOS} junto al índice",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=K_FRAGMENTOS,
        help="fragmentos que se piden al índice antes de agregar a documento",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=TOP_DOCUMENTOS,
        help="documentos por consulta en el entregable (§8.6)",
    )
    parser.add_argument(
        "--idioma",
        default=None,
        help="post-filtro por idioma (§8.7); sin él no se filtra, que es lo que conviene",
    )
    parser.add_argument("--modelo", default=CONFIG_POR_DEFECTO.modelo)
    parser.add_argument(
        "--desarrollo",
        action="store_true",
        help=f"usa {NOMBRE_MODELO_DESARROLLO}; para iterar, no para la entrega",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=CONFIG_POR_DEFECTO.dimension,
        help="dimensión de salida; truncar activa Matryoshka",
    )
    parser.add_argument("--lote", type=int, default=CONFIG_POR_DEFECTO.lote)
    parser.add_argument(
        "--presupuesto-atencion",
        type=int,
        default=CONFIG_POR_DEFECTO.presupuesto_atencion,
        help=(
            "tope de lote x longitud² por pasada. Bájalo si la GPU se queda "
            "sin memoria; el valor por defecto está calculado para 6 GB"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _construir_parser()
    args = parser.parse_args(argv)

    if args.entrada is None and args.consultas is None:
        parser.error(
            "hace falta --entrada (construir el índice) o --consultas "
            "(responder contra uno ya construido), o las dos"
        )
    if args.entrada is not None and args.salida is None:
        parser.error("--entrada construye el índice y necesita --salida")

    config = ConfigEncoder(
        modelo=NOMBRE_MODELO_DESARROLLO if args.desarrollo else args.modelo,
        dimension=args.dimension,
        lote=args.lote,
        presupuesto_atencion=args.presupuesto_atencion,
    )

    if args.entrada is not None and not _construir(args, config):
        # Un índice con fragmentos truncados no vale para la entrega, así que
        # tampoco vale para responder: se para aquí en vez de escribir un
        # resultados.jsonl que parecería bueno.
        return 1

    if args.consultas is not None and not _responder(args, config, parser):
        return 1

    if args.desarrollo:
        print(
            f"AVISO: corrida con {NOMBRE_MODELO_DESARROLLO}. "
            f"La entrega se hace con {CONFIG_POR_DEFECTO.modelo}.",
            file=sys.stderr,
        )
    return 0


def _construir(args: argparse.Namespace, config: ConfigEncoder) -> bool:
    reporte = generar_indice(args.entrada, args.salida, config, on_progreso=avisador())

    print(f"{reporte.n_vectores} vectores de {reporte.dimension} dims en {args.salida}")
    print(f"  modelo:        {reporte.modelo} ({reporte.pooling} pooling)", file=sys.stderr)
    print(f"  tokens máx:    {reporte.tokens_max} (p95 {reporte.tokens_p95})", file=sys.stderr)
    print(f"  norma:         [{reporte.norma_min:.6f}, {reporte.norma_max:.6f}]", file=sys.stderr)
    print(f"  por fenómeno:  {reporte.vectores_por_fenomeno}", file=sys.stderr)

    if reporte.n_truncados:
        print(
            f"ERROR: {reporte.n_truncados} fragmentos exceden la ventana de "
            f"{config.ventana_modelo} tokens y se indexaron truncados. El índice "
            f"no vale para la entrega: revisa max_tokens del fragmentador.",
            file=sys.stderr,
        )
        return False
    return True


def _responder(
    args: argparse.Namespace, config: ConfigEncoder, parser: argparse.ArgumentParser
) -> bool:
    directorio = args.indice or args.salida
    if directorio is None:
        parser.error("--consultas necesita --indice (o --entrada/--salida para construirlo)")

    destino = args.resultados or Path(directorio) / NOMBRE_RESULTADOS
    reporte = responder_consultas(
        directorio,
        args.consultas,
        destino,
        config,
        k=args.k,
        top=args.top,
        idioma=args.idioma,
    )

    print(f"{reporte.n_consultas} consultas respondidas en {destino}")
    print(
        f"  índice:        {reporte.n_vectores} vectores, top-{reporte.k_fragmentos} "
        f"por consulta → top-{reporte.top_documentos} documentos",
        file=sys.stderr,
    )
    print(f"  modelo:        {reporte.modelo}", file=sys.stderr)
    if reporte.idioma:
        print(f"  post-filtro:   idioma == {reporte.idioma}", file=sys.stderr)
    if reporte.n_duplicados_descartados:
        print(
            f"  duplicados:    {reporte.n_duplicados_descartados} fragmentos con "
            f"texto repetido descartados del top-k",
            file=sys.stderr,
        )

    if reporte.consultas_sin_top_completo:
        print(
            f"AVISO: {len(reporte.consultas_sin_top_completo)} consultas no "
            f"llegaron a {reporte.top_documentos} documentos distintos "
            f"({', '.join(reporte.consultas_sin_top_completo[:10])}). Sube --k: "
            f"el top-{reporte.k_fragmentos} de fragmentos se agotó en menos "
            f"documentos de los que pide la entrega.",
            file=sys.stderr,
        )
    return True


def avisador() -> Callable[[int, int], None]:
    """Avisa cada ``PROGRESO_CADA`` fragmentos, y siempre al terminar.

    Lleva la cuenta del último aviso en vez de mirar el resto de la división:
    los lotes no caen en múltiplos exactos del umbral, así que con el resto o
    no avisaría nunca o avisaría en cada lote —4 400 líneas de ruido para el
    corpus completo, con el avance real perdido dentro—.
    """
    ultimo = 0

    def avisar(procesados: int, total: int) -> None:
        nonlocal ultimo
        if procesados - ultimo >= PROGRESO_CADA or procesados == total:
            ultimo = procesados
            print(f"  {procesados}/{total} fragmentos codificados", file=sys.stderr)

    return avisar


if __name__ == "__main__":
    raise SystemExit(main())
