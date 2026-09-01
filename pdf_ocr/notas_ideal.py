import os
import re
import shutil

from .config import ARQUIVO_AREA
from .area import carregar_area
from .leitor import extrair_numero_nota, extrair_numero_e_recorte, extrair_numero_pedido_e_recorte
from .pedido import extrair_itens_pedido, imprimir_itens

# ─────────────────────────────────────────────────────────────────────
# VALIDAÇÃO: por enquanto só processamos pastas de chamado cujo nome
# contém "IDEAL". O motivo é que os PDFs desse fornecedor têm um layout
# específico, e a área de OCR (numero_nf_ideal.json) foi calibrada só
# pra ele. Qualquer outra pasta é ignorada.
# ─────────────────────────────────────────────────────────────────────
MARCA_IDEAL = "ideal"

# Nome de nota já renomeada pro número real da NF (ex: "000028305.pdf")
# - é o que `glpi_automation/executar.py` já deixa no download, fazendo
# o OCR na hora em vez de deixar pra depois. Usado tanto em
# `eh_nota_fiscal` (reconhecer esse formato como nota) quanto em
# `numero_do_nome_arquivo` (extrair o número sem precisar de OCR de novo).
_RE_NUMERO_NOTA = re.compile(r"^(\d{9})\.pdf$", re.IGNORECASE)


def eh_pasta_ideal(nome_pasta):
    """True se o nome da pasta do chamado contém 'IDEAL' (case-insensitive)."""
    return MARCA_IDEAL in nome_pasta.lower()


def eh_nota_fiscal(nome_arquivo):
    """
    True se o arquivo é uma nota fiscal - tanto o nome genérico do
    anexo baixado do GLPI ('NOTA FISCAL.pdf', 'NOTA FISCAL_1.pdf')
    quanto já renomeado pro número real da NF pelo OCR feito no
    download ('000028305.pdf', ver `numero_do_nome_arquivo`). Exclui o
    PEDIDO.pdf e outros anexos.

    Também exclui notas já amarradas, renomeadas com o prefixo
    "AMARRADA - " por `fluxo_protheus` (amarracao.py) depois de
    responder o chamado no GLPI - elas ficam na pasta como registro,
    mas não devem ser lidas/reprocessadas numa rodada nova (nem por
    OCR, já que o número não bate mais o padrão de 9 dígitos).
    """
    nome = nome_arquivo.lower()
    if not nome.endswith(".pdf"):
        return False
    if nome.startswith("amarrada"):
        return False
    return nome.startswith("nota fiscal") or bool(_RE_NUMERO_NOTA.match(nome_arquivo))


def numero_do_nome_arquivo(nome_arquivo):
    """
    Se `nome_arquivo` já está no formato '000028305.pdf' (renomeado no
    download, ver `glpi_automation/executar.py`), devolve o número
    direto do nome, sem precisar rodar OCR de novo. None se o nome
    ainda for o genérico do anexo ('NOTA FISCAL.pdf' e variantes).
    """
    match = _RE_NUMERO_NOTA.match(nome_arquivo)
    return match.group(1) if match else None


def listar_notas_fiscais(pasta_chamado):
    """Caminhos de todas as notas fiscais dentro da pasta do chamado."""
    return [
        os.path.join(pasta_chamado, f)
        for f in sorted(os.listdir(pasta_chamado))
        if eh_nota_fiscal(f)
    ]


def extrair_notas_do_chamado(pasta_chamado, area=None):
    """
    Extrai o número da NF de TODAS as notas fiscais de uma pasta de
    chamado IDEAL. Retorna uma lista de dicts: {arquivo, numero}.

    Notas já renomeadas pro número real no download (`numero_do_nome_
    arquivo`) não passam por OCR de novo - o número já está no nome.
    """
    if area is None:
        area = carregar_area(ARQUIVO_AREA)

    resultados = []
    for pdf in listar_notas_fiscais(pasta_chamado):
        nome_arquivo = os.path.basename(pdf)
        numero = numero_do_nome_arquivo(nome_arquivo) or extrair_numero_nota(pdf, area)
        resultados.append({"arquivo": nome_arquivo, "numero": numero})
    return resultados


def _achar_pedido(pasta_chamado):
    """Caminho do PEDIDO.pdf da pasta do chamado, ou None se não existir."""
    for f in os.listdir(pasta_chamado):
        if f.lower() == "pedido.pdf":
            return os.path.join(pasta_chamado, f)
    return None


def exportar_notas_do_chamado(pasta_chamado, pasta_destino, area=None):
    """
    Mesma extração de `extrair_notas_do_chamado`, mas TAMBÉM exporta pra
    `pasta_destino` (a pasta do chamado dentro do lote datado da
    rodada de OCR):
    - ANTES das notas: o PEDIDO.pdf de origem copiado, o número do
      pedido extraído por OCR (ex: "061410 /1" -> "061410") e o
      recorte usado nessa extração salvo como "PEDIDO_061410.jpg";
    - o PDF original de cada nota, renomeado pro número extraído
      (ex: "000026622.pdf");
    - o recorte da área usada no OCR de cada nota, como .jpg com o
      mesmo nome (ex: "000026622.jpg") - o "print" do quadrado que foi
      lido.

    Notas cujo OCR não encontrou o número são puladas na exportação
    (não tem como nomear o arquivo pelo número), mas ainda entram no
    retorno com numero=None, igual `extrair_notas_do_chamado`. O mesmo
    vale pro pedido: se o número não for encontrado, só avisa e segue
    (o PEDIDO.pdf já foi copiado de qualquer forma).

    Retorna a mesma lista de {arquivo, numero} que `extrair_notas_do_chamado`
    (só das notas - o número do pedido não entra nesse retorno).
    """
    if area is None:
        area = carregar_area(ARQUIVO_AREA)

    os.makedirs(pasta_destino, exist_ok=True)

    pedido = _achar_pedido(pasta_chamado)
    if pedido:
        shutil.copy2(pedido, os.path.join(pasta_destino, os.path.basename(pedido)))
        numero_pedido, recorte_pedido = extrair_numero_pedido_e_recorte(pedido)
        if numero_pedido:
            recorte_pedido.convert("RGB").save(
                os.path.join(pasta_destino, f"PEDIDO_{numero_pedido}.jpg"), "JPEG"
            )
        else:
            print(f"    [AVISO] Número do pedido não encontrado em: {pedido}")

        itens_pedido = extrair_itens_pedido(pedido)
        if itens_pedido:
            imprimir_itens(itens_pedido, "item(ns) no pedido")
        else:
            print("    [AVISO] Nenhum item extraído do PEDIDO.pdf (texto ilegível ou tabela vazia).")

    resultados = []
    for pdf in listar_notas_fiscais(pasta_chamado):
        nome_arquivo = os.path.basename(pdf)
        numero_do_nome = numero_do_nome_arquivo(nome_arquivo)
        if numero_do_nome:
            # Já renomeada no download (ver glpi_automation/executar.py) -
            # não roda OCR de novo, então não tem recorte novo pra salvar
            # como .jpg (só existe pra quem passou pelo OCR aqui agora).
            numero, recorte = numero_do_nome, None
        else:
            numero, recorte = extrair_numero_e_recorte(pdf, area)
        resultados.append({"arquivo": nome_arquivo, "numero": numero})

        if numero is None:
            continue

        shutil.copy2(pdf, os.path.join(pasta_destino, f"{numero}.pdf"))
        if recorte is not None:
            recorte.convert("RGB").save(os.path.join(pasta_destino, f"{numero}.jpg"), "JPEG")

    return resultados


def _para_int(numero):
    """'000026541' -> 26541. None ou lixo do OCR -> None."""
    if not numero:
        return None
    try:
        return int(numero)
    except (TypeError, ValueError):
        return None


def primeira_nota(notas):
    """
    A "primeira" nota de um agrupamento é a de MENOR NÚMERO — não a do
    primeiro arquivo. A ordem em que o GLPI baixa os anexos
    (NOTA FISCAL.pdf, _1, _2, ...) não tem relação nenhuma com a
    numeração das notas: num agrupamento real de "26512 à 26567", o _1
    era a 26541 e o _7 era a 26513.

    Ignora as notas cujo OCR falhou (numero=None) — inclusive quando é o
    NOTA FISCAL.pdf, que também falha às vezes.

    Devolve o dict da nota com 'numero_busca' acrescentado, ou None se
    nenhuma nota do agrupamento foi lida.
    """
    lidas = [(n, _para_int(n.get("numero"))) for n in notas]
    lidas = [(n, valor) for n, valor in lidas if valor is not None]
    if not lidas:
        return None

    nota, valor = min(lidas, key=lambda par: par[1])

    # 'numero_busca' vai sem os zeros à esquerda ('000026622' -> '26622'):
    # é assim que a nota aparece no formulário do GLPI e no nome da pasta,
    # e uma busca por substring casa com as duas formas — o contrário não.
    return {**nota, "numero_busca": str(valor)}


def todas_notas_ordenadas(notas):
    """
    Como `primeira_nota`, mas devolve TODAS as notas legíveis do
    agrupamento (não só a de menor número), ordenadas por número
    crescente, cada uma com 'numero_busca' acrescentado - usado pra
    processar a pasta inteira (uma nota de cada vez, avançando pelo
    botão 'Cancelar' do Protheus), não só a primeira.

    Ignora as notas cujo OCR falhou (numero=None), igual `primeira_nota`.
    """
    lidas = [(n, _para_int(n.get("numero"))) for n in notas]
    lidas = [(n, valor) for n, valor in lidas if valor is not None]
    lidas.sort(key=lambda par: par[1])
    return [{**n, "numero_busca": str(valor)} for n, valor in lidas]


def primeiras_notas_por_chamado(resultado):
    """
    Recebe o retorno de `executar_leitura_ideal` ({pasta: [notas]}) e
    devolve só a primeira nota de cada agrupamento — que é a que entra
    na pesquisa do Protheus. Basta uma: o pedido de compra é o mesmo
    para todas as notas do agrupamento.

    Devolve [{pasta, arquivo, numero, numero_busca}], pulando os
    agrupamentos em que nenhuma nota foi lida.
    """
    primeiras = []
    for pasta, notas in sorted(resultado.items()):
        primeira = primeira_nota(notas)
        if primeira is None:
            continue
        primeiras.append({"pasta": pasta, **primeira})
    return primeiras


def processar_chamados_ideal(raiz):
    """
    Percorre `raiz` (ex: chamados_glpi/) procurando pastas de chamado
    cujo nome contém IDEAL e que tenham notas fiscais, e extrai os
    números de NF de cada nota.

    Retorna um dict: { caminho_da_pasta: [ {arquivo, numero}, ... ] }.
    (Por enquanto é só pra teste/validação — quem chama imprime tudo.)
    """
    area = carregar_area(ARQUIVO_AREA)
    resultado_geral = {}

    for dirpath, _dirnames, filenames in os.walk(raiz):
        nome = os.path.basename(dirpath)
        if not eh_pasta_ideal(nome):
            continue
        if not any(eh_nota_fiscal(f) for f in filenames):
            continue
        resultado_geral[dirpath] = extrair_notas_do_chamado(dirpath, area)

    return resultado_geral
