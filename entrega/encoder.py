"""Configuración del encoder y conteo real de tokens.

Modelo elegido: ``ibm-granite/granite-embedding-311m-multilingual-r2``
(§16 del addendum de selección de encoder). Los datos que fija este módulo
salen de su ``config.json`` y de su tarjeta de modelo, no de memoria:

- ``architectures: ["ModernBertModel"]`` — encoder-only, que es la condición
  eliminatoria de §4.2 del enunciado. Ningún decoder interviene en la
  construcción del índice ni en la recuperación.
- 768 dimensiones, truncables por Matryoshka a 512/384/256/128.
- Ventana de 32 768 tokens, ociosa con fragmentos de ≤250 palabras (§13.1).
- Pooling **CLS**, no media. Lo dice la tarjeta literalmente.
- Apache 2.0.
- Sin prefijos de consulta ni de pasaje documentados.

Este módulo es la frontera entre la capa de fragmentación y la de indexación:
:func:`config_fragmentacion_con_tokenizador` es lo que hay que pasarle a
``fragmentador.py`` para que ``num_tokens`` deje de ser una estimación y la
salida valga para la entrega.

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

# El de 97M, misma familia y mismo tokenizador, para iterar sin pagar el coste
# del grande (§12.2 del addendum). No es el modelo de entrega.
NOMBRE_MODELO_DESARROLLO = "ibm-granite/granite-embedding-97m-multilingual-r2"

DIMENSION = 768
"""``hidden_size`` del checkpoint."""

DIMENSIONES_MATRYOSHKA: tuple[int, ...] = (768, 512, 384, 256, 128)
"""Las únicas dimensiones a las que la tarjeta autoriza truncar."""

VENTANA_MODELO = 32_768
"""``max_position_embeddings``. Muy por encima de lo que el chunker produce."""

ARQUITECTURA = "ModernBertModel"
"""Lo que debe declarar ``config.json``. Se verifica en tiempo de carga: es la
comprobación de §11 del addendum, y un checkpoint sustituido en la caché o un
repo renombrado la haría saltar antes de indexar nada."""

POOLING = "cls"
"""Declarado en la tarjeta del modelo. Se documenta aquí porque asumir media
en vez de CLS no rompe nada visible: solo baja el NDCG."""

TOKENS_ESPECIALES = 1
"""Los que el tokenizador añade a cada secuencia: solo ``<bos>``.

Medido contra el checkpoint, no supuesto: este tokenizador **no** define
``cls_token`` ni ``sep_token``, así que el "CLS pooling" de la tarjeta es la
primera posición de la secuencia, que es ese ``<bos>``. Un conteo que asuma el
par CLS/SEP de BERT sobreestima en uno cada fragmento."""


@dataclass(frozen=True)
class ConfigEncoder:
    """Todo lo que define cómo se convierte texto en vector.

    Frozen y en un solo objeto por la misma razón que ``ConfigFragmentacion``:
    el informe técnico tiene que poder citar la configuración exacta, y los
    prefijos tienen que viajar con el encoder y no con el chunker (§14.1).
    """

    modelo: str = NOMBRE_MODELO
    dimension: int = DIMENSION
    """Dimensión de salida. Truncar por debajo de 768 activa Matryoshka."""

    prefijo_consulta: str = ""
    """Vacío para granite: su tarjeta no documenta ninguno. Inventarse uno
    degrada en silencio tanto como omitir el que sí hace falta."""

    prefijo_fragmento: str = ""

    normalizar_salida: bool = True
    """§5.2 del enunciado exige ``IndexFlatIP`` con vectores normalizados para
    que el producto interno sea el coseno. Se normaliza siempre y de forma
    explícita, aunque el modelo ya lo haga en su capa de salida."""

    lote: int = 4
    """Tope de textos por lote. **No basta para acotar la memoria**: ver
    :attr:`presupuesto_atencion`.

    Pequeño a propósito, en contra de la intuición de que más lote es más
    rendimiento. Medido sobre una muestra sistemática del corpus en una RTX
    4050 de 6 GB (mediana 247 tokens, p95 426):

    ====== ============ ============== ==========
    lote   fragmentos/s corpus completo VRAM pico
    ====== ============ ============== ==========
    2      26,2         93 min          2,68 GB
    4      23,6         104 min         2,82 GB
    8      19,2         128 min         3,10 GB
    16     14,7         167 min         3,66 GB
    32     9,6          255 min         4,78 GB
    ====== ============ ============== ==========

    La causa es que la máscara de atención de ModernBERT se dimensiona con el
    texto **más largo** del lote y se paga para todos: agrupar de 32 en 32
    rellena de padding y multiplica una matriz L×L por 32. En una tarjeta con
    poca VRAM el cuello es la memoria, no el cómputo, y a partir de cierto
    punto el driver empieza a volcar a memoria del sistema y el rendimiento se
    desploma. En una GPU con más VRAM el óptimo será mayor: ajústalo con
    ``--lote`` y mide, no lo supongas."""

    presupuesto_atencion: int = 128_000_000
    """Tope de ``lote × longitud²`` por pasada, en elementos de máscara.

    ModernBERT materializa una máscara de atención de ``(lote, 1, L, L)`` en
    float32 —dos, en realidad: global y sliding window—, con ``L`` la longitud
    del texto **más largo** del lote. El coste crece con el cuadrado de ``L`` y
    multiplicado por el lote entero, así que un solo fragmento largo arrastra a
    todos sus compañeros: 32 textos con uno de 8 200 tokens piden 8,67 GB de
    una sentada y tumban una GPU de 6 GB. Con este presupuesto, los lotes se
    encogen cuando aparece un fragmento largo y el más grande de todos viaja
    solo. 128 M elementos son ~1 GB de máscaras (2 × 128 M × 4 bytes)."""

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
    """Fragmento listo para codificar. Con granite es la identidad, pero pasa
    por aquí igual para que cambiar de modelo sea cambiar la config."""
    return f"{config.prefijo_fragmento}{texto}"


# --- álgebra de vectores ---------------------------------------------------------


def normalizar(vectores: np.ndarray) -> np.ndarray:
    """Normaliza L2 por filas y devuelve ``float32``, que es lo que come FAISS.

    Lanza si alguna fila tiene norma cero: dividir daría ``NaN``, FAISS lo
    aceptaría sin quejarse y ese vector devolvería vecinos arbitrarios en cada
    consulta. Es exactamente el tipo de fallo silencioso de §14.
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
    """Matryoshka: recorta a ``dimension`` y **renormaliza** (§14.5).

    Sin la renormalización el producto interno deja de ser el coseno, que es
    justo lo que ``IndexFlatIP`` da por supuesto.
    """
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

    Cacheado porque el fragmentador lo llama una vez por fragmento: recargarlo
    costaría más que fragmentar. ``transformers`` se importa aquí dentro y no
    arriba para que ``fragmentador.py`` siga funcionando —con su estimación—
    en un entorno sin la dependencia instalada.
    """
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(modelo)


def contar_tokens(texto: str, config: ConfigEncoder = CONFIG_POR_DEFECTO) -> int:
    """Tokens reales del encoder, **con los especiales**.

    Los especiales cuentan: el límite de la secuencia se aplica a lo que entra
    al modelo, no al texto suelto. Contar sin ellos deja pasar fragmentos que
    el tokenizador luego trunca en silencio (§14.3).
    """
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

# §11 del addendum: la arquitectura es la condición eliminatoria, y se comprueba
# contra el ``config.json`` del checkpoint. Que un repo se llame "embedding" no
# dice nada: harrier, Qwen3-Embedding y EmbeddingGemma se distribuyen como
# modelos de embedding y son decoders.
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

    Es la comprobación de §11 del addendum, y corre antes de cargar los pesos:
    usar un decoder en la construcción del índice es riesgo de descalificación
    (§4.2 del enunciado), no una zona gris, así que el pipeline se detiene en
    vez de indexar algo inservible.
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

    float32 y no float16 a propósito (§14.4): la inferencia en media precisión
    introduce variación entre GPUs y rompe la reproducibilidad que §1.4 del
    enunciado exige para esta etapa. Un índice que no se puede reproducir no se
    puede auditar.
    """
    import torch
    from sentence_transformers import SentenceTransformer

    verificar_arquitectura(modelo)

    torch.manual_seed(semilla)
    encoder = SentenceTransformer(modelo)
    encoder.eval()
    return encoder


def _es_falta_de_memoria(error: BaseException) -> bool:
    """``torch.OutOfMemoryError`` sin importar torch para averiguarlo.

    Es subclase de ``RuntimeError`` y su mensaje siempre trae "out of memory".
    Reconocerlo por el mensaje mantiene este módulo importable sin torch, que
    es lo que permite que el fragmentador dependa de él solo para el conteo.
    """
    return isinstance(error, RuntimeError) and "out of memory" in str(error).lower()


def codificar_con_respaldo(intento: Callable[[], Any], respaldo: Callable[[], Any]) -> Any:
    """Ejecuta ``intento``; si se queda sin memoria de GPU, usa ``respaldo``.

    Red de seguridad para los pocos fragmentos que no caben ni solos en la
    tarjeta —57 por encima de 8 192 tokens en este corpus—. Perder una corrida
    de horas por ellos no tiene sentido, y truncarlos los indexaría a medias
    (§14.3). Cualquier otro error se propaga: reintentar en CPU algo que falló
    por otro motivo devolvería vectores de una operación rota.
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
    """Vectores crudos del encoder, sin normalizar.

    Sin normalizar a propósito: la normalización la hace el generador de forma
    explícita y verificable (§14.2). Que el modelo ya normalice en su capa de
    salida es un detalle del checkpoint, y el índice no puede depender de un
    detalle que un cambio de versión puede quitar sin avisar.
    """
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
        # El modelo entero baja a CPU y vuelve: `device=` por sí solo no mueve
        # los pesos, y dejarlo en CPU penalizaría todos los lotes siguientes.
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

    Cambia **solo** ``contar_tokens``: los topes de palabras y el algoritmo no
    dependen del encoder (§13.1), así que la re-fragmentación al fijar el
    modelo es una re-corrida, no un rediseño.
    """
    return replace(base, contar_tokens=contador_de_tokens(config))
