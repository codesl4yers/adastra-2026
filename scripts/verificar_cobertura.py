"""Comprueba que todo documento del inventario de ADL tiene vectores en el índice.

Es la verificación de piso de la entrega. El emparejamiento del jurado es por
``fuente`` (§10.2.1), así que un documento sin un solo vector no puede aparecer
en el top-3 de ninguna consulta: no es que recupere mal, es que es imposible
que recupere. Y nada más en el pipeline lo detecta —un extractor que falla en
silencio produce un ``Documento`` válido con cero bloques, el fragmentador
produce cero fragmentos y el generador no echa de menos lo que nunca llegó—.

Se empareja por ``doc_id`` y no por ``fuente`` porque 59 nombres de archivo se
repiten en el corpus: con el nombre, un documento cubierto taparía el hueco de
otro que comparte nombre y el informe daría por buena una cobertura que no es.

Uso::

    python scripts/verificar_cobertura.py \
        --indice base_documental/Indice_Datos_Codefest.xlsx \
        --metadata indice/metadata.jsonl

Devuelve 1 si hay algún documento sin vectores.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from indice import EntradaIndice, cargar_indice  # noqa: E402

# Cuántos huecos se listan antes de resumir. Con el corpus entero fuera del
# índice, volcar 1826 líneas al terminal esconde el resto del diagnóstico.
MAXIMO_LISTADO = 40


def doc_ids_con_vectores(metadata: Path) -> set[str]:
    """Los ``doc_id`` que aparecen al menos una vez en ``metadata.jsonl``."""
    presentes: set[str] = set()
    with Path(metadata).open(encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError as error:
                raise ValueError(f"{metadata}:{numero} no es JSON válido: {error}") from error
            doc_id = registro.get("doc_id")
            if isinstance(doc_id, str) and doc_id:
                presentes.add(doc_id)
    return presentes


def documentos_sin_vectores(
    entradas: dict[str, EntradaIndice], metadata: Path
) -> list[EntradaIndice]:
    """Las entradas del inventario que no tienen ni un vector en el índice.

    Conserva el orden del inventario: dos corridas sobre el mismo par de
    archivos tienen que dar el mismo informe.
    """
    presentes = doc_ids_con_vectores(metadata)
    return [entrada for entrada in entradas.values() if entrada.doc_id not in presentes]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--indice", required=True, type=Path, help="Indice_Datos_Codefest.xlsx de ADL"
    )
    parser.add_argument(
        "--metadata", required=True, type=Path, help="metadata.jsonl del índice generado"
    )
    args = parser.parse_args(argv)

    entradas = cargar_indice(args.indice)
    huecos = documentos_sin_vectores(entradas, args.metadata)

    total = len(entradas)
    print(f"documentos en el inventario: {total}")
    print(f"con vectores en el índice:   {total - len(huecos)}")

    if not huecos:
        print("sin huecos: todo documento del inventario puede recuperarse")
        return 0

    print(f"\nSIN UN SOLO VECTOR: {len(huecos)}")
    for entrada in huecos[:MAXIMO_LISTADO]:
        print(f"  {entrada.doc_id}  {entrada.ruta_relativa}")
    if len(huecos) > MAXIMO_LISTADO:
        print(f"  ... y {len(huecos) - MAXIMO_LISTADO} más")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
