"""Pruebas de la configuración del encoder.

Las que necesitan el checkpoint real de HuggingFace se saltan solas si no está
descargado: la suite tiene que correr en una máquina sin red.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from encoder import (
    CONFIG_POR_DEFECTO,
    DIMENSIONES_MATRYOSHKA,
    NOMBRE_MODELO,
    ConfigEncoder,
    normalizar,
    texto_de_consulta,
    texto_de_fragmento,
    truncar_dimension,
)

# --- configuración --------------------------------------------------------------


def test_el_modelo_por_defecto_es_el_granite_multilingue():
    assert CONFIG_POR_DEFECTO.modelo == NOMBRE_MODELO
    assert CONFIG_POR_DEFECTO.dimension == 768


def test_una_dimension_fuera_de_matryoshka_se_rechaza():
    with pytest.raises(ValueError, match="dimensión"):
        ConfigEncoder(dimension=700)


def test_las_dimensiones_de_matryoshka_se_aceptan():
    for dimension in DIMENSIONES_MATRYOSHKA:
        assert ConfigEncoder(dimension=dimension).dimension == dimension


# --- prefijos asimétricos (§14.1 del addendum) -----------------------------------


def test_granite_no_pide_prefijos():
    """La tarjeta del modelo no documenta ninguno. Añadir uno inventado
    degrada en silencio, así que por defecto el texto viaja intacto."""
    assert texto_de_consulta("qué es el ciclo lunar", CONFIG_POR_DEFECTO) == (
        "qué es el ciclo lunar"
    )
    assert texto_de_fragmento("el ciclo lunar dura 29 días", CONFIG_POR_DEFECTO) == (
        "el ciclo lunar dura 29 días"
    )


def test_con_un_modelo_asimetrico_la_consulta_y_el_fragmento_reciben_prefijos_distintos():
    """§14.1: el prefijo viaja con el encoder, no con el chunker. Si algún día
    se cambia a multilingual-e5, esta es la prueba que evita la caída silenciosa."""
    config = ConfigEncoder(
        modelo="intfloat/multilingual-e5-large",
        dimension=768,
        prefijo_consulta="query: ",
        prefijo_fragmento="passage: ",
    )

    assert texto_de_consulta("ciclo lunar", config) == "query: ciclo lunar"
    assert texto_de_fragmento("ciclo lunar", config) == "passage: ciclo lunar"


# --- normalización (§14.2 del addendum) ------------------------------------------


def test_normalizar_deja_la_norma_en_uno():
    vectores = np.array([[3.0, 4.0], [1.0, 0.0], [-2.0, -2.0]], dtype=np.float32)

    normas = np.linalg.norm(normalizar(vectores), axis=1)

    assert np.allclose(normas, 1.0, atol=1e-5)


def test_normalizar_conserva_la_direccion():
    vectores = np.array([[3.0, 4.0]], dtype=np.float32)

    assert np.allclose(normalizar(vectores), [[0.6, 0.8]], atol=1e-6)


def test_normalizar_no_toca_el_arreglo_de_entrada():
    vectores = np.array([[3.0, 4.0]], dtype=np.float32)

    normalizar(vectores)

    assert np.array_equal(vectores, [[3.0, 4.0]])


def test_normalizar_un_vector_nulo_es_un_error():
    """Un vector de norma cero no tiene dirección: normalizarlo da NaN y el
    índice devuelve resultados sin sentido sin avisar."""
    vectores = np.array([[1.0, 1.0], [0.0, 0.0]], dtype=np.float32)

    with pytest.raises(ValueError, match="norma cero"):
        normalizar(vectores)


def test_normalizar_devuelve_float32():
    """FAISS exige float32; un float64 se convierte con copia silenciosa."""
    vectores = np.array([[3.0, 4.0]], dtype=np.float64)

    assert normalizar(vectores).dtype == np.float32


# --- Matryoshka (§14.5 del addendum) ---------------------------------------------


def test_truncar_la_dimension_renormaliza():
    """Truncar rompe la norma unitaria; sin renormalizar el producto interno
    deja de ser el coseno."""
    vectores = normalizar(np.arange(16, dtype=np.float32).reshape(2, 8))

    truncados = truncar_dimension(vectores, 4)

    assert truncados.shape == (2, 4)
    assert np.allclose(np.linalg.norm(truncados, axis=1), 1.0, atol=1e-5)


def test_truncar_a_la_dimension_completa_no_cambia_nada():
    vectores = normalizar(np.arange(16, dtype=np.float32).reshape(2, 8))

    assert np.array_equal(truncar_dimension(vectores, 8), vectores)


def test_truncar_por_encima_de_la_dimension_disponible_es_un_error():
    vectores = normalizar(np.arange(16, dtype=np.float32).reshape(2, 8))

    with pytest.raises(ValueError, match="16"):
        truncar_dimension(vectores, 16)


# --- respaldo en CPU cuando la GPU se queda sin memoria --------------------------


def test_un_lote_que_no_cabe_en_la_gpu_se_reintenta_en_cpu(capsys):
    """Perder una corrida de horas por los 57 fragmentos que no caben sería
    absurdo: se codifican en CPU, que es lento pero termina."""
    from encoder import codificar_con_respaldo

    def intento():
        raise RuntimeError("CUDA out of memory. Tried to allocate 8.67 GiB")

    resultado = codificar_con_respaldo(intento, lambda: "vectores en cpu")

    assert resultado == "vectores en cpu"
    assert "CPU" in capsys.readouterr().err


def test_el_respaldo_no_se_traga_otros_errores():
    """Un error que no sea de memoria tiene que salir: reintentarlo en CPU solo
    lo escondería y devolvería vectores de algo que falló."""
    from encoder import codificar_con_respaldo

    def intento():
        raise RuntimeError("el tensor tiene la forma equivocada")

    with pytest.raises(RuntimeError, match="forma equivocada"):
        codificar_con_respaldo(intento, lambda: "no se debería llegar aquí")


def test_sin_error_no_se_usa_el_respaldo():
    from encoder import codificar_con_respaldo

    assert codificar_con_respaldo(lambda: "en gpu", lambda: "en cpu") == "en gpu"


# --- conteo de tokens: necesita el tokenizador real ------------------------------


@pytest.fixture(scope="module")
def tokenizador():
    """Se salta si el checkpoint no está en la caché local y no hay red."""
    encoder = pytest.importorskip("encoder")
    try:
        return encoder.cargar_tokenizador()
    except Exception as error:  # noqa: BLE001 - falta transformers, red o caché
        pytest.skip(f"tokenizador de granite no disponible: {error}")


def test_el_conteo_incluye_los_tokens_especiales(tokenizador):
    """El límite del encoder se aplica a la secuencia completa: si el conteo
    ignora los especiales, un fragmento al borde se trunca en el encoder.

    Granite añade **uno solo**, ``<bos>``, y no tiene ``cls_token`` definido:
    el "CLS pooling" de su tarjeta es la primera posición de la secuencia, que
    es justamente ese ``<bos>``. Medido contra el checkpoint, no supuesto.
    """
    from encoder import TOKENS_ESPECIALES, contar_tokens

    sin_especiales = len(tokenizador("hola", add_special_tokens=False)["input_ids"])

    assert TOKENS_ESPECIALES == 1
    assert contar_tokens("hola") == sin_especiales + TOKENS_ESPECIALES


def test_el_conteo_real_es_menor_que_la_estimacion_conservadora(tokenizador):
    """`estimar_tokens` sobreestima a propósito (§6.2). Comprobarlo sobre
    prosa española real es lo que justifica que la re-fragmentación no pueda
    producir fragmentos truncados."""
    from fragmentador import estimar_tokens

    from encoder import contar_tokens

    texto = (
        "El observatorio publicó un informe sobre las capacidades espaciales "
        "de la región durante el año dos mil veinticinco, con especial "
        "atención a los lanzamientos orbitales y a la vigilancia del entorno."
    )

    assert contar_tokens(texto) < estimar_tokens(texto)


def test_el_contador_inyectable_cuenta_igual_que_la_funcion(tokenizador):
    from encoder import contador_de_tokens, contar_tokens

    contador = contador_de_tokens()

    assert contador("informe anual") == contar_tokens("informe anual")


def test_el_texto_vacio_cuenta_solo_los_especiales(tokenizador):
    from encoder import TOKENS_ESPECIALES, contar_tokens

    assert contar_tokens("") == TOKENS_ESPECIALES


def test_la_configuracion_de_fragmentacion_usa_el_tokenizador_real(tokenizador):
    """El puente entre las dos capas: es lo que hay que pasarle al fragmentador
    para que la re-corrida del corpus sea válida para la entrega."""
    from encoder import config_fragmentacion_con_tokenizador

    config = config_fragmentacion_con_tokenizador()

    texto = "un fragmento cualquiera del corpus"
    assert config.contar_tokens(texto) == CONFIG_POR_DEFECTO.contar_tokens_de(texto)
    assert config.max_tokens == 450


def test_ningun_fragmento_cabe_por_debajo_de_su_cuenta_de_palabras(tokenizador):
    """Sanidad del tokenizador multilingüe: 262k de vocabulario no puede dar
    menos tokens que palabras en texto natural con puntuación."""
    from encoder import contar_tokens

    texto = "Bogotá, Brasilia y Buenos Aires firmaron el acuerdo."

    assert contar_tokens(texto) >= len(texto.split())


def test_la_ventana_del_modelo_supera_con_holgura_el_tope_del_chunker():
    """§13.1: el contexto largo está ocioso. Documentado como prueba para que
    nadie 'aproveche' la ventana subiendo max_tokens sin medir NDCG."""
    from fragmentador import CONFIG_POR_DEFECTO as CONFIG_CHUNKER

    assert CONFIG_POR_DEFECTO.ventana_modelo > CONFIG_CHUNKER.max_tokens
    assert math.isclose(CONFIG_POR_DEFECTO.ventana_modelo, 32768)
