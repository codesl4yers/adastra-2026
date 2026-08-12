"""Configuración del encoder, carga del modelo y conteo real de tokens.

Modelo: ``ibm-granite/granite-embedding-311m-multilingual-r2`` —ModernBERT,
encoder-only, 768 dims, ventana de 32 768, pooling CLS, Apache 2.0—. Las
constantes de aquí salen de su ``config.json`` y de su tarjeta, y la arquitectura
se verifica contra el checkpoint antes de cargar los pesos.

Es la frontera entre fragmentación e indexación: quien fragmenta para la entrega
le pasa :func:`config_fragmentacion_con_tokenizador` al fragmentador.

Por qué este modelo: ``docs/specs/spec-encoder-addendum.md``. Cómo se codifica y
por qué el lote es pequeño: ``docs/decisiones/recuperacion-y-entregable.md``.

Uso::

    from encoder import config_fragmentacion_con_tokenizador
    from fragmentador import fragmentar_corpus

    fragmentar_corpus(Path("extraidos"), Path("fragmentos"),
                      config_fragmentacion_con_tokenizador())
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import numpy as np

from fragmentador import CONFIG_POR_DEFECTO as CONFIG_FRAGMENTACION_BASE
from fragmentador import ConfigFragmentacion

NOMBRE_MODELO = "ibm-granite/granite-embedding-311m-multilingual-r2"

# Misma familia y mismo tokenizador, para iterar barato. No es el de entrega.
NOMBRE_MODELO_DESARROLLO = "ibm-granite/granite-embedding-97m-multilingual-r2"

DIMENSION = 768
DIMENSIONES_MATRYOSHKA: tuple[int, ...] = (768, 512, 384, 256, 128)
VENTANA_MODELO = 32_768
ARQUITECTURA = "ModernBertModel"
POOLING = "cls"

# Los que el tokenizador añade a cada secuencia: solo <bos>. Medido contra el
# checkpoint; no define cls_token ni sep_token, así que asumir el par CLS/SEP de
# BERT sobreestimaría en uno por fragmento.
TOKENS_ESPECIALES = 1


@dataclass(frozen=True)
class ConfigEncoder:
    """Todo lo que define cómo se convierte texto en vector.

    En un solo objeto para que el informe pueda citar la configuración exacta y
    para que los prefijos viajen con el encoder y no con el chunker.
    """

    modelo: str = NOMBRE_MODELO
    dimension: int = DIMENSION  # por debajo de 768 activa Matryoshka

    # Vacíos para granite: su tarjeta no documenta ninguno, e inventarse uno
    # degrada en silencio tanto como omitir el que sí hace falta.
    prefijo_consulta: str = ""
    prefijo_fragmento: str = ""

    normalizar_salida: bool = True

    # Pequeño a propósito: con poca VRAM el cuello es la memoria y el padding al
    # texto más largo del lote se paga para todos. En una GPU con más memoria el
    # óptimo es mayor: súbelo y mide. Las cifras están en el doc de recuperación.
    lote: int = 4

    # Tope de `lote × longitud²` por pasada, en elementos de máscara: 128 M son
    # ~1 GB. Es el que de verdad acota la memoria, porque el coste de atención de
    # ModernBERT lo fija el texto más largo del lote, no cuántos textos lleve.
    presupuesto_atencion: int = 128_000_000

    semilla: int = 0
    ventana_modelo: int = VENTANA_MODELO

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONES_MATRYOSHKA:
            raise ValueError(
                f"dimensión {self.dimension} fuera de las de Matryoshka "
                f"{DIMENSIONES_MATRYOSHKA}: truncar a cualquier otra no está "
                f"entrenado y degrada sin avisar"
            )

    def contar_tokens_de(self, texto: str) -> int:
        """Tokens que el encoder verá para ``texto``, especiales incluidos."""
        return contar_tokens(texto, self)


CONFIG_POR_DEFECTO = ConfigEncoder()


# --- prefijos asimétricos (§14.1) ------------------------------------------------


def texto_de_consulta(consulta: str, config: ConfigEncoder = CONFIG_POR_DEFECTO) -> str:
    """Consulta lista para codificar."""
    return f"{config.prefijo_consulta}{consulta}"


def texto_de_fragmento(texto: str, config: ConfigEncoder = CONFIG_POR_DEFECTO) -> str:
    """Fragmento listo para codificar. Con granite es la identidad, pero pasa por
    aquí igual para que cambiar de modelo sea cambiar la config."""
    return f"{config.prefijo_fragmento}{texto}"


# --- álgebra de vectores ---------------------------------------------------------


def normalizar(vectores: np.ndarray) -> np.ndarray:
    """Normaliza L2 por filas y devuelve ``float32``, que es lo que come FAISS.

    Lanza si alguna fila tiene norma cero: daría ``NaN`` y FAISS lo aceptaría sin
    quejarse, devolviendo vecinos arbitrarios en cada consulta.
    """
    matriz = np.asarray(vectores, dtype=np.float32)
    if matriz.ndim != 2:
        raise ValueError(f"se esperaba una matriz (n, dim), llegó {matriz.shape}")

    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    nulas = np.flatnonzero(normas[:, 0] == 0)
    if nulas.size:
        raise ValueError(
            f"{nulas.size} vector(es) de norma cero (filas {nulas[:5].tolist()}): "
            f"no tienen dirección y el índice devolvería resultados arbitrarios"
        )

    return (matriz / normas).astype(np.float32, copy=False)


def truncar_dimension(vectores: np.ndarray, dimension: int) -> np.ndarray:
    """Matryoshka: recorta a ``dimension`` y renormaliza. Sin renormalizar, el
    producto interno deja de ser el coseno que ``IndexFlatIP`` supone."""
    matriz = np.asarray(vectores, dtype=np.float32)
    if dimension > matriz.shape[1]:
        raise ValueError(
            f"no se puede truncar a {dimension}: los vectores tienen "
            f"{matriz.shape[1]} dimensiones"
        )
    if dimension == matriz.shape[1]:
        return matriz
    return normalizar(matriz[:, :dimension])


# --- tokenizador -----------------------------------------------------------------


@lru_cache(maxsize=4)
def cargar_tokenizador(modelo: str = NOMBRE_MODELO) -> Any:
    """``AutoTokenizer`` del checkpoint, cacheado por nombre de modelo.

    ``transformers`` se importa aquí dentro para que el fragmentador siga
    funcionando —con su estimación— sin la dependencia instalada.
    """
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(modelo)


def contar_tokens(texto: str, config: ConfigEncoder = CONFIG_POR_DEFECTO) -> int:
    """Tokens reales del encoder, con los especiales: el límite se aplica a lo
    que entra al modelo, no al texto suelto."""
    tokenizador = cargar_tokenizador(config.modelo)
    return len(tokenizador(texto, add_special_tokens=True)["input_ids"])


def contador_de_tokens(
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
) -> Callable[[str], int]:
    """Contador listo para inyectar en ``ConfigFragmentacion.contar_tokens``."""

    def contar(texto: str) -> int:
        return contar_tokens(texto, config)

    return contar


# --- modelo ----------------------------------------------------------------------

# Que un repo se llame "embedding" no dice nada de su arquitectura: harrier,
# Qwen3-Embedding y EmbeddingGemma se distribuyen así y son decoders.
FAMILIAS_ENCODER: frozenset[str] = frozenset(
    {
        "modernbert",
        "bert",
        "roberta",
        "xlm-roberta",
        "xlm_roberta",
        "camembert",
        "deberta",
        "deberta-v2",
        "distilbert",
        "electra",
        "mpnet",
        "nomic_bert",
        "new",  # gte-multilingual-base
    }
)

FAMILIAS_DECODER: frozenset[str] = frozenset(
    {
        "llama",
        "qwen2",
        "qwen3",
        "gemma",
        "gemma2",
        "gemma3",
        "mistral",
        "mixtral",
        "phi",
        "phi3",
        "falcon",
        "gpt2",
        "gpt_neox",
        "gptj",
        "olmo",
        "olmo2",
    }
)


def verificar_arquitectura(modelo: str = NOMBRE_MODELO) -> str:
    """Devuelve el ``model_type`` del checkpoint, o lanza si no es encoder-only.

    Corre antes de cargar los pesos: usar un decoder en la construcción del
    índice es riesgo de descalificación por §4.2, no una zona gris.
    """
    from transformers import AutoConfig

    configuracion = AutoConfig.from_pretrained(modelo)
    tipo = str(getattr(configuracion, "model_type", "")).lower()
    arquitecturas = list(getattr(configuracion, "architectures", None) or [])

    if tipo in FAMILIAS_DECODER:
        raise ValueError(
            f"{modelo} es de arquitectura decoder ({tipo}, {arquitecturas}). "
            f"§4.2 del enunciado prohíbe los decoders en la construcción del "
            f"índice y en la recuperación: usarlo es riesgo de descalificación."
        )
    if tipo not in FAMILIAS_ENCODER:
        raise ValueError(
            f"{modelo} declara model_type {tipo!r} ({arquitecturas}), que no está "
            f"en la lista de familias encoder-only conocidas {sorted(FAMILIAS_ENCODER)}. "
            f"Verifica su config.json a mano antes de usarlo (§11 del addendum)."
        )
    return tipo


@lru_cache(maxsize=2)
def cargar_modelo(modelo: str = NOMBRE_MODELO, semilla: int = 0) -> Any:
    """Carga el encoder en modo evaluación, determinista y en float32.

    float32 y no float16: la media precisión varía entre GPU y rompe la
    reproducibilidad que §1.4 exige.
    """
    import torch
    from sentence_transformers import SentenceTransformer

    verificar_arquitectura(modelo)

    torch.manual_seed(semilla)
    encoder = SentenceTransformer(modelo)
    encoder.eval()
    return encoder


def _es_falta_de_memoria(error: BaseException) -> bool:
    """``torch.OutOfMemoryError`` sin importar torch para averiguarlo: por el
    mensaje, para que el módulo siga siendo importable sin torch."""
    return isinstance(error, RuntimeError) and "out of memory" in str(error).lower()


def codificar_con_respaldo(intento: Callable[[], Any], respaldo: Callable[[], Any]) -> Any:
    """Ejecuta ``intento``; si se queda sin memoria de GPU, usa ``respaldo``.

    Cualquier otro error se propaga: reintentar en CPU algo que falló por otro
    motivo devolvería vectores de una operación rota.
    """
    try:
        return intento()
    except RuntimeError as error:
        if not _es_falta_de_memoria(error):
            raise
        print(
            "AVISO: sin memoria en la GPU para este lote; se reintenta en CPU. "
            "Si se repite mucho, baja --presupuesto-atencion.",
            file=sys.stderr,
        )
        return respaldo()


def codificar_textos(
    textos: list[str], config: ConfigEncoder = CONFIG_POR_DEFECTO
) -> np.ndarray:
    """Vectores crudos del encoder, sin normalizar: normaliza el generador, de
    forma explícita, para no depender de que el checkpoint lo siga haciendo."""
    import torch

    encoder = cargar_modelo(config.modelo, config.semilla)
    textos = list(textos)

    def codificar_en(dispositivo: str | None) -> np.ndarray:
        with torch.inference_mode():
            return encoder.encode(
                textos,
                batch_size=config.lote,
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
                device=dispositivo,
            )

    def en_cpu() -> np.ndarray:
        original = encoder.device
        # El modelo entero baja y vuelve: `device=` por sí solo no mueve los pesos.
        torch.cuda.empty_cache()
        encoder.to("cpu")
        try:
            return codificar_en("cpu")
        finally:
            encoder.to(original)

    vectores = codificar_con_respaldo(lambda: codificar_en(None), en_cpu)
    return np.asarray(vectores, dtype=np.float32)


def config_fragmentacion_con_tokenizador(
    config: ConfigEncoder = CONFIG_POR_DEFECTO,
    base: ConfigFragmentacion = CONFIG_FRAGMENTACION_BASE,
) -> ConfigFragmentacion:
    """La configuración de fragmentación de siempre, con el contador real.

    Cambia solo ``contar_tokens``: los topes y el algoritmo no dependen del
    encoder, así que re-fragmentar es una re-corrida y no un rediseño.
    """
    return replace(base, contar_tokens=contador_de_tokens(config))
