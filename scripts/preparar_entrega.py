"""Ensambla la carpeta ``entrega/`` con la estructura que pide ADL.

::

    entrega/
    ├── resultados.jsonl
    ├── generador.py
    ├── informe_tecnico.pdf
    └── base_vectorial/
        └── encoder_<nombre>/
            ├── index.faiss
            └── metadata.jsonl

``generador.py`` no viaja solo: importa media raíz del proyecto, así que se copia
el cierre transitivo de sus imports —calculado del AST, no de una lista a mano
que se quedaría vieja— al mismo directorio, que es donde Python lo busca.

Uso::

    python scripts/preparar_entrega.py --indice indice \
        --resultados entrega/resultados.jsonl --destino entrega
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from encoder import CONFIG_POR_DEFECTO  # noqa: E402
from generador import NOMBRE_INDICE, NOMBRE_METADATA  # noqa: E402

NOMBRE_RESULTADOS = "resultados.jsonl"
NOMBRE_GENERADOR = "generador.py"
NOMBRE_BASE_VECTORIAL = "base_vectorial"
NOMBRE_INFORME = "informe_tecnico.pdf"

MODULO_DE_ENTRADA = "generador"


@dataclass(frozen=True)
class ReporteEntrega:
    """Qué quedó en la carpeta de entrega."""

    destino: Path
    encoder: str        # nombre del directorio bajo base_vectorial/
    modulos: list[str]  # .py copiados junto al generador, en orden alfabético
    n_vectores: int
    n_consultas: int
    falta_informe: bool  # el informe no lo genera el pipeline; se avisa si falta


def nombre_de_encoder(modelo: str) -> str:
    """``ibm-granite/granite-embedding-311m…`` → ``encoder_granite-embedding-311m…``

    Solo la última parte: una barra dentro del nombre haría un subdirectorio.
    """
    return f"encoder_{modelo.rstrip('/').split('/')[-1]}"


def modulos_necesarios(raiz: Path, entrada: str = MODULO_DE_ENTRADA) -> list[Path]:
    """Los ``.py`` de ``raiz`` que ``entrada`` necesita, directa o indirectamente.

    Solo sigue los imports que existen como módulo suelto en ``raiz``: los
    paquetes externos los resuelve el intérprete del jurado.
    """
    raiz = Path(raiz)
    propios = {ruta.stem for ruta in raiz.glob("*.py")}

    encontrados: set[str] = set()
    pendientes = [entrada]
    while pendientes:
        modulo = pendientes.pop()
        if modulo in encontrados:
            continue
        ruta = raiz / f"{modulo}.py"
        if not ruta.is_file():
            continue
        encontrados.add(modulo)
        pendientes.extend(dep for dep in _imports_de(ruta) if dep in propios)

    return sorted((raiz / f"{modulo}.py" for modulo in encontrados), key=lambda p: p.name)


def _imports_de(ruta: Path) -> set[str]:
    """Nombres de primer nivel importados por un archivo."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def preparar_entrega(
    raiz: Path,
    indice: Path,
    resultados: Path,
    destino: Path,
    modelo: str = CONFIG_POR_DEFECTO.modelo,
) -> ReporteEntrega:
    """Copia todo lo que va en la entrega y devuelve qué quedó dentro.

    Comprueba antes de escribir nada: una entrega a medio copiar parece completa.
    """
    raiz, indice, resultados, destino = map(Path, (raiz, indice, resultados, destino))

    origen_indice = indice / NOMBRE_INDICE
    origen_metadata = indice / NOMBRE_METADATA
    for ruta in (origen_indice, origen_metadata, resultados):
        if not ruta.is_file():
            raise ValueError(f"falta {ruta.name}: no está en {ruta.parent}")

    modulos = modulos_necesarios(raiz)
    if not any(ruta.name == NOMBRE_GENERADOR for ruta in modulos):
        raise ValueError(f"falta {NOMBRE_GENERADOR} en {raiz}")

    encoder = nombre_de_encoder(modelo)
    carpeta_encoder = destino / NOMBRE_BASE_VECTORIAL / encoder
    carpeta_encoder.mkdir(parents=True, exist_ok=True)

    _copiar(origen_indice, carpeta_encoder / NOMBRE_INDICE)
    _copiar(origen_metadata, carpeta_encoder / NOMBRE_METADATA)
    _copiar(resultados, destino / NOMBRE_RESULTADOS)
    for modulo in modulos:
        _copiar(modulo, destino / modulo.name)

    return ReporteEntrega(
        destino=destino,
        encoder=encoder,
        modulos=[ruta.name for ruta in modulos],
        n_vectores=_contar_lineas(origen_metadata),
        n_consultas=_contar_lineas(resultados),
        falta_informe=not (destino / NOMBRE_INFORME).is_file(),
    )


def _copiar(origen: Path, destino: Path) -> None:
    """Copia salvo que el archivo ya esté donde tiene que estar: ``generador.py``
    escribe ``resultados.jsonl`` en ``entrega/``, y en Windows copiarlo sobre sí
    mismo es un ``PermissionError``."""
    if destino.exists() and origen.samefile(destino):
        return
    shutil.copy2(origen, destino)


def _contar_lineas(ruta: Path) -> int:
    with ruta.open(encoding="utf-8") as archivo:
        return sum(1 for linea in archivo if linea.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--indice", required=True, type=Path, help="directorio del índice")
    parser.add_argument(
        "--resultados", required=True, type=Path, help="resultados.jsonl de las consultas"
    )
    parser.add_argument("--destino", type=Path, default=Path("entrega"))
    parser.add_argument("--modelo", default=CONFIG_POR_DEFECTO.modelo)
    parser.add_argument(
        "--raiz",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="raíz del proyecto de donde salen los módulos",
    )
    args = parser.parse_args(argv)

    reporte = preparar_entrega(
        raiz=args.raiz,
        indice=args.indice,
        resultados=args.resultados,
        destino=args.destino,
        modelo=args.modelo,
    )

    print(f"entrega en {reporte.destino}")
    print(f"  base_vectorial/{reporte.encoder}/  {reporte.n_vectores} vectores")
    print(f"  resultados.jsonl                   {reporte.n_consultas} consultas")
    print(f"  módulos: {', '.join(reporte.modulos)}")
    if reporte.falta_informe:
        print(f"\nfalta {NOMBRE_INFORME}: no lo genera el pipeline, hay que escribirlo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
