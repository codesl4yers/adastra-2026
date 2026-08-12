"""Pruebas del barrido de configuraciones (§8.3 del spec del fragmentador)."""

import json

import pytest
from conftest import bloque

from contrato import documento_a_dict
from scripts.barrido_fragmentacion import FilaBarrido, barrer, main, tabla_markdown
from tests.test_fragmentador import prosa


@pytest.fixture
def documentos(documento_con_bloques):
    return [
        documento_con_bloques(
            bloque("Introducción", "titulo", 1),
            bloque(prosa(60), ruta=["Introducción"]),
            fuente="uno.pdf",
        ),
        documento_con_bloques(bloque(prosa(40, marca="b")), fuente="dos.pdf"),
    ]


def test_barre_todas_las_combinaciones(documentos):
    filas = barrer(documentos, objetivos=(120, 190, 240), solapes=(0, 1))
    assert len(filas) == 6


def test_el_barrido_cubre_al_menos_cuatro_configuraciones(documentos):
    """§8.3 exige un mínimo de cuatro."""
    assert len(barrer(documentos)) >= 4


def test_un_objetivo_menor_produce_mas_fragmentos(documentos):
    filas = barrer(documentos, objetivos=(120, 240), solapes=(0,))
    corto, largo = filas
    assert corto.n_fragmentos > largo.n_fragmentos


def test_la_mediana_no_supera_el_p95(documentos):
    for fila in barrer(documentos):
        assert fila.mediana_palabras <= fila.p95_palabras


def test_un_corpus_vacio_no_divide_por_cero(documento_con_bloques):
    filas = barrer([documento_con_bloques(fuente="vacio.pdf")])
    assert all(fila.porcentaje_una_oracion == 0.0 for fila in filas)
    assert all(fila.n_fragmentos == 0 for fila in filas)


def test_la_tabla_lleva_cabecera_y_una_linea_por_configuracion():
    filas = [FilaBarrido(190, 1, 10, 180, 230, 5.0, 2.0)]
    lineas = tabla_markdown(filas).splitlines()
    assert len(lineas) == 3  # cabecera, separador y una fila
    assert lineas[0].startswith("| objetivo")
    assert "| 190 | 1 | 10 | 180 | 230 | 5.0 % | 2.0 % |" == lineas[2]


def test_el_cli_del_barrido_escribe_la_tabla(tmp_path, documentos):
    entrada = tmp_path / "extraidos"
    entrada.mkdir()
    for documento in documentos:
        (entrada / f"{documento.doc_id}.json").write_text(
            json.dumps(documento_a_dict(documento), ensure_ascii=False), encoding="utf-8"
        )
    salida = tmp_path / "barrido.md"

    assert main(["--entrada", str(entrada), "--salida", str(salida)]) == 0
    assert salida.read_text(encoding="utf-8").startswith("| objetivo")
