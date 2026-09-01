import os
import re
import sys
import time
import json
import tempfile
from datetime import datetime

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By

from browser_config import get_chrome_options
from pdf_ocr import ARQUIVO_AREA, carregar_area, eh_pasta_ideal, eh_nota_fiscal, extrair_numero_nota
from .config import GLPI_URL_CHAMADOS, PASTA_SAIDA_BASE, RESULT_FILE, LIMITE_TESTE_CHAMADOS
from .login import a_logar
from .coletar import coletar_chamados_com_feito
from .extrair import extrair_dados_card_selenium
from .log import log, titulo, secao, ok, aviso, erro, info


def _pasta_do_dia():
    """Pasta de saída do dia, DENTRO do diretório da amarracao."""
    data_hoje = datetime.now().strftime("%d-%m-%Y")
    pasta = os.path.join(PASTA_SAIDA_BASE, data_hoje)
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _salvar_resultados(dados_chamados):
    """Salva o JSON consolidado de forma atômica."""
    os.makedirs(PASTA_SAIDA_BASE, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile('w', dir=PASTA_SAIDA_BASE, delete=False, encoding='utf-8') as tf:
            json.dump(dados_chamados, tf, ensure_ascii=False, indent=2)
            temp_nome = tf.name
        os.replace(temp_nome, RESULT_FILE)
    except Exception as e:
        erro(f"Erro ao salvar resultados_completo.json: {e}", nivel=1)


def _baixar_anexos(driver, caminho_pasta_chamado):
    """Baixa todos os anexos do chamado reusando a sessão do Selenium."""
    try:
        session = requests.Session()
        for cookie in driver.get_cookies():
            session.cookies.set(cookie['name'], cookie['value'])
        session.headers.update({"User-Agent": driver.execute_script("return navigator.userAgent;")})

        # A timeline do GLPI carrega os anexos sob demanda ao rolar - sem
        # isso, anexos mais antigos do chamado nem aparecem no DOM.
        for _ in range(12):
            driver.execute_script("""
            var timeline = document.querySelector('.itil-timeline');
            if (timeline) { timeline.scrollTop += 800; }
            """)
            time.sleep(0.4)
        time.sleep(1)

        cards = driver.find_elements(By.CLASS_NAME, "timeline-content")

        total_downloads = 0
        nomes_utilizados = set()

        # anexos diretos nos cards
        for card in cards:
            try:
                links = card.find_elements(By.XPATH, ".//a[contains(@href, 'document.send.php')]")
                if not links:
                    continue

                for idx_link, elemento in enumerate(links, 1):
                    try:
                        total_downloads += 1
                        url_download = elemento.get_attribute("href")
                        texto_visivel = elemento.text.strip()
                        nome_arquivo_real = elemento.get_attribute("title")

                        extensao = ""
                        if nome_arquivo_real and "." in nome_arquivo_real:
                            extensao = "." + nome_arquivo_real.split(".")[-1].lower()
                        elif "." in texto_visivel:
                            extensao = "." + texto_visivel.split(".")[-1].lower()
                        else:
                            extensao = ".bin"

                        if " - " in texto_visivel:
                            nome_limpo = texto_visivel.split(" - ")[-1].strip()
                        else:
                            nome_limpo = nome_arquivo_real or f"ANEXO_{idx_link}"

                        nome_limpo = nome_limpo.replace(extensao, "").strip()
                        nome_final = f"{nome_limpo}{extensao}"
                        nome_final = re.sub(r'[\\/*?:"<>|]', '', nome_final).strip()

                        contador_dup = 1
                        base_nome = nome_final
                        while nome_final in nomes_utilizados:
                            nome_sem_ext = base_nome.replace(extensao, "")
                            nome_final = f"{nome_sem_ext}_{contador_dup}{extensao}"
                            contador_dup += 1
                        nomes_utilizados.add(nome_final)

                        destino = os.path.join(caminho_pasta_chamado, nome_final)
                        resposta = session.get(url_download, stream=True)
                        if resposta.status_code == 200:
                            with open(destino, 'wb') as f:
                                for chunk in resposta.iter_content(8192):
                                    f.write(chunk)
                            ok(nome_final, nivel=1)
                        else:
                            erro(f"{nome_final} (HTTP {resposta.status_code})", nivel=1)
                    except Exception as e:
                        aviso(f"Falha ao processar anexo: {e}", nivel=1)
            except Exception as e:
                aviso(f"Falha ao ler anexos de um card: {e}", nivel=1)

        # anexos em sub-documents (mensagens)
        subdocs = driver.find_elements(
            By.XPATH,
            "//ul[contains(@class,'sub-documents')]//a[contains(@href,'document.send.php')]"
        )
        for elemento in subdocs:
            try:
                total_downloads += 1
                url_download = elemento.get_attribute("href")
                nome_arquivo_real = elemento.get_attribute("title")

                extensao = ".bin"
                if nome_arquivo_real and "." in nome_arquivo_real:
                    extensao = "." + nome_arquivo_real.split(".")[-1].lower()

                nome_final = f"ANEXO_{total_downloads}{extensao}"
                nome_final = re.sub(r'[\\/*?:"<>|]', '', nome_final).strip()

                contador_dup = 1
                base_nome = nome_final
                while nome_final in nomes_utilizados:
                    nome_sem_ext = base_nome.replace(extensao, "")
                    nome_final = f"{nome_sem_ext}_{contador_dup}{extensao}"
                    contador_dup += 1
                nomes_utilizados.add(nome_final)

                destino = os.path.join(caminho_pasta_chamado, nome_final)
                resposta = session.get(url_download, stream=True)
                if resposta.status_code == 200:
                    with open(destino, 'wb') as f:
                        for chunk in resposta.iter_content(8192):
                            f.write(chunk)
                    ok(nome_final, nivel=1)
                else:
                    erro(f"{nome_final} (HTTP {resposta.status_code})", nivel=1)
            except Exception as e:
                aviso(f"Falha ao processar sub-documento: {e}", nivel=1)

        info(f"{total_downloads} anexo(s) baixado(s).", nivel=1)
    except Exception as e:
        erro(f"Falha crítica ao baixar anexos: {e}", nivel=1)


def _renomear_notas_ideal_com_ocr(caminho_pasta_chamado, nome_pasta, area):
    """
    Faz OCR no número de cada nota fiscal baixada (fornecedor IDEAL só
    - a área de OCR é calibrada só pra ele, `eh_pasta_ideal` filtra o
    resto) e já renomeia o arquivo pro número extraído (ex: "NOTA
    FISCAL.pdf" -> "000028305.pdf") na hora do download, em vez de
    deixar pra depois (rodada separada de `executar_leitura_ideal`).

    Isso deixa o número real disponível mais cedo pro resto do fluxo
    (ex: saber qual PDF apagar depois de uma amarração, sem precisar
    adivinhar a partir do nome genérico do anexo) - `pdf_ocr.
    eh_nota_fiscal`/`extrair_notas_do_chamado` já reconhecem arquivos
    nesse formato e pulam o OCR de novo pra eles.

    Falha de OCR (número não encontrado) deixa o arquivo com o nome
    original - a leitura posterior (`executar_leitura_ideal`) tenta de
    novo. `area=None` (arquivo de coordenadas ainda não definido) pula
    esse passo inteiro, sem quebrar o download.
    """
    if area is None or not eh_pasta_ideal(nome_pasta):
        return

    for nome_arquivo in os.listdir(caminho_pasta_chamado):
        if not eh_nota_fiscal(nome_arquivo):
            continue

        caminho_pdf = os.path.join(caminho_pasta_chamado, nome_arquivo)
        try:
            numero = extrair_numero_nota(caminho_pdf, area)
        except Exception as e:
            erro(f"Falha no OCR de '{nome_arquivo}': {e}", nivel=1)
            continue

        if not numero:
            aviso(f"Número não encontrado em '{nome_arquivo}' (OCR no download) - mantendo nome original.", nivel=1)
            continue

        destino = os.path.join(caminho_pasta_chamado, f"{numero}.pdf")
        if os.path.exists(destino):
            aviso(f"'{numero}.pdf' já existe - mantendo '{nome_arquivo}' sem renomear.", nivel=1)
            continue

        os.rename(caminho_pdf, destino)
        ok(f"'{nome_arquivo}' renomeada para '{numero}.pdf' (OCR no download).", nivel=1)


def executar_extracao_glpi(headless=None):
    """
    Fluxo completo do GLPI: abre o navegador (Options compartilhado),
    loga, coleta os chamados com um 'Feito', e para cada um extrai os
    dados, cria a pasta (DENTRO do diretório da amarracao), tira o print
    e baixa os anexos. Retorna a lista de dados dos chamados.

    `headless` vem da opção "GLPI em modo invisível" da tela inicial
    (ver `mostrar_gui_inicial` em amarracao.py); se não vier (`None`),
    `get_chrome_options` cai no padrão global de `browser_config.
    HEADLESS`.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    inicio = datetime.now()
    titulo(f"INÍCIO DO PROCESSO DE AUTOMAÇÃO - GLPI  ({inicio.strftime('%d/%m/%Y %H:%M:%S')})")

    pasta_dia = _pasta_do_dia()

    try:
        area_ocr_ideal = carregar_area(ARQUIVO_AREA)
    except FileNotFoundError as e:
        aviso(f"Área de OCR não definida - pulando renomeação automática das notas IDEAL no download: {e}")
        area_ocr_ideal = None

    driver = webdriver.Chrome(options=get_chrome_options(headless=headless))
    dados_chamados = []
    pastas_criadas = []  # pastas de chamado criadas NESTA rodada

    try:
        secao("PASSO 1 - Login e coleta de chamados")
        a_logar(driver)

        info("Acessando a lista de chamados filtrados...")
        driver.get(GLPI_URL_CHAMADOS)
        chamados = coletar_chamados_com_feito(driver)

        if not chamados:
            aviso("Nenhum chamado foi encontrado com os filtros aplicados.")
            return dados_chamados, pastas_criadas

        # ── MODO DE TESTE ────────────────────────────────────────────
        # Se LIMITE_TESTE_CHAMADOS estiver definido, processa só os N
        # primeiros e ignora o resto. Para voltar a processar TODOS,
        # defina LIMITE_TESTE_CHAMADOS = None no config.py.
        if LIMITE_TESTE_CHAMADOS is not None:
            total_original = len(chamados)
            chamados = chamados[:LIMITE_TESTE_CHAMADOS]
            aviso(
                f"[TESTE] Limitando a {LIMITE_TESTE_CHAMADOS} chamado(s) de {total_original}; "
                "os demais serão ignorados."
            )

        secao("PASSO 2 - Extraindo dados dos chamados")

        for idx, chamado in enumerate(chamados, 1):
            id_chamado = chamado.get('id', 'N/A')
            try:
                url_chamado = chamado['url']
                log(f"\n  📄 [{idx}/{len(chamados)}] Chamado #{id_chamado}")

                driver.get(url_chamado)
                time.sleep(1)

                dados_card = extrair_dados_card_selenium(driver)
                if not dados_card:
                    erro("Erro ao extrair dados do chamado - pulando.", nivel=1)
                    continue

                dados_card['url_original'] = url_chamado
                dados_card['chamado_id'] = id_chamado
                dados_chamados.append(dados_card)

                dados_info = dados_card.get('dados', {})
                vencimento = dados_info.get('data', 'SEM_DATA')
                fornecedor = dados_info.get('fornecedor', 'SEM_FORNECEDOR')
                pc = dados_info.get('numero_pedido', 'SEM_PC')
                nf = dados_info.get('nf', 'SEM_NF')

                nome_pasta_cru = f"{vencimento} - {fornecedor} - {pc} - {nf}"
                nome_pasta_limpo = re.sub(r'[\\/*?:"<>|]', '', nome_pasta_cru).strip()
                caminho_pasta_chamado = os.path.join(pasta_dia, nome_pasta_limpo)
                os.makedirs(caminho_pasta_chamado, exist_ok=True)
                pastas_criadas.append(caminho_pasta_chamado)
                ok(f"Pasta: {nome_pasta_limpo}", nivel=1)

                # Salva id/url do chamado na própria pasta - é o único jeito
                # de recuperar essa informação depois (o nome da pasta não
                # tem o id do chamado, só vencimento/fornecedor/pc/nf), pra
                # quando o fluxo do Protheus precisar voltar no GLPI e
                # responder esse chamado.
                with open(os.path.join(caminho_pasta_chamado, "chamado.json"), "w", encoding="utf-8") as f:
                    json.dump({"chamado_id": id_chamado, "url": url_chamado}, f, ensure_ascii=False)

                # anexos
                _baixar_anexos(driver, caminho_pasta_chamado)

                # OCR nas notas IDEAL já no download - renomeia pro número
                # real da NF, se conseguir ler.
                _renomear_notas_ideal_com_ocr(caminho_pasta_chamado, nome_pasta_limpo, area_ocr_ideal)

                # salva resultados parciais
                _salvar_resultados(dados_chamados)

            except Exception as e:
                erro(f"Erro ao processar chamado {id_chamado}: {e}", nivel=1)

    except Exception as e:
        erro(f"ERRO CRÍTICO DURANTE A EXECUÇÃO: {e}")
    finally:
        driver.quit()
        fim = datetime.now()
        titulo(
            f"FIM DO PROCESSO - GLPI  ({fim.strftime('%d/%m/%Y %H:%M:%S')})  |  "
            f"{len(dados_chamados)} chamado(s) processado(s) em {(fim - inicio).total_seconds():.1f}s"
        )

    return dados_chamados, pastas_criadas
