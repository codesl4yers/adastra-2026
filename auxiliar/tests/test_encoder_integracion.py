"""Pruebas contra el checkpoint real de granite.

Se saltan solas si el modelo no está descargado: pesa 1,2 GB y la suite tiene
que poder correr en cualquier máquina. Para ejecutarlas hay que traerlo antes::

    python -c "from encoder import cargar_modelo; cargar_modelo()"
    pytest tests/test_encoder_integracion.py

Cubren la Fase 1 del protocolo de evaluación (§15.1 del addendum): arquitectura,
truncamiento cero y sanidad cruzada entre idiomas.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from encoder import CONFIG_POR_DEFECTO, NOMBRE_MODELO

pytestmark = pytest.mark.integracion


@pytest.fixture(scope="module")
def modelo():
    from encoder import cargar_modelo

    try:
        return cargar_modelo()
    except Exception as error:  # noqa: BLE001 - sin red, sin caché o sin dependencia
        pytest.skip(f"checkpoint de granite no disponible: {error}")


# --- §11: la condición eliminatoria ----------------------------------------------


def test_el_checkpoint_declara_una_arquitectura_encoder_only():
    """§4.2 del enunciado prohíbe los decoders en la construcción del índice.
    Se comprueba contra el config.json, no contra la palabra 'embedding' del
    nombre del repo."""
    from encoder import verificar_arquitectura

    assert verificar_arquitectura(NOMBRE_MODELO) == "modernbert"


def test_un_checkpoint_decoder_se_rechaza():
    """La regla operativa de §11: si aparece Qwen, Llama o Gemma, se descarta
    sin discusión. Se prueba con un checkpoint minúsculo de arquitectura
    decoder para no descargar 8 GB."""
    from encoder import verificar_arquitectura

    try:
        with pytest.raises(ValueError, match="decoder"):
            verificar_arquitectura("hf-internal-testing/tiny-random-LlamaForCausalLM")
    except OSError as error:
        pytest.skip(f"sin red para traer el config.json de prueba: {error}")


# --- codificación ----------------------------------------------------------------


def test_codificar_devuelve_la_dimension_declarada(modelo):
    from encoder import codificar_textos

    vectores = codificar_textos(["informe anual del observatorio"])

    assert vectores.shape == (1, 768)
    assert vectores.dtype == np.float32


def test_codificar_dos_veces_da_el_mismo_vector(modelo):
    """§1.4 del enunciado: el jurado tiene que poder reproducir el índice."""
    from encoder import codificar_textos

    textos = ["la red de sensores cubre el arco sur", "lanzamientos orbitales"]

    assert np.array_equal(codificar_textos(textos), codificar_textos(textos))


def test_el_orden_del_lote_no_cambia_el_vector(modelo):
    """Si el vector dependiera del lote, el índice cambiaría con --lote y la
    reproducibilidad sería una ilusión."""
    from encoder import codificar_textos

    a, b = "informe anual del observatorio", "vigilancia del entorno espacial"
    juntos = codificar_textos([a, b])
    sueltos = np.vstack([codificar_textos([a]), codificar_textos([b])])

    assert np.allclose(juntos, sueltos, atol=1e-5)


# --- §15.1: sanidad cruzada entre idiomas ----------------------------------------


def test_una_consulta_en_espanol_recupera_su_fragmento_en_ingles(modelo):
    """El corpus es es/en/pt y las consultas del jurado irán en español. Si el
    modelo no cruza idiomas, no sirve para este corpus por mucho MTEB que tenga."""
    from encoder import codificar_textos, normalizar

    consulta = "¿cuántos satélites lanzó la región el año pasado?"
    equivalente = "The region launched fourteen satellites into orbit last year."
    irrelevante = "The committee approved the minutes of the previous meeting."

    vectores = normalizar(codificar_textos([consulta, equivalente, irrelevante]))
    similitud_equivalente = float(vectores[0] @ vectores[1])
    similitud_irrelevante = float(vectores[0] @ vectores[2])

    assert similitud_equivalente > similitud_irrelevante


# --- §14.3: truncamiento cero sobre el corpus real -------------------------------


def test_ningun_fragmento_del_corpus_real_excede_la_ventana(raiz_proyecto):
    """El truncamiento es silencioso: el vector sale igual y el fragmento queda
    indexado a medias. Se mide sobre la salida real del fragmentador."""
    from encoder import contar_tokens

    ruta = raiz_proyecto / "chunks" / "chunks.jsonl"
    if not ruta.is_file():
        pytest.skip("no hay chunks/chunks.jsonl; corre antes el fragmentador")

    excedidos = 0
    with ruta.open(encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo):
            if numero >= 2000:
                break
            registro = json.loads(linea)
            if contar_tokens(registro["texto_enriquecido"]) > CONFIG_POR_DEFECTO.ventana_modelo:
                excedidos += 1

    assert excedidos == 0
