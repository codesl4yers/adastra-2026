"""Pruebas del evaluador de métricas contra el ground truth interno.

Los valores esperados están calculados a mano a propósito: comprobar una
implementación de NDCG contra otra copia de la misma fórmula no prueba nada.
"""

import json

import pytest

from scripts.evaluar import (
    cargar_ground,
    doc_id_de,
    f1_en_k,
    ndcg_en_k,
    techo_f1,
)


def escribir_ground(ruta, consultas):
    """Un ground_truth.json con la forma del real: lista de consultas etiquetadas."""
    ruta.write_text(
        json.dumps(consultas, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return ruta


def consulta_etiquetada(query_id, chunk_ids):
    return {
        "question_id": query_id,
        "question": f"pregunta de {query_id}",
        "relevant_chunks": [
            {
                "rank": posicion,
                "chunk_id": chunk_id,
                "source_file": "archivo.pdf",
                "excerpt": "...",
            }
            for posicion, chunk_id in enumerate(chunk_ids, start=1)
        ],
        "notes": "",
    }


def test_los_tres_aciertos_de_cinco_relevantes_dan_el_techo():
    """3 predichos, 3 aciertos, 5 relevantes: P=1, R=0,6, F1=0,75. Es el techo
    de F1@3 en la mayoría de consultas del ground truth."""
    f1 = f1_en_k(["A", "B", "C"], {"A", "B", "C", "D", "E"}, 3)

    assert f1 == pytest.approx(0.75)


def test_sin_aciertos_el_f1_es_cero():
    assert f1_en_k(["X", "Y", "Z"], {"A", "B"}, 3) == 0.0


def test_un_acierto_de_tres_con_cuatro_relevantes():
    """P=1/3, R=1/4, F1 = 2·(1/3)·(1/4) / (1/3+1/4) = 0,285714."""
    f1 = f1_en_k(["A", "X", "Y"], {"A", "B", "C", "D"}, 3)

    assert f1 == pytest.approx(0.285714, abs=1e-6)


def test_solo_cuentan_los_k_primeros():
    """El acierto en el puesto 4 no entra en F1@3."""
    assert f1_en_k(["X", "Y", "Z", "A"], {"A"}, 3) == 0.0


def test_con_menos_predichos_que_k_no_se_castiga_dos_veces():
    """La precisión es sobre lo entregado, no sobre los puestos vacíos: no
    llenar el top ya se avisa aparte, y contarlo aquí lo penaliza dos veces."""
    assert f1_en_k(["A", "B"], {"A", "B"}, 3) == pytest.approx(1.0)


def test_un_predicho_repetido_no_cuenta_dos_veces():
    """P=2/3 sobre los tres entregados, R=1: F1=0,8."""
    assert f1_en_k(["A", "A", "B"], {"A", "B"}, 3) == pytest.approx(0.8)


def test_sin_relevantes_el_f1_es_cero():
    assert f1_en_k(["A"], set(), 3) == 0.0


# --- NDCG@k ----------------------------------------------------------------------


def test_los_relevantes_en_cabeza_dan_ndcg_uno():
    ganancias = {f"c{n}": 1.0 for n in range(1, 6)}

    assert ndcg_en_k(["c1", "c2", "c3", "c4", "c5"], ganancias, 10) == pytest.approx(1.0)


def test_sin_aciertos_el_ndcg_es_cero():
    assert ndcg_en_k(["x", "y"], {"c1": 1.0}, 10) == 0.0


def test_el_orden_no_cambia_el_binario_pero_si_el_graduado():
    """Los cinco aciertos, del peor al mejor. En binario da 1,0 porque están
    todos; en graduado cae a 0,722243 porque el rank 5 ocupa el puesto 1."""
    binarias = {f"c{n}": 1.0 for n in range(1, 6)}
    graduadas = {"c1": 5.0, "c2": 4.0, "c3": 3.0, "c4": 2.0, "c5": 1.0}
    invertido = ["c5", "c4", "c3", "c2", "c1"]

    assert ndcg_en_k(invertido, binarias, 10) == pytest.approx(1.0)
    assert ndcg_en_k(invertido, graduadas, 10) == pytest.approx(0.722243, abs=1e-6)


def test_un_no_relevante_arriba_consume_su_puesto():
    """No se salta: ocupa el puesto 1 con ganancia 0 y empuja a los buenos.
    DCG = 0/1 + 1/log2(3) + 1/log2(4) = 1,130930; IDCG = 1 + 1/log2(3) =
    1,630930; NDCG = 0,693426."""
    ganancias = {"c1": 1.0, "c2": 1.0}

    assert ndcg_en_k(["x", "c1", "c2"], ganancias, 10) == pytest.approx(
        0.693426, abs=1e-6
    )


def test_solo_cuentan_los_k_primeros_del_ndcg():
    ganancias = {"c1": 1.0}

    assert ndcg_en_k(["x", "y", "z", "c1"], ganancias, 3) == 0.0


def test_sin_ganancias_el_ndcg_es_cero():
    assert ndcg_en_k(["c1"], {}, 10) == 0.0


def test_el_ideal_no_pasa_de_k():
    """Con 5 relevantes y k=3, el ideal son los 3 mejores, no los 5: si no, el
    máximo alcanzable sería inalcanzable y todo NDCG@3 saldría deprimido."""
    ganancias = {f"c{n}": 1.0 for n in range(1, 6)}

    assert ndcg_en_k(["c1", "c2", "c3"], ganancias, 3) == pytest.approx(1.0)


# --- ground truth y techo --------------------------------------------------------


def test_el_doc_id_sale_del_chunk_id():
    assert doc_id_de("F1-CSET-110-c0029") == "F1-CSET-110"


def test_un_chunk_id_con_formato_raro_detiene_la_evaluacion():
    """Derivar mal un doc_id inventa aciertos o los pierde, y ninguna de las dos
    cosas se nota en el número final."""
    with pytest.raises(ValueError, match="inesperado"):
        doc_id_de("F1-CSET-110")


def test_el_ground_agrupa_fragmentos_y_documentos(tmp_path):
    ruta = escribir_ground(
        tmp_path / "ground.json",
        [consulta_etiquetada("q001", ["F1-A-001-c0001", "F1-A-001-c0002", "F1-B-002-c0003"])],
    )

    etiquetadas = cargar_ground(ruta)

    assert len(etiquetadas) == 1
    assert etiquetadas[0].query_id == "q001"
    assert etiquetadas[0].fragmentos == {
        "F1-A-001-c0001": 1,
        "F1-A-001-c0002": 2,
        "F1-B-002-c0003": 3,
    }
    assert etiquetadas[0].documentos == {"F1-A-001", "F1-B-002"}


def test_el_techo_de_tres_sobre_cinco_relevantes():
    """El caso de 28 de las 50 consultas del ground truth real."""
    assert techo_f1(5, 3) == pytest.approx(0.75)


def test_el_techo_de_tres_sobre_cuatro_relevantes():
    assert techo_f1(4, 3) == pytest.approx(0.857142, abs=1e-6)


def test_con_tantos_relevantes_como_puestos_el_techo_es_uno():
    assert techo_f1(3, 3) == pytest.approx(1.0)


def test_con_menos_relevantes_que_puestos_el_techo_baja_por_precision():
    """2 relevantes y 3 puestos: uno sobra por fuerza. P=2/3, R=1, F1=0,8."""
    assert techo_f1(2, 3) == pytest.approx(0.8)
