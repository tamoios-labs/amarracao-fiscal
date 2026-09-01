import re
import unicodedata

import pdfplumber

# Limites de coluna (em pontos PDF, x0 mín/máx) da tabela de itens do
# PEDIDO.pdf - posições FIXAS, validadas em vários PEDIDO.pdf reais
# (mesmo template gerado pelo Protheus/Tamoios pra qualquer
# fornecedor, não muda). Só as colunas usadas na amarração.
COLUNAS_PEDIDO = {
    "item": (10, 45),
    "produto": (45, 130),
    "descricao": (130, 250),
    "quantidade": (255, 313),
    "valor_unitario": (355, 390),
}

# Fim do cabeçalho (empresa/pedido) / início da tabela de itens, em
# pontos a partir do topo da página - tudo ANTES disso é cortado fora.
TOPO_TABELA = 85


def _coluna_da_palavra(x0):
    for nome, (ini, fim) in COLUNAS_PEDIDO.items():
        if ini <= x0 < fim:
            return nome
    return None


def extrair_itens_pedido(pdf_path):
    """
    Extrai a tabela de itens do PEDIDO.pdf (Item, Produto, Descricao,
    Quantidade, Valor Unitario) via pdfplumber, direto do texto do PDF
    - sem OCR (o PEDIDO.pdf tem texto real, diferente da nota fiscal
    escaneada).

    Corta tudo ACIMA da tabela (cabeçalho de empresa/pedido, a partir
    de TOPO_TABELA) e usa o resto da página inteira: a tabela muda de
    tamanho (número de itens variável) mas dificilmente passa do fim
    da página. As linhas de itens não têm borda/grade no PDF (só o
    cabeçalho tem - o pdfplumber só reconhece o cabeçalho como
    "tabela" de verdade), então agrupa palavras por posição: mesma
    altura (top) = mesma linha, posição x0 dentro de COLUNAS_PEDIDO =
    coluna.

    Devolve uma lista de dicts, um por item, no MESMO formato de
    `ler_itens_nota` (protheus_automation/grid.py) - {item, produto,
    descricao, quantidade, valor_unitario} - pra poder ser impressa/
    comparada do mesmo jeito. Lista vazia se o PDF não tiver texto
    extraível (alguns PEDIDO.pdf têm fonte com codificação quebrada -
    viram "(cid:X)" em vez de texto real; nesse caso não tem OCR de
    fallback aqui, só retorna vazio).
    """
    with pdfplumber.open(pdf_path) as pdf:
        pagina = pdf.pages[0]
        recorte = pagina.crop((0, TOPO_TABELA, pagina.width, pagina.height))
        palavras = recorte.extract_words()

    linhas = {}
    for p in palavras:
        chave = round(p["top"])
        linhas.setdefault(chave, []).append(p)

    itens = []
    for chave in sorted(linhas):
        colunas = {}
        for p in sorted(linhas[chave], key=lambda p: p["x0"]):
            nome_coluna = _coluna_da_palavra(p["x0"])
            if nome_coluna is None:
                continue
            colunas.setdefault(nome_coluna, []).append(p["text"])

        item = " ".join(colunas.get("item", []))
        if not re.fullmatch(r"\d{4}", item):
            continue  # não é uma linha de item de verdade (rodapé, total, etc.)

        itens.append({
            "item": item,
            "produto": " ".join(colunas.get("produto", [])),
            "descricao": " ".join(colunas.get("descricao", [])),
            "quantidade": " ".join(colunas.get("quantidade", [])),
            "valor_unitario": " ".join(colunas.get("valor_unitario", [])),
        })

    return itens


def valor_com_1_casa(valor_str):
    """
    Converte um valor_unitario no formato brasileiro (ex: "140,6700")
    pra float arredondado com 1 casa decimal (ex: 140.7). Usado pra
    comparar valores com tolerância: a nota e o pedido às vezes trazem
    o mesmo preço com arredondamentos diferentes na 3ª/4ª casa decimal
    (ex: 140,6700 vs 140,6702), então só a 1ª casa importa pra amarrar.
    """
    try:
        return round(float(valor_str.replace(".", "").replace(",", ".")), 1)
    except ValueError:
        return None


def _normalizar(texto):
    """Maiúsculo e sem acentos, pra comparar texto sem depender de variação de acentuação (ex: 'MÃO DE OBRA' vs 'MAO DE OBRA')."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return sem_acento.upper().strip()


def eh_mao_de_obra(descricao):
    return "MAO DE OBRA" in _normalizar(descricao)


def casar_itens(itens_nota, itens_pedido):
    """
    Casa os itens da nota (Protheus) com os itens do pedido (PDF). Dois
    itens formam um casal quando:
      - têm o MESMO produto e o MESMO valor_unitario com 1 casa decimal
        de tolerância (ver `valor_com_1_casa`); OU
      - os dois são "MÃO DE OBRA" (descrição contém isso, sem acento e
        case-insensitive) - nesse caso o valor unitário é IGNORADO, só a
        descrição já basta pra casar (mão de obra costuma vir com
        valores bem diferentes entre nota e pedido, então exigir o mesmo
        valor nunca casaria esses itens).

    Cada item do pedido só entra em um casal (o primeiro item da nota
    que bater com ele), pra não casar o mesmo item do pedido duas vezes
    quando ele se repete em várias linhas.

    Devolve uma lista de tuplas (item_nota, item_pedido, motivo), onde
    `motivo` é "valor" ou "mao_de_obra".
    """
    pares = []
    pedido_usados = set()
    for item_nota in itens_nota:
        valor_nota = valor_com_1_casa(item_nota["valor_unitario"])
        nota_mao_de_obra = eh_mao_de_obra(item_nota["descricao"])
        for idx, item_pedido in enumerate(itens_pedido):
            if idx in pedido_usados:
                continue

            bateu_valor = (
                item_nota["produto"] == item_pedido["produto"]
                and valor_nota is not None
                and valor_nota == valor_com_1_casa(item_pedido["valor_unitario"])
            )
            bateu_mao_de_obra = nota_mao_de_obra and eh_mao_de_obra(item_pedido["descricao"])

            if bateu_valor or bateu_mao_de_obra:
                motivo = "valor" if bateu_valor else "mao_de_obra"
                pares.append((item_nota, item_pedido, motivo))
                pedido_usados.add(idx)
                break
    return pares


def imprimir_amarracao(pares):
    """
    Imprime os casais (item da nota <-> item do pedido) achados por
    `casar_itens`, um por linha (não duas - NOTA e PEDIDO separadas
    ficava repetitivo pra quem só quer confirmar visualmente o
    casamento). Mostra código do produto e valor unitário dos DOIS
    lados - o valor pode não bater na última casa decimal (casa por
    tolerância, ver `valor_com_1_casa`), então vale mostrar os dois
    valores, não só um.
    """
    print(f"{len(pares)} casal(is) de amarração:")
    for item_nota, item_pedido, motivo in pares:
        aviso = "  [vinculado por ser mão de obra]" if motivo == "mao_de_obra" else ""
        print(f"  - Nota item {item_nota['item']} ({item_nota['produto']}) {item_nota['descricao']} "
              f"R$ {item_nota['valor_unitario']}"
              f"  <->  Pedido item {item_pedido['item']} ({item_pedido['produto']}) {item_pedido['descricao']} "
              f"R$ {item_pedido['valor_unitario']}{aviso}")


def imprimir_itens(itens, titulo="item(ns)"):
    """Imprime os itens, um por linha, com item e descrição - sem os campos técnicos (código do produto, qtd, valor)."""
    print(f"{len(itens)} {titulo}:")
    for it in itens:
        print(f"  - {it['descricao']} (item {it['item']})")
