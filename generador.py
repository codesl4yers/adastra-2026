"""Construye el índice vectorial y responde las consultas contra él.

Es la etapa que el enunciado exige poder reproducir (§1.4), así que no hay ni un
punto de aleatoriedad: el orden de los vectores es el del archivo de entrada y el
modelo corre en ``eval()`` y float32.

Escribe ``index.faiss`` (``IndexFlatIP``, vectores normalizados),
``metadata.jsonl`` —una línea por vector, en el mismo orden que el índice, sin
``texto_enriquecido``— y ``reporte_indice.json``. La segunda etapa carga ese
índice de disco y escribe el entregable ``resultados.jsonl``, con dos vistas del
mismo top-k: el top-3 de documentos de §8.6 y el top-10 de fragmentos que mide
NDCG@10.

Lo que se codifica es ``texto_enriquecido``, no ``texto``. Las decisiones de esta
capa —lotes, orden, agregación a documento, deduplicación— están en
``docs/decisiones/recuperacion-y-entregable.md`` y en
``docs/decisiones/enriquecimiento-de-contexto.md``.

Uso::

    python generador.py --entrada chunks --salida base_vectorial/encoder_<modelo>
    python generador.py --entrada chunks --salida base_vectorial/encoder_<modelo> --desarrollo

    python generador.py --indice base_vectorial/encoder_<modelo> \\
        --consultas base_documental/Extracto_Preguntas_50_v2.pdf \\
        --resultados resultados.jsonl

Las dos etapas seguidas, si se quiere construir y responder en una sola corrida::

    python generador.py --entrada chunks --salida base_vectorial/encoder_<modelo> \\
        --consultas base_documental/Extracto_Preguntas_50_v2.pdf \\
        --resultados resultados.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
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

NOMBRE_ENTRADA = "chunks.jsonl"
NOMBRE_INDICE = "index.faiss"
NOMBRE_METADATA = "metadata.jsonl"
NOMBRE_REPORTE = "reporte_indice.json"
NOMBRE_RESULTADOS = "resultados.jsonl"

# El top-3 de §8.6: F1@3 se mide exactamente sobre tres.
TOP_DOCUMENTOS = 3

# El 10 de NDCG@10. Con menos, la métrica tiene un techo por construcción.
TOP_FRAGMENTOS = 10

# Fragmentos que se piden a FAISS antes de agregar a documento. Sobra por encima
# de TOP_DOCUMENTOS porque varios fragmentos caen en el mismo doc_id.
K_FRAGMENTOS = 50

CAMPO_A_CODIFICAR = "texto_enriquecido"

# El prefijo de contexto es una decisión del indexador, no un dato del corpus:
# entra al encoder y no a lo que se reporta.
CAMPOS_EXCLUIDOS: tuple[str, ...] = (CAMPO_A_CODIFICAR,)

PROGRESO_CADA = 5_000


@dataclass(frozen=True)
class ReporteIndice:
    """Qué se indexó y con qué. Se serializa tal cual a ``reporte_indice.json``."""

    n_vectores: int
    dimension: int
    modelo: str
    pooling: str
    prefijo_fragmento: str
    n_truncados: int  # debe ser cero: uno solo es un fragmento indexado a medias
    tokens_max: int
    tokens_p95: int
    norma_min: float  # las dos normas deben dar 1,0 ± 1e-5
    norma_max: float
    vectores_por_fenomeno: dict[str, int]
    vectores_por_formato: dict[str, int]

    # Vectores que salieron de la caché en vez de un pase del encoder.
    n_reutilizados: int = 0


def generar_indice(
    entrada: Path,
    salida: Path,
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
    codificar: Callable[[list[str]], np.ndarray] | None = None,
    contar_tokens: Callable[[str], int] | None = None,
    on_progreso: Callable[[int, int], None] | None = None,
) -> ReporteIndice:
    """Codifica los fragmentos de ``entrada`` y escribe el índice en ``salida``.

    ``entrada`` puede ser el ``chunks.jsonl`` o su directorio. ``codificar`` y
    ``contar_tokens`` se inyectan para que las pruebas no descarguen los pesos.
    """
    import faiss

    registros = _cargar_fragmentos(_ruta_de_entrada(entrada))
    codificar = codificar or _codificador(config)
    contar_tokens = contar_tokens or _contador(config)

    indice = faiss.IndexFlatIP(config.dimension)
    total = len(registros)

    # De una vez y antes de codificar: hacen falta para dimensionar los lotes.
    tokens = [
        contar_tokens(texto_de_fragmento(r[CAMPO_A_CODIFICAR], config)) for r in registros
    ]
    truncados = sum(1 for cuenta in tokens if cuenta > config.ventana_modelo)

    # El corpus trae el mismo dataset en dos formatos (lit-covid, CSV y XLSX):
    # cada uno conserva su fila en el índice, pero se codifica una sola vez.
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
    """Lee el JSONL entero a memoria: son ~350 MB y así la metadata se escribe en
    el mismo orden en que se indexó, sin fiarse de que dos recorridos coincidan."""
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
    """Los textos que aparecen más de una vez: solo esos merecen caché.

    Cachear todo costaría más de un giga de RAM para no reutilizar ninguno.
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

    El ahorro está en lo que se le pide al encoder, nunca en lo que se le entrega
    al índice: el lote sale completo y en su orden pase lo que pase.
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
    en ``lote × longitud²``, que es el que de verdad acota la memoria. Un texto
    que por sí solo excede el presupuesto viaja en su propio lote.

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
    """Percentil por rango, sin interpolar: devuelve un conteo real."""
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

    fila: int  # fila del índice, que es la línea de metadata.jsonl
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Recuperado:
    """Un documento del top-3, con el fragmento que lo puso ahí."""

    doc_id: str
    score: float
    n_fragmentos: int  # diagnóstico, no criterio: el puesto lo da el mejor
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
    # Cada una es un F1@3 mermado por construcción: salen nombradas, no contadas.
    consultas_sin_top_completo: list[str]
    top_fragmentos: int = 0
    # Lo mismo para NDCG@10: menos de diez puestos es techo, no mal ranking.
    consultas_sin_fragmentos_completos: list[str] = field(default_factory=list)


def responder_consultas(
    indice: Path,
    consultas: Path | list[Consulta],
    salida: Path,
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
    codificar: Callable[[list[str]], np.ndarray] | None = None,
    k: int = K_FRAGMENTOS,
    top: int = TOP_DOCUMENTOS,
    top_fragmentos: int = TOP_FRAGMENTOS,
    idioma: str | None = None,
) -> ReporteConsultas:
    """Responde las consultas contra el índice de ``indice`` y escribe ``salida``.

    ``indice`` es el directorio que escribió :func:`generar_indice`, o el propio
    ``index.faiss``. Se lee de disco: responder no reconstruye nada.

    Nadie comprueba que el encoder sea el mismo con el que se indexó —el índice no
    guarda de quién es cada vector—, así que responder con otro modelo da
    resultados sin sentido y sin aviso. Por eso el reporte deja escrito cuál fue.
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

    # Si fila y línea no cuadran, todo lo que salga a continuación es metadata de
    # otro fragmento, y con muy buena pinta.
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
    fragmentos_cortos: list[str] = []
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

            fragmentos = mejores_fragmentos(candidatos, top_fragmentos)
            if len(fragmentos) < top_fragmentos:
                fragmentos_cortos.append(consulta.id)

            lineas.append(
                json.dumps(
                    registro_de_resultado(consulta, documentos, fragmentos),
                    ensure_ascii=False,
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
        top_fragmentos=top_fragmentos,
        consultas_sin_fragmentos_completos=fragmentos_cortos,
    )


def _codificar_consultas(
    consultas: list[Consulta],
    codificar: Callable[[list[str]], np.ndarray],
    config: ConfigEncoder,
) -> np.ndarray:
    """Vectores de consulta, normalizados igual que los del índice.

    Se agrupa solo por ``config.lote``, sin presupuesto de atención: una consulta
    son dos líneas y el cuadrado de la longitud aquí no llega a apretar.
    """
    textos = [texto_de_consulta(c.texto, config) for c in consultas]
    crudos = np.vstack(
        [codificar(textos[i : i + config.lote]) for i in range(0, len(textos), config.lote)]
    )
    return normalizar(truncar_dimension(crudos, config.dimension))


def filtrar_por_idioma(candidatos: list[Candidato], idioma: str | None) -> list[Candidato]:
    """Post-filtro por idioma (§8.7). Sin ``idioma``, no filtra nada.

    Apagado por defecto: filtrar a ``es`` no afina la respuesta, la vacía.
    """
    if idioma is None:
        return candidatos
    return [c for c in candidatos if c.metadata.get("idioma") == idioma]


def deduplicar_por_texto(candidatos: list[Candidato]) -> tuple[list[Candidato], int]:
    """Quita del top-k los fragmentos repetidos **dentro de un mismo documento**.

    Se queda el primero, que por venir ordenado es el de mejor score. La clave
    incluye el ``doc_id`` a propósito: deduplicar también entre documentos parece
    más limpio y cuesta aciertos. Ver ``docs/decisiones/recuperacion-y-entregable.md``.
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


def mejores_fragmentos(candidatos: list[Candidato], top: int) -> list[Candidato]:
    """Los ``top`` mejores fragmentos, ya filtrados y deduplicados.

    Es un corte: los candidatos llegan ordenados por score. Tiene función propia
    porque es donde se engancharía un reranking, entre la dedup y el corte.
    """
    if top <= 0:
        return []
    return candidatos[:top]


def agregar_por_documento(
    candidatos: list[Candidato], top: int = TOP_DOCUMENTOS
) -> list[Recuperado]:
    """Del top-k de fragmentos al top-N de documentos (§8.6).

    El score del documento es el de su **mejor** fragmento, no la suma: sumar
    premia al documento largo por ser largo. El desempate por ``doc_id`` no es
    cosmético; sin él, dos corridas del mismo índice ordenarían distinto.
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


def registro_de_resultado(
    consulta: Consulta,
    documentos: list[Recuperado],
    fragmentos: Sequence[Candidato] = (),
) -> dict[str, Any]:
    """Una línea de ``resultados.jsonl``, con los campos de la Tabla 2.

    **Este es el único sitio que hay que tocar si ADL fija otros nombres de
    campo.** ``documentos`` es el top-3 de §8.6, cada uno con el fragmento que lo
    metió ahí; ``fragmentos`` es el top-10 que mide NDCG@10. Sin fragmentos, el
    campo no se escribe.
    """
    registro: dict[str, Any] = {
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

    if fragmentos:
        registro["fragmentos"] = [
            {
                "puesto": puesto,
                "chunk_id": fragmento.metadata.get("chunk_id"),
                "doc_id": fragmento.metadata.get("doc_id"),
                "fuente": fragmento.metadata.get("fuente"),
                "score": round(fragmento.score, 6),
                "pagina": fragmento.metadata.get("pagina"),
                "texto": fragmento.metadata.get("texto"),
            }
            for puesto, fragmento in enumerate(fragmentos, start=1)
        ]

    return registro


# --- lectura del índice ----------------------------------------------------------


def _desplazamientos_de_lineas(ruta: Path) -> list[int]:
    """Byte de arranque de cada línea de ``metadata.jsonl``.

    Son 200 MB: con los desplazamientos se lee solo lo que el índice devuelve y en
    memoria quedan 134k enteros, en vez de un giga de dicts.
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


# ADL numera las consultas q001, q002… El identificador es suyo: se lee, no se
# inventa, porque el entregable se casa con el de ellos.
_MARCA_CONSULTA = re.compile(r"(?im)^[^\S\n]*(q\s?\d{1,4})[\s.:)\-]+")

_CLAVES_ID = ("query_id", "id", "consulta_id", "id_consulta")
_CLAVES_TEXTO = ("consulta", "texto", "pregunta", "query", "text")


def cargar_consultas(ruta: Path) -> list[Consulta]:
    """Las consultas de ADL, vengan como vengan: el PDF tal cual lo entregan, un
    JSONL o un texto plano con una consulta por línea."""
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

    Las del PDF vienen partidas en varias líneas, así que el corte lo marca el
    identificador siguiente y no el salto de línea. Sin marcas, una por línea.
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
    # partes[0] precede a la primera marca; de ahí van en (identificador, cuerpo).
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
        help="directorio de fragmentos o el propio chunks.jsonl; construye el índice",
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
        "--top-fragmentos",
        type=int,
        default=TOP_FRAGMENTOS,
        help="fragmentos por consulta en el entregable (NDCG@10); 0 los apaga",
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
        # Un índice con fragmentos truncados no vale para la entrega: se para
        # aquí en vez de escribir un resultados.jsonl que parecería bueno.
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
        top_fragmentos=args.top_fragmentos,
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

    if reporte.consultas_sin_fragmentos_completos:
        print(
            f"AVISO: {len(reporte.consultas_sin_fragmentos_completos)} consultas "
            f"no llegaron a {reporte.top_fragmentos} fragmentos "
            f"({', '.join(reporte.consultas_sin_fragmentos_completos[:10])}). "
            f"NDCG@{reporte.top_fragmentos} tiene techo en esas.",
            file=sys.stderr,
        )
    return True


def avisador() -> Callable[[int, int], None]:
    """Avisa cada ``PROGRESO_CADA`` fragmentos, y siempre al terminar.

    Lleva la cuenta del último aviso porque los lotes no caen en múltiplos
    exactos del umbral: con el resto de la división, o no avisa nunca o avisa en
    cada lote.
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
