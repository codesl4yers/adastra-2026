"""Pruebas del evaluador de métricas contra el ground truth interno.

Los valores esperados están calculados a mano a propósito: comprobar una
implementación de NDCG contra otra copia de la misma fórmula no prueba nada.
"""

import json

import pytest

from scripts.evaluar import (
    cargar_ground,
    cargar_resultados,
    evaluar,
    main,
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


# --- el script completo ----------------------------------------------------------


def escribir_resultados(ruta, registros):
    ruta.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros),
        encoding="utf-8",
        newline="\n",
    )
    return ruta


def registro(query_id, doc_ids, chunk_ids=None):
    linea = {
        "query_id": query_id,
        "consulta": "da igual",
        "documentos": [
            {"puesto": n, "doc_id": doc_id} for n, doc_id in enumerate(doc_ids, start=1)
        ],
    }
    if chunk_ids is not None:
        linea["fragmentos"] = [
            {"puesto": n, "chunk_id": chunk_id, "doc_id": doc_id_de(chunk_id)}
            for n, chunk_id in enumerate(chunk_ids, start=1)
        ]
    return linea


def test_el_acierto_perfecto_da_las_tres_metricas_al_maximo(tmp_path):
    ground = escribir_ground(
        tmp_path / "g.json",
        [consulta_etiquetada("q001", [f"F1-A-00{n}-c0001" for n in range(1, 4)])],
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001", "F1-A-002", "F1-A-003"],
                  [f"F1-A-00{n}-c0001" for n in range(1, 4)])],
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.f1 == pytest.approx(1.0)
    assert reporte.ndcg_binario == pytest.approx(1.0)
    assert reporte.ndcg_graduado == pytest.approx(1.0)
    assert reporte.techo_f1 == pytest.approx(1.0)


def test_una_consulta_del_ground_que_falta_detiene_la_evaluacion(tmp_path):
    """Promediar sobre las que si estan devuelve un numero que no compara con nada."""
    ground = escribir_ground(
        tmp_path / "g.json",
        [consulta_etiquetada("q001", ["F1-A-001-c0001"]),
         consulta_etiquetada("q002", ["F1-A-002-c0001"])],
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"])]
    )

    with pytest.raises(ValueError, match="q002"):
        evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)


def test_las_consultas_de_mas_se_ignoran(tmp_path):
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"]),
         registro("q999", ["F1-Z-999"], ["F1-Z-999-c0001"])],
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.n_consultas == 1
    assert reporte.n_ignoradas == 1


def test_sin_fragmentos_se_mide_f1_y_no_ndcg(tmp_path):
    """Un entregable de antes de esta pieza sigue siendo medible a medias."""
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"])]
    )

    reporte = evaluar(cargar_ground(ground), cargar_resultados(resultados), 3, 10)

    assert reporte.f1 > 0.0
    assert reporte.ndcg_binario is None
    assert reporte.ndcg_graduado is None


def test_un_query_id_repetido_en_los_resultados_es_un_error(tmp_path):
    """Dos lineas para la misma consulta y no se sabe cual se evalua."""
    resultados = escribir_resultados(
        tmp_path / "r.jsonl",
        [registro("q001", ["F1-A-001"]), registro("q001", ["F1-B-002"])],
    )

    with pytest.raises(ValueError, match="q001"):
        cargar_resultados(resultados)


def test_el_main_imprime_las_metricas_y_sale_con_cero(tmp_path, capsys):
    """Mide, no verifica: el que falla con 1 es verificar_cobertura.py."""
    ground = escribir_ground(
        tmp_path / "g.json", [consulta_etiquetada("q001", ["F1-A-001-c0001"])]
    )
    resultados = escribir_resultados(
        tmp_path / "r.jsonl", [registro("q001", ["F1-A-001"], ["F1-A-001-c0001"])]
    )

    codigo = main(["--resultados", str(resultados), "--ground", str(ground)])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "F1@3" in salida
    assert "NDCG@10" in salida
