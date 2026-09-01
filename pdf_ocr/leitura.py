"""
Fluxo de LEITURA de dados das notas fiscais (OCR).

Módulo independente do GLPI — tem seu próprio fluxo e seus próprios
prints no terminal. O GLPI só baixa os PDFs; a leitura acontece aqui,
depois, de forma separada.
"""
import os

from .config import ARQUIVO_AREA, CHAMADOS_DIR
from .area import carregar_area
from .notas_ideal import eh_pasta_ideal, eh_nota_fiscal, extrair_notas_do_chamado


def _tem_nota_fiscal(pasta):
    try:
        return any(eh_nota_fiscal(f) for f in os.listdir(pasta))
    except OSError:
        return False


def _buscar_pastas_ideal(raiz):
    """Pastas de chamado (com notas fiscais) cujo nome contém IDEAL."""
    pastas = []
    for dirpath, _dirnames, filenames in os.walk(raiz):
        nome = os.path.basename(dirpath)
        if not eh_pasta_ideal(nome):
            continue
        if not any(eh_nota_fiscal(f) for f in filenames):
            continue
        pastas.append(dirpath)
    return pastas


def executar_leitura_ideal(raiz=None, pastas=None):
    """
    Passa OCR em todas as notas fiscais das pastas IDEAL e mostra os
    números no terminal. Retorna { caminho_da_pasta: [ {arquivo, numero}, ... ] }.

    - Se `pastas` for uma lista (ex: as pastas criadas na rodada atual
      do GLPI), processa SÓ essas (filtrando as que têm IDEAL no nome).
    - Senão, varre `raiz` inteira (default: chamados_glpi/).
    """
    print("=" * 60)
    print("  LEITURA DE NOTAS FISCAIS - IDEAL")
    print("=" * 60)

    print("Buscando pastas com nome IDEAL...")
    if pastas is not None:
        alvos = [p for p in pastas if eh_pasta_ideal(os.path.basename(p)) and _tem_nota_fiscal(p)]
    else:
        alvos = _buscar_pastas_ideal(raiz if raiz is not None else CHAMADOS_DIR)
    pastas = alvos
    print(f"{len(pastas)} pasta(s) IDEAL encontrada(s).")

    if not pastas:
        print("Nada a ler.")
        return {}

    area = carregar_area(ARQUIVO_AREA)

    resultado = {}
    total_notas = 0

    for pasta in pastas:
        print(f"\nPasta: {os.path.basename(pasta)}")
        print("  Passando OCR nas notas fiscais...")
        notas = extrair_notas_do_chamado(pasta, area)
        resultado[pasta] = notas
        for item in notas:
            total_notas += 1
            numero = item["numero"] if item["numero"] else "(não encontrado)"
            print(f"    {item['arquivo']}  ->  NF: {numero}")

    print("\n" + "=" * 60)
    print(f"  {len(pastas)} pasta(s) IDEAL | {total_notas} nota(s) fiscal(is) lida(s)")
    for pasta, notas in resultado.items():
        print(f"    {os.path.basename(pasta)}:")
        for item in notas:
            numero = item["numero"] if item["numero"] else "(não encontrado)"
            print(f"      {item['arquivo']}  ->  NF: {numero}")
    print("=" * 60)

    return resultado
