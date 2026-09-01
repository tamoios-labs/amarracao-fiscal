import re
import time

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .log import ok, erro


def extrair_dados_card(html_content):
    """
    Extrai os dados do card de pagamento (FORNECEDOR, VENCIMENTO, Nº PC,
    N° NOTA FISCAL) da div 'read-only-content' via BeautifulSoup + regex.
    Formata NFs em faixa (ex: "24668 à 24689"). Retorna None se inválido.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # 1. Encontra a div 'read-only-content' correta
    read_only_divs = soup.find_all('div', class_='read-only-content')
    read_only_content = None

    for rod in read_only_divs:
        if 'Dados do formulário' in rod.get_text():
            read_only_content = rod
            break

    if not read_only_content:
        for div in soup.find_all('div'):
            if div.find('h1', string='Dados do formulário') or 'Dados do formulário' in div.get_text():
                read_only_content = div
                break

    if not read_only_content:
        erro("Container com 'Dados do formulário' não foi localizado.", nivel=1)
        return None

    texto_bruto = " ".join(read_only_content.get_text().split())

    # 2. Captura via Regex baseado na numeração do formulário
    match_fornecedor = re.search(r'FORNECEDOR\s*:\s*(.*?)(?=\s*\d\)|$)', texto_bruto, re.IGNORECASE)
    match_vencimento = re.search(r'VENCIMENTO\s*:\s*(.*?)(?=\s*\d\)|$)', texto_bruto, re.IGNORECASE)
    match_pc         = re.search(r'Nº\s*PC\s*:\s*(.*?)(?=\s*\d\)|$)', texto_bruto, re.IGNORECASE)
    match_nf         = re.search(r'N[°º]\s*NOTA\s*FISCAL\s*:\s*(.*?)(?=\s*\d\)|$)', texto_bruto, re.IGNORECASE)

    fornecedor    = match_fornecedor.group(1).strip() if match_fornecedor else ""
    data          = match_vencimento.group(1).strip() if match_vencimento else ""
    numero_pedido = match_pc.group(1).strip() if match_pc else ""
    nf_bruta      = match_nf.group(1).strip() if match_nf else ""

    if not fornecedor and not data:
        erro("Falha ao mapear os campos internos via Regex.", nivel=1)
        return None

    # 3. Processa/ordena/formata as Notas Fiscais
    nf_formatada = ""
    if nf_bruta:
        numeros_nf = [int(n) for n in nf_bruta.split() if n.isdigit()]
        if numeros_nf:
            numeros_nf.sort()
            if len(numeros_nf) > 1:
                nf_formatada = f"{numeros_nf[0]} à {numeros_nf[-1]}"
            else:
                nf_formatada = str(numeros_nf[0])
        else:
            nf_formatada = nf_bruta

    resultado = {
        "dados": {
            "data": data,
            "fornecedor": fornecedor,
            "numero_pedido": numero_pedido,
            "nf": nf_formatada
        }
    }

    ok(f"Dados extraídos: {fornecedor} | venc. {data} | PC {numero_pedido} | NF {nf_formatada}", nivel=1)
    return resultado


def extrair_dados_card_selenium(driver):
    """
    Aguarda o carregamento da página do chamado, valida que é um card de
    pagamento (com os 4 campos obrigatórios) e extrai os dados.
    """
    try:
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "read-only-content")))
        time.sleep(2)

        html_content = driver.page_source

        if 'read-only-content' not in html_content:
            erro("'read-only-content' não encontrado na página.", nivel=1)
            return None

        if 'Dados do formulário' not in html_content:
            erro("'Dados do formulário' não encontrado na página.", nivel=1)
            return None

        if 'card-title card-header' not in html_content:
            erro("Título do card não encontrado na página.", nivel=1)
            return None

        campos_requeridos = ['FORNECEDOR', 'VENCIMENTO', 'Nº PC', 'N° NOTA FISCAL']
        campos_faltantes = [c for c in campos_requeridos if c not in html_content]

        if campos_faltantes:
            erro(f"Campos faltantes no card: {', '.join(campos_faltantes)}.", nivel=1)
            return None

        return extrair_dados_card(html_content)

    except Exception as e:
        erro(f"Falha ao aguardar carregamento da página: {e}", nivel=1)
        return None
