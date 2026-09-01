import json
import os
import time
from contextlib import contextmanager

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from browser_config import get_chrome_options
from glpi_automation import executar_extracao_glpi, a_logar
from pdf_ocr import (
    executar_leitura_ideal,
    extrair_notas_do_chamado,
    primeiras_notas_por_chamado,
    todas_notas_ordenadas,
    listar_notas_fiscais,
    extrair_itens_pedido,
    imprimir_itens,
    casar_itens,
    imprimir_amarracao,
    valor_com_1_casa,
    eh_mao_de_obra,
)
from protheus_automation import (
    URL,
    USUARIO,
    SENHA,
    esperar,
    pegar_botao_ok,
    fechar_todos_avisos,
    fechar_aviso_atencao,
    preencher_usuario,
    preencher_senha,
    clicar_entrar,
    clicar_entrar_qualquer_contexto,
    login_teve_sucesso,
    aguardar_carregamento,
    abrir_item_menu,
    clicar_rotina,
    clicar_botao_dialog,
    clicar_botao_browse,
    pesquisar,
    ler_itens_nota,
    selecionar_celula,
    abrir_menu_outras_acoes,
    clicar_item_menu_popup,
    dialog_com_titulo_esta_aberta,
    grade_esta_aberta,
    ler_linhas_tcbrowse,
    selecionar_linha_tcbrowse,
    aguardar_celula_preenchida,
    monitor_esta_aberto,
    ler_status_nota_monitor,
    selecionar_primeira_linha_monitor,
    abrir_menu_outras_acoes_monitor,
    tratar_dialog_divergencia_fiscal,
    tratar_dialog_divergencia_nf_pedido,
    tratar_dialog_saldo_pedido_insuficiente,
)

# Quando True, o clique em "Continuar" (na janela de amarração
# concluída) NÃO clica no botão real de salvar do Protheus - só simula
# e valida como se tivesse salvo. Serve pra testar o resto do fluxo
# (responder o chamado no GLPI) sem arriscar salvar uma nota de
# verdade em produção a cada teste. Pra voltar ao fluxo real, defina
# como False (só depois que o clique real de "Salvar" for ensinado).
SIMULAR_SALVAR_PROTHEUS = False

# Quando True, `fluxo_protheus` NÃO clica em "Alterar" depois de
# pesquisar a NF - em vez disso, tenta marcar o checkbox da primeira
# linha (`selecionar_primeira_linha_monitor`, o passo necessário pra
# "Outras Ações" > "Gerar Docto" agir sobre a nota certa) e para por
# aí. O resto do fluxo até a pesquisa (escolher a pasta, ler a NF por
# OCR, abrir o Monitor, pesquisar) continua rodando normal - só troca o
# que acontece DEPOIS da pesquisa. Existe porque esperar uma amarração
# inteira rodar (abrir a nota, vincular todos os itens, confirmar) só
# pra chegar numa tela onde já dava pra testar o checkbox é lento
# demais. Pra voltar ao fluxo real (vincular de verdade), defina como
# False.
TESTE_CHECKBOX_MONITOR = False


def extrair_numero_pc_da_pasta(pasta):
    """
    Extrai o número do Pedido de Compra (PC) do nome da pasta do
    chamado, no formato "{vencimento} - {fornecedor} - {pc} - {nf}"
    (ver `glpi_automation/executar.py`, onde a pasta é criada com esse
    nome). Separa pelos 2 últimos " - " (não pelo primeiro), pra não
    quebrar se `vencimento`/`fornecedor` também tiverem hífen no meio.

    Devolve None se o nome da pasta não tiver esse formato (ex: menos
    de 4 partes).
    """
    nome = os.path.basename(pasta.rstrip(os.sep).rstrip("/"))
    partes = nome.rsplit(" - ", 2)
    if len(partes) < 3:
        return None
    return partes[-2].strip()


def ler_chamado_da_pasta(pasta):
    """
    Lê o id/url do chamado GLPI salvos por `executar_extracao_glpi` em
    'chamado.json' dentro da pasta do chamado (ver
    glpi_automation/executar.py) - é o único lugar que guarda essa
    informação, já que o nome da pasta só tem vencimento/fornecedor/pc/nf.

    Devolve (chamado_id, url) ou (None, None) se o arquivo não existir
    (ex: pasta escolhida manualmente com a opção "Criar pastas novas do
    GLPI" desmarcada na tela inicial, de uma rodada antiga do GLPI de
    antes desse arquivo existir).
    """
    caminho = os.path.join(pasta, "chamado.json")
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except FileNotFoundError:
        return None, None
    return dados.get("chamado_id"), dados.get("url")


PREFIXO_PASTA_EM_ANDAMENTO = "AMARRANDO - "
PREFIXO_PASTA_CONCLUIDA = "AMARRADA - "


def _atualizar_pasta_amarracao(pasta):
    """
    Chamada logo depois de renomear uma nota pra "AMARRADA - ..." (ver
    `fluxo_protheus`), pra refletir o progresso da pasta inteira no
    próprio nome dela - dá pra ver de fora (sem abrir nada) quais pastas
    já têm alguma nota amarrada e quais já foram concluídas por completo:
      - Ainda sobra nota fiscal pendente na pasta (`listar_notas_fiscais`
        não vazio - conta as ainda não renomeadas): marca/mantém o
        prefixo "AMARRANDO - " (pelo menos uma nota já foi amarrada, mas
        a pasta não terminou).
      - Não sobra mais nenhuma nota fiscal pendente: troca (ou aplica
        direto, se a pasta pulou o estado "AMARRANDO") pro prefixo
        "AMARRADA - " (pasta inteira concluída).

    Não faz nada se a pasta já está no estado certo. Não é fatal: se o
    rename falhar (ex: pasta em uso), só avisa e devolve o caminho
    original - o trabalho que importa (nota já amarrada) não depende
    disso.

    Devolve o caminho ATUAL da pasta (novo se renomeou, o mesmo se não
    mudou nada) - quem chama precisa usar esse valor daí em diante (nome
    de arquivo dentro da pasta, releitura pra retry, etc.), já que o
    caminho antigo deixa de existir depois do rename.
    """
    pasta = pasta.rstrip(os.sep).rstrip("/")
    pai = os.path.dirname(pasta)
    nome_atual = os.path.basename(pasta)

    sobra_pendente = bool(listar_notas_fiscais(pasta))

    if sobra_pendente:
        if nome_atual.startswith((PREFIXO_PASTA_EM_ANDAMENTO, PREFIXO_PASTA_CONCLUIDA)):
            return pasta
        novo_nome = f"{PREFIXO_PASTA_EM_ANDAMENTO}{nome_atual}"
    else:
        if nome_atual.startswith(PREFIXO_PASTA_CONCLUIDA):
            return pasta
        base = nome_atual
        if nome_atual.startswith(PREFIXO_PASTA_EM_ANDAMENTO):
            base = nome_atual[len(PREFIXO_PASTA_EM_ANDAMENTO):]
        novo_nome = f"{PREFIXO_PASTA_CONCLUIDA}{base}"

    novo_caminho = os.path.join(pai, novo_nome)
    try:
        os.rename(pasta, novo_caminho)
        print(f"✔ Pasta renomeada: {novo_nome}")
        return novo_caminho
    except OSError as e:
        print(f"  [AVISO] Não deu pra renomear a pasta pra '{novo_nome}': {e}")
        return pasta


def selecionar_pastas_teste():
    """
    Abre um seletor de pastas (mesmo padrão do tkinter.filedialog usado
    em ler_pdf.py) pra escolher, uma a uma, pastas de chamado já
    baixadas em chamados_glpi/. Repete a seleção até o usuário cancelar
    (fecha o seletor sem escolher pasta), permitindo escolher várias.
    """
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    diretorio_inicial = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chamados_glpi")
    pastas = []
    print("Selecione as pastas de chamado a simular (cancele quando terminar):")
    while True:
        pasta = filedialog.askdirectory(
            title="Selecione uma pasta de chamado (cancelar para terminar)",
            initialdir=diretorio_inicial,
        )
        if not pasta:
            break
        pasta = os.path.normpath(pasta)
        pastas.append(pasta)
        diretorio_inicial = os.path.dirname(pasta)
        print(f"  + {os.path.basename(pasta)}")

    root.destroy()
    return pastas


TEXTO_AJUDA = """\
COMO FUNCIONA ESTE PROGRAMA

Este programa automatiza a conferência e o vínculo ("amarração") das
notas fiscais dos chamados do GLPI com os pedidos de compra no
Protheus. Ele faz a maior parte do trabalho sozinho, mas para nas
etapas mais delicadas para você conferir e confirmar.

1. BUSCA NO GLPI
Com a opção "Criar pastas novas do GLPI agora" marcada, o programa
entra no GLPI, procura os chamados de nota fiscal em aberto e baixa os
anexos (nota fiscal e pedido de compra) em pastas dentro de
"chamados_glpi". Desmarcada, ele pula essa parte e deixa você escolher,
à mão, pastas que já foram baixadas antes.

2. LEITURA DAS NOTAS (OCR)
O programa lê os PDFs das notas fiscais baixadas e descobre sozinho o
número de cada nota, sem precisar digitar nada.

3. PROTHEUS - VÍNCULO AUTOMÁTICO
O programa entra no Protheus, abre o Monitor de Notas Fiscais, procura
a nota pelo número encontrado e vincula automaticamente cada item da
nota ao item correspondente do pedido de compra.

4. TELA "AMARRAÇÃO CONCLUÍDA"
Depois de vincular todos os itens de uma nota, aparece uma tela com o
print da tela e uma tabela comparando os itens da nota com os do
pedido. Você decide:
  - "Continuar": confirma a nota (clica em "Confirmar" no Protheus) e
    segue para o próximo passo.
  - "Cancelar": pula essa nota (não salva nada) e passa para a próxima.

5. APÓS "CONTINUAR" - AUTOMÁTICO, SEM PARAR
Depois de "Continuar", o programa faz tudo sozinho, sem pausar pra
pedir confirmação de nada:
  - Clica em "Confirmar" no Protheus;
  - Marca o checkbox da nota na lista do Monitor;
  - Clica em "Outras Ações" > "Gerar Docto", confirma a geração e trata
    as telas de divergência que às vezes aparecem (segue em frente
    escolhendo a opção que gera o documento mesmo com a divergência);
  - Espera a bolinha da nota, na lista do Monitor, virar VERMELHA -
    é o sinal de que o documento foi gerado de verdade;
  - Responde automaticamente o chamado no GLPI avisando que a nota foi
    amarrada;
  - Renomeia o PDF dessa nota pra "AMARRADA - ..." na pasta (ela já foi
    processada e fica marcada, sem ser apagada);
  - Marca a PASTA do chamado com "AMARRANDO - " no nome assim que a
    primeira nota dela for amarrada (pra indicar de longe, sem abrir
    nada, que já tem progresso ali - quando a ÚLTIMA nota da pasta for
    amarrada, o nome vira "AMARRADA - ");
  - Passa direto pra próxima nota da mesma pasta, se houver.
Se a bolinha não virar vermelha (documento não confirmado), o programa
PARA e mostra a tela de falha, SEM responder o GLPI nem renomear nada -
não é seguro considerar a nota amarrada se o documento não foi gerado.

6. SE ALGO DER ERRADO
Se o programa encontrar um erro em qualquer etapa, ele mostra uma tela
com o print da tela no momento da falha e o que aconteceu, com três
opções:
  - "Cancelar": encerra o programa;
  - "Tentar novamente": reinicia o processo do zero (login, etc.),
    pulando as notas que já foram amarradas com sucesso;
  - "Ir para próxima nota": pula só a nota que estava dando problema
    (sem apagar o PDF dela, pra revisão manual depois) e segue com as
    próximas.

DICAS
  - Não feche a aba do Protheus durante o processo - o programa usa
    ela o tempo todo.
  - Sempre confira as telas de confirmação antes de clicar em
    "Continuar" ou "Ir para próxima nota" - elas existem justamente
    pra você validar antes de qualquer ação definitiva (responder o
    GLPI, renomear arquivos).
"""


def mostrar_gui_inicial():
    """
    Tela inicial do programa - pra quem não mexe em código não precisar
    editar nada no arquivo pra rodar. Tem duas abas:
      - "Início": a opção "Criar pastas novas do GLPI agora" (marcada
        por padrão - ver `TEXTO_AJUDA` pro que cada estado faz), as
        opções de modo invisível (headless) do navegador pra GLPI e pra
        Protheus SEPARADAS (cada sistema abre sua própria sessão de
        browser - ver `responder_chamado_glpi`/`executar_extracao_glpi`
        vs `fluxo_protheus` - então cada um pode rodar visível ou
        escondido independente do outro), e os botões "Cancelar"/
        "Iniciar automação".
      - "Ajuda": o texto de `TEXTO_AJUDA`, explicando cada etapa do
        fluxo em português simples.

    Devolve um dict {"criar_pastas_glpi": bool, "headless_glpi": bool,
    "headless_protheus": bool}, ou None se a janela foi fechada/
    cancelada sem clicar em "Iniciar automação" (nesse caso `main()`
    deve encerrar sem fazer nada).

    Bloqueia até a janela fechar.
    """
    import tkinter as tk
    from tkinter import ttk

    janela = tk.Tk()
    janela.title("Amarração automática de notas fiscais")
    janela.attributes("-topmost", True)
    janela.geometry("640x560")
    janela.minsize(560, 420)

    resultado = {"valor": None}

    def _cancelar():
        resultado["valor"] = None
        janela.destroy()

    janela.protocol("WM_DELETE_WINDOW", _cancelar)

    notebook = ttk.Notebook(janela)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    # ── Aba "Início" ─────────────────────────────────────────────────
    aba_inicio = tk.Frame(notebook)
    notebook.add(aba_inicio, text="Início")

    tk.Label(
        aba_inicio,
        text="Amarração automática de notas fiscais",
        font=("Segoe UI", 14, "bold"),
    ).pack(padx=15, pady=(15, 5), anchor="w")

    tk.Label(
        aba_inicio,
        text=(
            "Confira a opção abaixo antes de começar. Em dúvida, veja a aba "
            "\"Ajuda\" - ou deixe como está (opção recomendada)."
        ),
        justify="left",
        wraplength=580,
    ).pack(padx=15, pady=(0, 15), anchor="w")

    criar_pastas_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        aba_inicio,
        text="Criar pastas novas do GLPI agora (baixar chamados/notas em aberto)",
        variable=criar_pastas_var,
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        justify="left",
        wraplength=580,
    ).pack(padx=15, pady=(0, 5), anchor="w")

    tk.Label(
        aba_inicio,
        text=(
            "Marcada: entra no GLPI, procura chamados novos e baixa "
            "as notas fiscais/pedidos de compra em pastas novas.\n\n"
            "Desmarcada (padrão): NÃO mexe no GLPI - abre uma janela pra você "
            "escolher, à mão, pastas de chamado que já foram baixadas antes (útil "
            "pra reprocessar ou testar sem esperar o GLPI de novo)."
        ),
        justify="left",
        wraplength=560,
        fg="#444444",
    ).pack(padx=35, pady=(0, 10), anchor="w")

    tk.Label(
        aba_inicio,
        text="Modo invisível (headless) do navegador",
        font=("Segoe UI", 10, "bold"),
        anchor="w",
    ).pack(padx=15, pady=(10, 0), anchor="w")

    tk.Label(
        aba_inicio,
        text=(
            "Marcado: o navegador desse sistema roda escondido, sem abrir janela "
            "nenhuma na tela. Desmarcado: abre a janela do navegador de verdade, "
            "pra você acompanhar/conferir o que está acontecendo."
        ),
        justify="left",
        wraplength=560,
        fg="#444444",
    ).pack(padx=15, pady=(0, 8), anchor="w")

    # GLPI headless por padrão (só baixa anexos e responde chamado, não
    # tem tela nenhuma pra conferir de propósito - ver `mostrar_falha`/
    # `mostrar_amarracao_concluida`, que são só do lado do Protheus).
    # Protheus VISÍVEL por padrão - as telas de confirmação mostram
    # SCREENSHOT (não precisam do navegador aberto pra funcionar), mas
    # ver o navegador de verdade rodando ainda ajuda a acompanhar/
    # confiar no que a automação está fazendo, então o padrão continua
    # seguro (visível) - quem já confia no fluxo pode marcar headless.
    headless_glpi_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        aba_inicio,
        text="GLPI em modo invisível (headless)",
        variable=headless_glpi_var,
        anchor="w",
        justify="left",
        wraplength=560,
    ).pack(padx=35, pady=(0, 2), anchor="w")

    headless_protheus_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        aba_inicio,
        text="Protheus em modo invisível (headless)",
        variable=headless_protheus_var,
        anchor="w",
        justify="left",
        wraplength=560,
    ).pack(padx=35, pady=(0, 10), anchor="w")

    def _iniciar():
        resultado["valor"] = {
            "criar_pastas_glpi": criar_pastas_var.get(),
            "headless_glpi": headless_glpi_var.get(),
            "headless_protheus": headless_protheus_var.get(),
        }
        janela.destroy()

    frame_botoes = tk.Frame(aba_inicio)
    frame_botoes.pack(side="bottom", pady=15)
    tk.Button(frame_botoes, text="Cancelar", width=14, command=_cancelar).pack(side="left", padx=5)
    tk.Button(frame_botoes, text="Iniciar automação", width=18, command=_iniciar).pack(side="left", padx=5)

    # ── Aba "Ajuda" ──────────────────────────────────────────────────
    aba_ajuda = tk.Frame(notebook)
    notebook.add(aba_ajuda, text="Ajuda")

    area_scroll = tk.Frame(aba_ajuda)
    area_scroll.pack(fill="both", expand=True, padx=10, pady=10)

    canvas = tk.Canvas(area_scroll, highlightthickness=0)
    scrollbar = tk.Scrollbar(area_scroll, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    conteudo_ajuda = tk.Frame(canvas)
    canvas.create_window((0, 0), window=conteudo_ajuda, anchor="nw")
    conteudo_ajuda.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    # Roda do mouse: Windows manda "delta" em múltiplos de 120.
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    tk.Label(
        conteudo_ajuda,
        text=TEXTO_AJUDA,
        justify="left",
        anchor="w",
        font=("Segoe UI", 10),
    ).pack(padx=5, pady=5, anchor="w")

    janela.mainloop()
    return resultado["valor"]


@contextmanager
def etapa(nome, mostrar=False):
    """
    Cronometra uma etapa TÉCNICA do fluxo. Fica em silêncio quando dá
    certo - o nome de cada micro-etapa interna (ex: "Abrir menu
    'Miscelanea'") não diz nada útil pra quem só está acompanhando o
    andamento (o público desses logs não é técnico); a narrativa que a
    pessoa vê vem dos prints de mais alto nível espalhados pelo fluxo
    ("Abrindo o Monitor...", "Vinculando item 2 de 3...", etc.), não
    daqui. Se falhar, sempre imprime o erro (com o nome técnico, útil
    pra quem for investigar depois) e RELANÇA a exceção - uma etapa que
    falha geralmente inviabiliza as seguintes (ex: sem abrir
    "Miscelanea" não tem como abrir "Totvs Colabora" depois), então não
    faz sentido seguir em frente como se tivesse dado certo. Quem chama
    (`fluxo_protheus`/`main`) é responsável por abandonar a execução e
    reiniciar do zero.

    `mostrar=True` liga um aviso de início pras poucas etapas
    naturalmente demoradas (ex: esperar os avisos de resolução/DPI),
    onde vale indicar que ainda está trabalhando - sem isso, uma espera
    de ~20s no meio de um fluxo silencioso parece travado.
    """
    inicio = time.time()
    if mostrar:
        print(f"  {nome}...")
    try:
        yield
    except Exception as e:
        print(f"  [ERRO] {nome} (após {time.time() - inicio:.2f}s): {type(e).__name__}: {e}")
        raise


def realizar_login(driver, max_tentativas=3):
    """
    Faz o processo inteiro de login (clicar OK inicial, fechar avisos de
    resolução/DPI, preencher usuário/senha, clicar em Entrar) e confirma
    se o Protheus aceitou. Se não aceitar, dá F5 na MESMA página e
    refaz tudo do zero - na prática observada, a primeira tentativa às
    vezes falha por timing/carregamento, mas refresh + repetir tende a
    funcionar.

    Não entra em nenhum iframe antes de preencher usuário/senha: os
    campos (po-login/po-password) ficam no documento principal, não
    dentro do wa-webview - `entrar_iframe_webview` sempre falhava aqui
    (30s desperdiçados por tentativa) porque não existe iframe pra
    entrar nesse ponto do fluxo.

    Devolve True se logou, False se esgotou `max_tentativas`.
    """
    for tentativa in range(1, max_tentativas + 1):
        if tentativa == 1:
            print("Entrando no sistema Protheus...")
        else:
            print(f"Login não foi confirmado, tentando de novo ({tentativa}/{max_tentativas})...")
            driver.refresh()

        with etapa("Clicar botão OK inicial"):
            botao_ok = esperar(driver, pegar_botao_ok)
            botao_ok.click()

        with etapa("Fechar avisos de resolução/DPI", mostrar=True):
            fechar_todos_avisos(driver)

        with etapa("Preencher usuário"):
            preencher_usuario(driver, USUARIO)

        with etapa("Preencher senha"):
            preencher_senha(driver, SENHA)

        with etapa("Clicar em Entrar"):
            clicar_entrar(driver)

        if login_teve_sucesso(driver):
            print("Login confirmado.")
            return True

    print("Não foi possível entrar no sistema depois de várias tentativas.")

    return False


def _salvar_screenshot_falha(driver):
    """
    Salva um screenshot da tela no momento exato de uma falha - pra dar
    pra ver o estado real do browser depois (em vez de só inferir pelo
    texto da exceção), útil sobretudo quando a falha pode ser "a tela
    não é a que o código esperava" e não um erro de timing/seletor.
    """
    try:
        pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "falhas_debug")
        os.makedirs(pasta, exist_ok=True)
        caminho = os.path.join(pasta, f"falha_{time.strftime('%Y%m%d_%H%M%S')}.png")
        driver.save_screenshot(caminho)
        print(f"   [DEBUG] Screenshot da falha salvo em: {caminho}")
    except Exception as e:
        print(f"   [DEBUG] Não deu pra salvar screenshot da falha: {type(e).__name__}: {e}")


def _carregar_screenshot(driver, largura_max=1000):
    """Print atual do driver (`get_screenshot_as_png`) como PIL Image, redimensionado pra caber em `largura_max`."""
    import io

    from PIL import Image

    imagem = Image.open(io.BytesIO(driver.get_screenshot_as_png()))
    if imagem.width > largura_max:
        proporcao = largura_max / imagem.width
        imagem = imagem.resize((largura_max, int(imagem.height * proporcao)))
    return imagem


def mostrar_amarracao_concluida(driver, pares, numero_pc=None, nf=None):
    """
    Mostra, numa janela sempre no topo, o número do pedido de compra e
    da nota fiscal em destaque no topo, seguido do print da tela do
    Protheus no momento em que a amarração terminou e de uma tabela com
    os pares NOTA <-> PEDIDO (`pares`, de `casar_itens`), e dois botões:
    "Continuar" (vai salvar no Protheus, marcar o checkbox da nota no
    Monitor, responder o chamado no GLPI e marcar a nota como amarrada
    na pasta - ver `fluxo_protheus`) e "Cancelar" (vai pular essa nota e
    seguir pra próxima).

    O print + a tabela ficam DENTRO de uma área com scroll (Canvas +
    Scrollbar) com altura limitada à tela - uma nota com muitos itens
    faz a tabela crescer bastante, e sem isso a janela ficava maior
    que a tela e os botões saíam da área visível/clicável. Os botões
    ficam FORA da área de scroll, fixos no rodapé, sempre visíveis.

    Bloqueia até o usuário clicar em um dos dois (ou fechar no X) e
    devolve a escolha: "continuar", "cancelar" ou None (fechou no X).
    """
    import tkinter as tk
    from tkinter import ttk

    from PIL import ImageTk

    imagem = _carregar_screenshot(driver)

    janela = tk.Tk()
    janela.title("Amarração concluída")
    janela.attributes("-topmost", True)

    # Limita a altura da janela à tela disponível (com margem) - o
    # conteúdo pode ser mais alto que isso, mas aí rola em vez de
    # estourar a tela.
    altura_max = max(janela.winfo_screenheight() - 100, 400)
    janela.geometry(f"{min(imagem.width + 40, janela.winfo_screenwidth() - 60)}x{altura_max}")

    area_scroll = tk.Frame(janela)
    area_scroll.pack(fill="both", expand=True)

    canvas = tk.Canvas(area_scroll, highlightthickness=0)
    scrollbar = tk.Scrollbar(area_scroll, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    conteudo = tk.Frame(canvas)
    canvas.create_window((0, 0), window=conteudo, anchor="nw")
    conteudo.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    # Roda do mouse: Windows manda "delta" em múltiplos de 120.
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    tk.Label(
        conteudo,
        text=f"Pedido de Compra: {numero_pc or '(desconhecido)'}    |    Nota Fiscal: {nf or '(desconhecida)'}",
        font=("Segoe UI", 13, "bold"),
        justify="left",
    ).pack(padx=10, pady=(10, 0), anchor="w")

    foto = ImageTk.PhotoImage(imagem, master=janela)
    label_imagem = tk.Label(conteudo, image=foto)
    label_imagem.image = foto  # mantém referência viva (Tkinter não segura sozinho)
    label_imagem.pack()

    colunas = ("origem", "item", "produto", "descricao", "vlr_unit")
    titulos = {
        "origem": "Origem", "item": "Item", "produto": "Produto",
        "descricao": "Descrição", "vlr_unit": "Vlr Unit.",
    }
    tabela = ttk.Treeview(conteudo, columns=colunas, show="headings", height=len(pares) * 2)
    for col in colunas:
        tabela.heading(col, text=titulos[col])
        tabela.column(col, anchor="w", width=220 if col == "descricao" else 130)

    for item_nota, item_pedido, _motivo in pares:
        tabela.insert("", "end", values=(
            "NOTA", item_nota["item"], item_nota["produto"],
            item_nota["descricao"], item_nota["valor_unitario"],
        ))
        tabela.insert("", "end", values=(
            "PEDIDO", item_pedido["item"], item_pedido["produto"],
            item_pedido["descricao"], item_pedido["valor_unitario"],
        ))
    tabela.pack(fill="x", padx=10, pady=10)

    escolha = {"valor": None}

    def _escolher(valor):
        escolha["valor"] = valor
        janela.destroy()

    frame_botoes = tk.Frame(janela)
    frame_botoes.pack(pady=10)
    tk.Button(frame_botoes, text="Cancelar", width=14, command=lambda: _escolher("cancelar")).pack(side="left", padx=5)
    tk.Button(frame_botoes, text="Continuar", width=14, command=lambda: _escolher("continuar")).pack(side="left", padx=5)

    janela.mainloop()
    return escolha["valor"]


def mostrar_falha(driver, erro):
    """
    Mostra, numa janela sempre no topo, o print da tela do Protheus no
    momento exato da falha, com o erro e três botões:
      - "Cancelar": encerra o programa.
      - "Tentar novamente": refaz o fluxo do zero (login, navegação até
        o Monitor, etc.) - mas pulando as notas que já foram amarradas
        nessa pasta, já que o PDF delas já foi renomeado pra "AMARRADA -
        ..." em `fluxo_protheus` (e por isso ignorado por `eh_nota_fiscal`
        numa releitura); ver `main`, que recalcula `notas` a partir do
        que sobrou na pasta antes de chamar `fluxo_protheus` de novo.
      - "Ir para próxima nota": pra quando a MESMA nota fica falhando
        toda hora - pula ela (sem apagar o PDF, ela fica na pasta pra
        revisão manual depois) e refaz o fluxo do zero a partir da
        próxima. Só faz sentido se já havia uma nota em andamento no
        momento da falha (`fluxo_protheus` devolve ela junto do
        resultado); se a falha foi antes disso (ex: no login), cai pra
        o mesmo comportamento de "Tentar novamente".

    Bloqueia até o usuário escolher um dos três botões. Fechar a janela
    no X conta como "cancelar" (mesmo efeito seguro de antes: encerra
    em vez de tentar seguir num estado desconhecido).

    O print + a mensagem de erro ficam DENTRO de uma área com scroll
    (Canvas + Scrollbar) com altura limitada à tela - sem isso, num
    monitor menor a janela nascia mais alta que a tela e os botões
    ficavam fora da área visível/clicável (mesmo problema/mesma
    solução de `mostrar_amarracao_concluida`). Os botões ficam FORA da
    área de scroll, fixos no rodapé, sempre visíveis.

    Devolve "cancelar", "tentar_novamente" ou "pular_nota".
    """
    import tkinter as tk

    from PIL import ImageTk

    imagem = _carregar_screenshot(driver)

    janela = tk.Tk()
    janela.title("Falha na automação")
    janela.attributes("-topmost", True)

    altura_max = max(janela.winfo_screenheight() - 100, 400)
    janela.geometry(f"{min(imagem.width + 40, janela.winfo_screenwidth() - 60)}x{altura_max}")

    area_scroll = tk.Frame(janela)
    area_scroll.pack(fill="both", expand=True)

    canvas = tk.Canvas(area_scroll, highlightthickness=0)
    scrollbar = tk.Scrollbar(area_scroll, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    conteudo = tk.Frame(canvas)
    canvas.create_window((0, 0), window=conteudo, anchor="nw")
    conteudo.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
    # Roda do mouse: Windows manda "delta" em múltiplos de 120.
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

    foto = ImageTk.PhotoImage(imagem, master=janela)
    label_imagem = tk.Label(conteudo, image=foto)
    label_imagem.image = foto  # mantém referência viva (Tkinter não segura sozinho)
    label_imagem.pack()

    tk.Label(
        conteudo,
        text=(
            "A automação falhou.\n\n"
            f"{type(erro).__name__}: {erro}"
        ),
        fg="red",
        font=("Segoe UI", 11, "bold"),
        justify="left",
        wraplength=960,
    ).pack(padx=10, pady=10)

    escolha = {"valor": "cancelar"}

    def _escolher(valor):
        escolha["valor"] = valor
        janela.destroy()

    frame_botoes = tk.Frame(janela)
    frame_botoes.pack(pady=10)
    tk.Button(frame_botoes, text="Cancelar", width=16, command=lambda: _escolher("cancelar")).pack(side="left", padx=5)
    tk.Button(
        frame_botoes, text="Tentar novamente", width=16, command=lambda: _escolher("tentar_novamente")
    ).pack(side="left", padx=5)
    tk.Button(
        frame_botoes, text="Ir para próxima nota", width=18, command=lambda: _escolher("pular_nota")
    ).pack(side="left", padx=5)

    janela.mainloop()
    return escolha["valor"]


def salvar_nota_protheus(driver, simular=SIMULAR_SALVAR_PROTHEUS):
    """
    Clica "Confirmar" (wa-button caption="Confirmar", mesma categoria
    "tbrowsebutton" do "Alterar"/"Cancelar" - reusa `clicar_botao_
    browse`) e para aí, com a tela de volta na grade do Monitor. NÃO
    seleciona a linha nem clica em "Outras Ações" -> "Gerar Docto" -
    isso é feito por `fluxo_protheus` logo depois desta função retornar
    (`selecionar_primeira_linha_monitor` pra marcar o checkbox, depois
    `_gerar_documento_e_confirmar` pra "Outras Ações" -> "Gerar Docto" e
    tudo que vem junto - confirmação, dialogs de divergência, esperar a
    bolinha confirmar vermelha).

    Com `simular=True` (padrão, controlado por `SIMULAR_SALVAR_PROTHEUS`),
    NÃO clica em nada real - só finge que salvou e devolve True. Serve
    pra testar o resto do fluxo (responder o chamado no GLPI) sem
    arriscar salvar uma nota de verdade em produção a cada teste.

    Devolve True se salvou (ou simulou) com sucesso.
    """
    if simular:
        print("  [SIMULAÇÃO] Nota seria salva agora no Protheus (nenhum clique real foi feito).")
        return True

    # `clicar_botao_browse` não espera nada depois do clique - sem
    # confirmar que a dialog "Importador XML - ALTERAR" realmente
    # FECHOU (voltou pro Monitor) antes de seguir, o resto do fluxo
    # (marcar o checkbox da linha no Monitor) podia rodar cedo demais,
    # ainda com a dialog aberta - mesmo problema/mesma solução já usada
    # pro "Alterar" (retry + `grade_esta_aberta`), aqui com
    # `monitor_esta_aberto`. `mostrar=True`: é a etapa mais importante
    # do fluxo, vale deixar visível no log em vez de silenciosa.
    with etapa("Clicar 'Confirmar' e aguardar volta pro Monitor", mostrar=True):
        for _tentativa in range(1, 4):
            clicar_botao_browse(driver, "Confirmar")
            aguardar_carregamento(driver)
            if monitor_esta_aberto(driver, timeout=20):
                break
        else:
            raise RuntimeError("A tela não voltou pro Monitor depois de clicar 'Confirmar'.")

    print("  ✔ Nota confirmada.")

    return True


def responder_chamado_glpi(chamado_id, chamado_url, mensagem, headless=None):
    """
    Abre o chamado no GLPI numa sessão de navegador SEPARADA da do
    Protheus (não reusa `driver` do `fluxo_protheus` - são dois
    sistemas/logins diferentes). `headless` vem da opção "GLPI em modo
    invisível" da tela inicial (ver `mostrar_gui_inicial`); se não
    vier (`None`), `get_chrome_options` cai no padrão global de
    `browser_config.HEADLESS`.

    Clica em "Responder" (abre o formulário de acompanhamento/
    seguimento, colapsado por padrão), preenche `mensagem` no editor
    rich-text (TinyMCE) e clica em "Adicionar" pra postar de verdade.

    O textarea por trás do TinyMCE tem um id gerado dinamicamente a
    cada carregamento da página (ex: "content_1358899138" - muda a
    cada request) - por isso o JS acha o textarea certo por
    `name="content"` DENTRO de #new-ITILFollowup-block e pega o editor
    a partir do id dele (`tinymce.get(...)`), em vez de tentar fixar
    um id que não se repete.
    """
    print(f"Abrindo o chamado {chamado_id} no GLPI...")
    driver_glpi = webdriver.Chrome(options=get_chrome_options(headless=headless))
    try:
        a_logar(driver_glpi)
        driver_glpi.get(chamado_url)

        with etapa("Clicar 'Responder' (abrir formulário de acompanhamento)"):
            botao_responder = WebDriverWait(driver_glpi, 20).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button.answer-action[data-bs-target='#new-ITILFollowup-block']")
                )
            )
            botao_responder.click()

        with etapa("Preencher o texto da resposta"):
            # PRESENCE, não visibility: o TinyMCE deixa esse textarea com
            # display:none de propósito (mostra o editor visual por cima) -
            # esperar por "visível" nunca seria satisfeito e travaria aqui.
            WebDriverWait(driver_glpi, 20).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "#new-ITILFollowup-block textarea[name='content']")
                )
            )
            conteudo_definido = driver_glpi.execute_script(
                """
                var textarea = document.querySelector('#new-ITILFollowup-block textarea[name="content"]');
                var editor = tinymce.get(textarea.id);
                editor.setContent(arguments[0]);
                editor.save();
                return editor.getContent({format: "text"}).trim();
                """,
                mensagem,
            )
            # Confere de verdade que o texto entrou (em vez de confiar que o
            # execute_script não deu erro) - prefere falhar alto a deixar a
            # resposta ir vazia pro "Adicionar" sem ninguém notar.
            if not conteudo_definido:
                raise RuntimeError(
                    "O texto não foi preenchido no editor (TinyMCE segue vazio) - "
                    "algo no seletor ou no id do textarea não bateu."
                )

        with etapa("Clicar 'Adicionar' (postar a resposta)"):
            # `find_element` sozinho não espera nada - o botão já existe no
            # DOM desde o início (só fica fora de área clicável até a
            # animação do collapse/editor assentar), por isso precisa
            # esperar "clicável" de verdade, não só "presente".
            botao_adicionar = WebDriverWait(driver_glpi, 20).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "#new-ITILFollowup-block button[type='submit'][name='add']")
                )
            )
            driver_glpi.execute_script("arguments[0].scrollIntoView({block: 'center'});", botao_adicionar)
            try:
                botao_adicionar.click()
            except (ElementNotInteractableException, ElementClickInterceptedException):
                # Fallback: clique via JS não depende de o elemento estar
                # geometricamente clicável (sem outro elemento sobreposto na
                # frente, tooltip, etc.) nem de estar dentro da área visível
                # de um scroll interno do card - só de existir no DOM. Mesmo
                # padrão já usado em protheus_automation/dialogs.py
                # (`clicar_via_eventos_js`).
                driver_glpi.execute_script("arguments[0].click();", botao_adicionar)

        # Confirma que voltou pra tela do chamado depois do POST (em vez de
        # só confiar que o clique não deu erro) - o form submete de verdade
        # (não é AJAX), então a página navega pra longe e volta.
        with etapa("Confirmar que a resposta foi postada"):
            WebDriverWait(driver_glpi, 20).until(EC.url_contains(f"id={chamado_id}"))

        print(f"✔ Resposta postada no chamado {chamado_id} do GLPI: {mensagem!r}")
    finally:
        driver_glpi.quit()


def _login_e_abrir_monitor(driver):
    """
    Loga no Protheus e navega até a tela do Monitor de Notas Fiscais já
    com os filtros aplicados (pronta pra pesquisar uma NF com
    `pesquisar`) - login (com retry, ver `realizar_login`), tela de
    Boas-vindas/Parâmetros, menu Miscelanea > Totvs Colabora > Monitor >
    Visualizar, fechamento dos avisos que aparecem no meio do caminho, e
    a tela de "Parametros" (filtros) final.

    Lança RuntimeError se o login falhar em todas as tentativas.
    """
    if not realizar_login(driver):
        raise RuntimeError("Login no Protheus falhou em todas as tentativas.")

    with etapa("Clicar 'Entrar' na tela de Boas-vindas/Parâmetros"):
        # Nesse ponto existe MAIS DE UM wa-webview no DOM (o da tela de
        # login antiga + o novo, da tela de confirmação de ambiente) -
        # por isso usa a versão que procura em qualquer contexto, em vez
        # de assumir um iframe fixo.
        driver.switch_to.default_content()
        clicar_entrar_qualquer_contexto(driver)

    with etapa("Aguardar carregamento do ambiente"):
        aguardar_carregamento(driver)

    print("Abrindo o Monitor de Notas Fiscais...")

    with etapa("Abrir menu 'Miscelanea'"):
        abrir_item_menu(driver, "Miscelanea")

    with etapa("Abrir menu 'Totvs Colabora'"):
        abrir_item_menu(driver, "Totvs Colabora", esperar_checked=True)

    with etapa("Abrir 'Monitor'"):
        abrir_item_menu(driver, "Monitor")

    # Clicar em "Monitor" leva pra uma tela intermediária de escolher a
    # ROTINA (ex: "Monitor" com "Visualizar"/"Navegar", "Relação Tp Vs
    # Cfop" com "Visualizar" também - mesmo título repetido em seção
    # diferente). Sem esperar essa tela carregar de verdade antes de
    # procurar "Visualizar", o código corre mais rápido que a troca de
    # tela - `clicar_rotina` podia achar um "Visualizar" de uma seção
    # errada (ou de um resquício da tela anterior) só porque ficou
    # visível primeiro, em vez do da seção "Monitor".
    with etapa("Aguardar tela de escolha de rotina do Monitor"):
        aguardar_carregamento(driver)

    with etapa("Clicar rotina 'Visualizar'"):
        clicar_rotina(driver, "Visualizar")

    # A tela do Monitor carrega (barra de progresso "Dicionário de
    # parametros") antes dos avisos aparecerem.
    with etapa("Aguardar carregamento do Monitor"):
        aguardar_carregamento(driver)

    # Podem aparecer vários avisos em sequência aqui (~4: "Release
    # Expirado", "Reforma Tributária", "Importador XML x TOTVS
    # Transmite", etc.) - fechar_todos_avisos trata qualquer
    # wa-dialog.dict-msdialog com botão "Fechar", genericamente. O
    # primeiro demora a aparecer, por isso espera longa nele - e por
    # isso usa mostrar=True (indica que ainda está trabalhando).
    with etapa("Fechar avisos após o Monitor", mostrar=True):
        fechar_todos_avisos(driver, max_avisos=6, timeout_primeiro=25, timeout_resto=6)

    # Depois de fechar os avisos, pode carregar de novo e/ou surgir mais
    # avisos antes da tela de Parametros (filtros) aparecer.
    with etapa("Etapa intermediária (carregamento + avisos extras)"):
        aguardar_carregamento(driver)
        fechar_todos_avisos(driver, max_avisos=4, timeout_primeiro=6, timeout_resto=4)

    # A tela de "Parametros" (filtros) com o botão "Ok" é a ÚLTIMA a
    # aparecer, só depois de todos os avisos. Timeout generoso porque ela
    # pode demorar a surgir.
    with etapa("Clicar 'OK' da tela de Parametros"):
        clicar_botao_dialog(driver, "OK", timeout=25)

    # O clique no "OK" só confirma que a DIALOG fechou - a grade do
    # Monitor ainda fica alguns instantes aplicando os filtros depois
    # disso. Sem esperar aqui, o campo "Pesquisar" às vezes ainda não
    # está interativo quando o script tenta focar nele ("Não foi
    # possível focar o campo 'Pesquisar'").
    with etapa("Aguardar aplicação dos filtros do Monitor"):
        aguardar_carregamento(driver)


def _gerar_documento_e_confirmar(driver, nf_busca):
    """
    A partir da tela do Monitor com a nota `nf_busca` já selecionada
    (checkbox marcado - ver `selecionar_primeira_linha_monitor`), clica
    "Outras Ações" -> "Gerar Docto", confirma a geração ("Sim") e trata
    as duas dialogs de divergência que podem aparecer (`tratar_dialog_
    divergencia_fiscal`/`tratar_dialog_divergencia_nf_pedido` -
    "Ignorar"/"Ok" respectivamente; nenhuma das duas é erro não
    aparecer, a maioria das notas não tem divergência nenhuma).

    Só considera a geração CONCLUÍDA quando a bolinha de status da nota
    (mesma lida por `ler_status_nota_monitor`, a mesma checada logo
    após a pesquisa em `fluxo_protheus`) vira VERMELHA - ela começa
    VERDE (pendente) e só fica vermelha depois que o documento é
    gerado de verdade; `aguardar_carregamento` sozinho não confirma
    isso, só que a tela parou de carregar.

    Lança RuntimeError se "Outras Ações" não abrir, ou se a bolinha não
    confirmar vermelha dentro do timeout - em nenhum dos dois casos é
    seguro considerar a nota amarrada de verdade (quem chama não deve
    responder o GLPI nem renomear nada se essa função levantar).
    """
    with etapa("Abrir 'Outras Ações' (toolbar do Monitor)"):
        popup = abrir_menu_outras_acoes_monitor(driver)
    if not popup:
        raise RuntimeError("'Outras Ações' (toolbar do Monitor) não abriu depois de marcar o checkbox.")

    with etapa("Clicar 'Gerar Docto'"):
        clicar_item_menu_popup(driver, "Gerar Docto")

    # "Gerar Docto" abre uma dialog "Atenção - Confirma a geração de
    # documento para os itens selecionados?" com botões "Não"/"Sim" -
    # sem confirmar aqui, a geração nem começa.
    with etapa("Confirmar geração do documento ('Sim')"):
        clicar_botao_dialog(driver, "Sim")

    with etapa("Checar dialog 'Divergência Fiscal' (até 15s)", mostrar=True):
        if tratar_dialog_divergencia_fiscal(driver, timeout=15):
            print("  Dialog 'Divergência Fiscal' apareceu - cliquei 'Ignorar'.")

    with etapa("Checar dialog 'Divergência NF - Pedido' (até 15s)", mostrar=True):
        if tratar_dialog_divergencia_nf_pedido(driver, timeout_aparecer=15):
            print("  Dialog 'Divergência NF - Pedido' apareceu - cliquei 'Ok'.")

    with etapa("Aguardar geração do documento", mostrar=True):
        aguardar_carregamento(driver)

    with etapa("Aguardar bolinha da nota virar vermelha (documento gerado)", mostrar=True):
        try:
            esperar(
                driver,
                lambda d: ler_status_nota_monitor(d, nf_busca) == "vermelho",
                timeout=180,
                intervalo=1,
            )
        except TimeoutException:
            status_final = ler_status_nota_monitor(driver, nf_busca)
            raise RuntimeError(
                f"A bolinha da nota {nf_busca} não confirmou vermelha (documento gerado) em 180s "
                f"depois de 'Gerar Docto' (status atual: "
                f"{status_final or '(sem bolinha/não encontrada)'})."
            )

    print(f"  ✔ Bolinha da nota {nf_busca} confirmada vermelha - documento gerado com sucesso.")


def fluxo_protheus(pasta=None, notas=None, pedido_pdf=None, numero_pc=None, chamado_id=None, chamado_url=None,
                    headless_protheus=False, headless_glpi=None):
    inicio_fluxo = time.time()
    # Guarda a nota em andamento no momento de uma falha (setada dentro
    # do `while notas_restantes` abaixo) - o `except` usa pra saber qual
    # nota devolver pro chamador quando o usuário escolhe "Ir para
    # próxima nota" em `mostrar_falha`. None se a falha ocorrer antes de
    # começar a processar qualquer nota (ex: no login).
    nota = None
    # `headless_protheus` vem da tela inicial (ver `mostrar_gui_
    # inicial`), independente do modo escolhido pro GLPI - as telas de
    # confirmação (`mostrar_falha`/`mostrar_amarracao_concluida`) mostram
    # SCREENSHOT, então funcionam mesmo headless, mas o padrão continua
    # visível (`False`) - ver `mostrar_gui_inicial`.
    driver = webdriver.Chrome(options=get_chrome_options(headless=headless_protheus))
    driver.get(URL)

    # Tudo dentro deste try roda com o browser já aberto - qualquer etapa
    # que falhar relança a exceção (ver `etapa`), então o finally garante
    # que o browser sempre fecha antes de propagar o erro pro chamador
    # (main()), que abandona essa execução e reinicia o script do zero.
    try:
        _login_e_abrir_monitor(driver)

        # Com o Monitor aberto, pesquisa o número da nota no campo
        # "Pesquisar". Esse campo exige o número com zeros à esquerda,
        # totalizando 9 dígitos (ex: NF 26622 -> "000026622") - diferente
        # do `numero_busca` usado no GLPI/nome de pasta, que é sem zeros.
        # O ENTER (via ActionChains, dentro de `pesquisar`/`preencher_input`)
        # já dispara a busca de verdade - não clica na lupa.
        notas_restantes = list(notas or [])
        while notas_restantes:
            nota = notas_restantes.pop(0)
            print(f"\n--- Nota fiscal: {nota['arquivo']} (pasta {os.path.basename(pasta or '')}) ---")
            nf = nota["numero_busca"]
            nf_busca = str(nf).zfill(9)
            print(f"Buscando a nota {nf_busca} no Monitor...")
            with etapa(f"Pesquisar NF {nf_busca} no Monitor"):
                pesquisar(driver, nf_busca)

            # A grade não atualiza/destaca o item encontrado instantaneamente
            # depois da busca - aguardar_carregamento não pega isso (é uma
            # atualização leve, sem barra de progresso). Espera curta fixa
            # pra dar tempo da grade assentar antes de clicar em "Alterar".
            time.sleep(2)

            # A primeira nota a aparecer depois da pesquisa precisa estar
            # com a bolinha VERDE - é o estado "pendente, pronta pra
            # processar" (ela só vira VERMELHA depois que o documento é
            # gerado no fim do fluxo, ver `esperar bolinha da nota virar
            # vermelha` mais abaixo). Qualquer outra cor (vermelha - já
            # processada antes, preta - estado que a automação não sabe
            # tratar) ou nem achar a nota (None) significa que não é
            # seguro seguir. Levanta a exceção AQUI, antes de clicar em
            # "Alterar", pra parar o fluxo e mostrar a tela de falha (com
            # screenshot - ver `_salvar_screenshot_falha`/`mostrar_falha`
            # no `except` de `fluxo_protheus`) em vez de seguir tentando
            # abrir/vincular uma nota que provavelmente não vai dar certo.
            status_nota = ler_status_nota_monitor(driver, nf_busca)
            print(f"Status da nota {nf_busca} no Monitor: {status_nota or '(sem bolinha/não encontrada)'}")
            if status_nota != "verde":
                raise RuntimeError(
                    f"A nota {nf_busca} não está com a bolinha VERDE no Monitor (status: "
                    f"{status_nota or '(sem bolinha/não encontrada)'}) - só processamos notas "
                    "verdes (pendentes). Parando antes de abrir/vincular essa nota."
                )

            # Modo de teste (ver `TESTE_CHECKBOX_MONITOR`): pula "Alterar" e
            # o resto da amarração - a nota pesquisada já aparece em
            # primeiro na grade do Monitor, então dá pra testar o checkbox
            # AQUI, sem precisar abrir/vincular/confirmar nada antes.
            if TESTE_CHECKBOX_MONITOR:
                print("[TESTE] Tentando marcar o checkbox da primeira linha do Monitor...")
                marcou = selecionar_primeira_linha_monitor(driver)
                print(f"[TESTE] Resultado: {'✔ marcou' if marcou else '✗ NÃO marcou'} o checkbox.")

                if marcou:
                    _gerar_documento_e_confirmar(driver, nf_busca)

                input("[TESTE] Pressione ENTER para fechar o browser...")
                return "concluido", None, pasta

            # Depois da busca, a grade atualiza e o item da NF procurada
            # aparece em primeiro. Clica em "Alterar" pra abrir esse item -
            # e CONFIRMA que a tela de edição (grade de itens) realmente
            # abriu, em vez de só confiar que o clique não deu erro
            # (que não garante que a tela mudou de verdade).
            with etapa("Clicar em 'Alterar' e confirmar que a tela de edição abriu"):
                for _tentativa in range(1, 4):
                    clicar_botao_browse(driver, "Alterar")
                    aguardar_carregamento(driver)
                    if grade_esta_aberta(driver, timeout=10):
                        break
                else:
                    raise RuntimeError(
                        "Tela de edição (grade de itens) não abriu depois de clicar em 'Alterar'."
                    )
            print("Nota aberta, lendo os itens...")

            with etapa("Ler itens da nota"):
                itens = ler_itens_nota(driver)
                # Itens com a bolinha verde já foram conferidos - ignora
                # (nem print, nem seleção de célula/"Outras Ações" neles).
                pendentes = [(idx, it) for idx, it in enumerate(itens) if it['status'] != 'verde']

            if pendentes:
                print(f"{len(pendentes)} de {len(itens)} item(ns) da nota precisam ser vinculados ao pedido de compra:")
                for _, it in pendentes:
                    print(f"  - {it['descricao']} (item {it['item']})")
            else:
                print(f"Os {len(itens)} item(ns) da nota já estavam conferidos - nada para vincular.")

            # Mostra os itens do PEDIDO.pdf logo em seguida, pra comparar
            # os dois lado a lado - no fim, os dados da nota no Protheus e
            # do pedido de compra (PDF) são usados juntos na amarração.
            # Fica ANTES do loop de debug abaixo (não depende dele).
            pares = []
            if pedido_pdf:
                itens_pedido = extrair_itens_pedido(pedido_pdf)
                imprimir_itens(itens_pedido, "item(ns) no pedido de compra (PEDIDO.pdf)")

                pares = casar_itens(itens, itens_pedido)
                imprimir_amarracao(pares)

            # item da NOTA (pelo número do item) -> item do PEDIDO casado com
            # ele, achado por `casar_itens` - usado pra saber qual "Item"
            # (numeração dentro do PC) procurar na grade de vínculo.
            pedido_por_item_nota = {item_nota["item"]: item_pedido for item_nota, item_pedido, _ in pares}

            total_pendentes = len(pendentes)
            for posicao, (idx, it) in enumerate(pendentes, start=1):
                print(f"\nVinculando item {posicao} de {total_pendentes}: {it['descricao']}...")

                with etapa(f"Selecionar 'Num. Pedido' (item {it['item']})"):
                    selecionar_celula(driver, idx, "Num. Pedido")

                # Checagem defensiva: se a dialog "Vínculo com Pedido de
                # Compra" do item ANTERIOR ficou presa aberta (ex: o "Ok"
                # foi clicado mas não confirmou o vínculo de verdade), o
                # clique em "Outras Ações" desse item falha bloqueado por
                # ela - sem essa checagem, o erro sai como "'Outras Ações'
                # não abriu", que não deixa óbvio que já tinha uma dialog
                # no caminho.
                if dialog_com_titulo_esta_aberta(driver, "Vínculo com Pedido de Compra"):
                    raise RuntimeError(
                        f"A dialog 'Vínculo com Pedido de Compra' de um item anterior ainda está "
                        f"aberta - não vai dar pra abrir 'Outras Ações' pro item {it['item']} "
                        "enquanto ela bloquear a tela (provavelmente o vínculo do item anterior "
                        "não foi confirmado de verdade)."
                    )

                popup = abrir_menu_outras_acoes(driver)
                if not popup:
                    raise RuntimeError(f"'Outras Ações' não abriu pro item {it['item']}.")

                with etapa(f"Clicar 'PC (Item)' no menu (item {it['item']})"):
                    clicar_item_menu_popup(driver, "PC (Item)")

                # Se esse item já tiver vínculo de antes, o Protheus mostra
                # um aviso "Atenção" em vez de abrir a dialog de vínculo -
                # fecha o aviso e pula pro próximo item pendente. Timeout
                # generoso (10s): esse aviso pode demorar a aparecer (tem
                # validação no backend antes de decidir qual tela mostrar).
                aviso = fechar_aviso_atencao(driver, timeout=10)
                if aviso:
                    print("  Esse item já estava vinculado a um pedido - pulando para o próximo.")
                    continue

                # Na dialog "Vínculo com Pedido de Compra", acha a linha que
                # é o "casamento" do item da nota com o item do pedido
                # (achado antes por `casar_itens`): Numero PC (da pasta) +
                # Item (numeração do item DENTRO do PC, que é a mesma do
                # PEDIDO.pdf) juntos identificam a linha - só Numero PC não
                # basta (a numeração de Item se repete entre PCs
                # diferentes). Produto e Prc Unitario entram só como
                # conferência extra, não como filtro principal.
                item_pedido_casado = pedido_por_item_nota.get(it["item"])
                if item_pedido_casado is None:
                    raise RuntimeError(
                        f"Item {it['item']} da nota não tem par casado em 'pares' (casar_itens) - "
                        "não dá pra saber qual Item do PC procurar."
                    )

                # Numero PC + Item são o identificador único de verdade da
                # linha (chave exata, casada em uma ÚNICA chamada JS junto
                # com o clique - ver `selecionar_linha_tcbrowse`). Produto e
                # Prc Unitario são conferidos DEPOIS, como checagem extra,
                # não como parte da busca em si (teriam que tolerar
                # variação de arredondamento, o que não dá pra fazer de
                # forma simples dentro do JS).
                with etapa(f"Marcar checkbox da linha do PC {numero_pc} (item {it['item']})"):
                    linha_clicada = selecionar_linha_tcbrowse(
                        driver,
                        {"Numero PC": numero_pc, "Item": item_pedido_casado["item"]},
                    )
                    if linha_clicada is None:
                        linhas_pc = ler_linhas_tcbrowse(driver)
                        detalhes = "; ".join(str(l) for l in linhas_pc)
                        raise RuntimeError(
                            f"Não achei nenhuma linha com Numero PC='{numero_pc}' e "
                            f"Item='{item_pedido_casado['item']}' pro item {it['item']} "
                            f"(de {len(linhas_pc)} linha(s) na grade). Linhas: {detalhes}"
                        )

                produto_bateu = linha_clicada.get("Produto", "").strip() == it["produto"]
                # Mão de obra: ignora o valor na conferência (mesma exceção
                # de `casar_itens`) - por pedido só existe 1 item de mão de
                # obra, então Numero PC + Item + Produto já garantem que é a
                # linha certa, sem precisar bater o valor unitário.
                if eh_mao_de_obra(it["descricao"]):
                    valor_bateu = True
                else:
                    valor_alvo = valor_com_1_casa(it["valor_unitario"])
                    valor_bateu = (
                        valor_alvo is not None
                        and valor_com_1_casa(linha_clicada.get("Prc Unitario", "")) == valor_alvo
                    )
                if not (produto_bateu and valor_bateu):
                    raise RuntimeError(
                        f"Marquei a linha certa por Numero PC/Item (item {it['item']}), mas a conferência "
                        f"de produto ({'ok' if produto_bateu else 'DIVERGIU'}) ou valor "
                        f"({'ok' if valor_bateu else 'DIVERGIU'}) não bateu. Linha: {linha_clicada}."
                    )

                with etapa(f"Clicar 'Ok' no vínculo com pedido de compra (item {it['item']})"):
                    clicar_botao_dialog(driver, "Ok")

                # Pode aparecer uma dialog "TOTVS" perguntando se quer
                # incluir o saldo numa linha nova, quando a quantidade do
                # item do pedido é menor que a da NF - clica "Não" se
                # aparecer (não é erro não aparecer, a maioria dos itens
                # não mostra isso).
                if tratar_dialog_saldo_pedido_insuficiente(driver, timeout_aparecer=10):
                    print(f"  Dialog de saldo do pedido apareceu pro item {it['item']} - cliquei 'Não'.")

                # Depois do "Ok", o Protheus pode mostrar o aviso "Atenção"
                # (mesmo pro item que acabou de ser vinculado com sucesso)
                # depois de um tempo - até 1 minuto, pelo que já observamos.
                # Espera e fecha se aparecer; se não aparecer a tempo, segue
                # em frente normalmente (nem todo item mostra esse aviso).
                aviso_pos_ok = fechar_aviso_atencao(driver, timeout=60)
                if aviso_pos_ok:
                    print(f"  Aviso do sistema: {aviso_pos_ok}")

                print(f"✔ Item {posicao} de {total_pendentes} vinculado.")

                # DEBUG: pausa antes de seguir pro próximo item (dá tempo do
                # Protheus assentar antes da próxima seleção de célula).
                print("  Aguardando o sistema antes do próximo item (30s)...")
                time.sleep(30)

            if total_pendentes:
                print(f"\n✔ Todos os {total_pendentes} item(ns) pendente(s) foram vinculados.")
            escolha = mostrar_amarracao_concluida(driver, pares, numero_pc=numero_pc, nf=nf)

            if escolha == "cancelar":
                # Botão real do Protheus (wa-button caption="Cancelar",
                # mesma categoria "tbrowsebutton" do "Alterar") - volta pra
                # tela de pesquisa do Monitor, de onde dá pra buscar a
                # próxima nota da mesma pasta sem precisar renavegar pelo
                # menu (Miscelanea > Totvs Colabora > Monitor > Visualizar).
                with etapa("Clicar 'Cancelar' (voltar pra pesquisa)"):
                    clicar_botao_browse(driver, "Cancelar")

                if notas_restantes:
                    continue
                print("Não há mais notas nessa pasta pra processar.")
                break

            if escolha == "continuar":
                if salvar_nota_protheus(driver):
                    # Marca o checkbox da primeira linha do Monitor (a
                    # nota que acabou de ser confirmada, sempre volta em
                    # primeiro) - passo necessário pra "Outras Ações" ->
                    # "Gerar Docto" agir sobre ELA, não sobre nada.
                    with etapa("Marcar checkbox da primeira linha do Monitor"):
                        if not selecionar_primeira_linha_monitor(driver):
                            raise RuntimeError(
                                "Não consegui marcar o checkbox da primeira linha do Monitor "
                                "depois de 'Confirmar' - sem isso, 'Outras Ações' > 'Gerar Docto' "
                                "agiria sobre nada."
                            )

                    # Gera o documento e só devolve quando a bolinha da
                    # nota confirmar VERMELHA (ver `_gerar_documento_e_
                    # confirmar`) - só a partir daqui a amarração está
                    # DE VERDADE concluída. Se essa função lançar, cai
                    # no `except` de fora (mostra a falha) SEM responder
                    # o GLPI nem renomear nada - não é seguro considerar
                    # a nota amarrada se o documento não foi gerado.
                    _gerar_documento_e_confirmar(driver, nf_busca)

                    print(f"✔ Nota amarrada e documento gerado com sucesso. Pedido: {numero_pc} | Nota fiscal: {nf}")

                    if not chamado_id:
                        raise RuntimeError(
                            "Nota amarrada, mas não há chamado_id conhecido pra essa pasta - "
                            "não dá pra responder o GLPI."
                        )
                    mensagem = f"Nota fiscal {nf} amarrada ao pedido de compra {numero_pc} com sucesso."
                    responder_chamado_glpi(chamado_id, chamado_url, mensagem, headless=headless_glpi)

                    # Renomeia (não apaga) só o PDF da nota usada NESSA
                    # amarração, prefixando "AMARRADA - " - outras notas da
                    # mesma pasta (se houver) ficam intactas pra rodadas
                    # futuras. O prefixo tira o arquivo do padrão que
                    # `eh_nota_fiscal` reconhece (ver pdf_ocr/notas_ideal.py),
                    # então ele fica na pasta como registro do que já foi
                    # amarrado, mas é ignorado em qualquer releitura
                    # (`extrair_notas_do_chamado`/retry). Não fatal: se
                    # falhar, só avisa (o trabalho que importa - salvar +
                    # responder o GLPI - já foi feito, não vale a pena
                    # abortar por causa disso).
                    caminho_pdf_usado = os.path.join(pasta, nota["arquivo"])
                    nome_amarrado = f"AMARRADA - {nota['arquivo']}"
                    caminho_amarrado = os.path.join(pasta, nome_amarrado)
                    try:
                        os.replace(caminho_pdf_usado, caminho_amarrado)
                        print(f"✔ PDF da nota renomeado: {nome_amarrado}")
                    except OSError as e:
                        print(f"  [AVISO] Não deu pra renomear '{nota['arquivo']}': {e}")

                    # Reflete o progresso no nome da PASTA em si (ver
                    # `_atualizar_pasta_amarracao`): "AMARRANDO - " enquanto
                    # sobra nota pendente, "AMARRADA - " quando essa foi a
                    # última. `pasta` é reatribuída pro caminho novo - as
                    # próximas notas do loop (e um eventual retry em `main`)
                    # precisam do caminho atual, não do antigo (que deixou
                    # de existir).
                    pasta = _atualizar_pasta_amarracao(pasta)

                    # `pedido_pdf` aponta pro MESMO arquivo, só que dentro
                    # da pasta antiga - sem atualizar aqui também, a
                    # próxima nota do loop (mesma pasta, várias notas)
                    # tentava abrir o PEDIDO.pdf no caminho antigo e dava
                    # FileNotFoundError, já que a pasta acabou de ser
                    # renomeada. Só recalcula se já existia um PEDIDO.pdf
                    # antes (não inventa um pra pasta que nunca teve).
                    if pedido_pdf:
                        pedido_pdf = os.path.join(pasta, os.path.basename(pedido_pdf))

                # "Confirmar" já deixa a tela no Monitor (confirmado por
                # `monitor_esta_aberto` lá em `salvar_nota_protheus`) - dá
                # pra buscar a próxima nota da mesma pasta direto, sem
                # precisar de nenhum clique extra (mesma continuação que
                # "cancelar" já faz).
                if notas_restantes:
                    continue
                print("Não há mais notas nessa pasta pra processar.")
                break

            # Janela fechada no X (nem "continuar" nem "cancelar"): não faz
            # nada e encerra - fica pro usuário decidir de novo depois.
            print(f"Janela fechada sem escolher uma ação ({escolha!r}).")
            break
        print(f"\nFluxo do Protheus concluído em {time.time() - inicio_fluxo:.2f}s.")
        input("Pressione ENTER para fechar o browser...")
        return "concluido", None, pasta
    except Exception as e:
        _salvar_screenshot_falha(driver)
        escolha_falha = "cancelar"
        try:
            escolha_falha = mostrar_falha(driver, e)
        except Exception as e2:
            print(f"   [DEBUG] Não deu pra mostrar a janela de falha: {type(e2).__name__}: {e2}")

        if escolha_falha == "tentar_novamente":
            return "tentar_novamente", None, pasta

        if escolha_falha == "pular_nota":
            if nota is not None:
                print(f"Pulando a nota {nota.get('arquivo')} (falha recorrente) - seguindo pra próxima.")
                return "pular_nota", nota, pasta
            print("Nenhuma nota em andamento no momento da falha - tentando novamente do zero.")
            return "tentar_novamente", None, pasta

        print("Cancelado pelo usuário na janela de falha - encerrando.")
        raise SystemExit(1)
    finally:
        driver.quit()


def main():
    # 0) Tela inicial: deixa quem não mexe em código escolher se quer
    #    criar pastas novas do GLPI agora e se cada sistema (GLPI/
    #    Protheus) roda com o navegador visível ou headless, sem
    #    precisar editar nada no arquivo (ver `mostrar_gui_inicial`).
    opcoes_iniciais = mostrar_gui_inicial()
    if opcoes_iniciais is None:
        print("Cancelado na tela inicial - encerrando sem fazer nada.")
        return

    criar_pastas_glpi = opcoes_iniciais["criar_pastas_glpi"]
    headless_glpi = opcoes_iniciais["headless_glpi"]
    headless_protheus = opcoes_iniciais["headless_protheus"]

    # 1) Extração do GLPI: loga, coleta chamados, cria pastas dos
    #    chamados DENTRO deste diretório (chamados_glpi/) e baixa anexos.
    #    Com a opção desmarcada na tela inicial, pula o GLPI (lento) e
    #    simula `pastas_criadas` com pastas escolhidas manualmente.
    pastas_criadas = []
    if not criar_pastas_glpi:
        pastas_criadas = selecionar_pastas_teste()
        if not pastas_criadas:
            # Sem pasta nenhuma não há o que processar - falha alto aqui
            # em vez de seguir e abrir o browser/logar no Protheus à toa
            # pra um fluxo_protheus(pasta=None, notas=[]) que não faz nada.
            raise RuntimeError(
                "Nenhuma pasta selecionada ('Criar pastas novas do GLPI' desmarcada na tela "
                "inicial) - nada para processar."
            )
        print(f"\n[PASTAS EXISTENTES] {len(pastas_criadas)} pasta(s) selecionada(s) manualmente (GLPI pulado).\n")
    else:
        try:
            dados, pastas_criadas = executar_extracao_glpi(headless=headless_glpi)
            print(f"\nGLPI: {len(dados)} chamado(s) processado(s).\n")
        except Exception as e:
            print(f"Falha na extração do GLPI: {type(e).__name__}: {e}")

    # 2) Leitura OCR: só as pastas IDEAL criadas NESTA rodada do GLPI.
    notas_por_chamado = {}
    try:
        notas_por_chamado = executar_leitura_ideal(pastas=pastas_criadas)
    except Exception as e:
        print(f"Falha na leitura OCR das notas: {type(e).__name__}: {e}")

    # 3) Uma nota por agrupamento é o suficiente pra pesquisa: o pedido
    #    de compra é o mesmo pra todas as notas do chamado.
    primeiras = primeiras_notas_por_chamado(notas_por_chamado)
    if primeiras:
        print("\nPrimeira nota de cada agrupamento (entrada da pesquisa no Protheus):")
        for item in primeiras:
            print(f"  {os.path.basename(item['pasta'])}  ->  NF {item['numero_busca']} ({item['arquivo']})")

    # 4) Automação do Protheus (mesmo Options global do GLPI).
    #    Por enquanto só o primeiro agrupamento (pasta): dentro dele,
    #    `fluxo_protheus` processa TODAS as notas em sequência (avançando
    #    pelo botão 'Cancelar' do Protheus). Quando cobrir mais de um
    #    agrupamento, isto vira um `for item in primeiras`.
    pasta = None
    notas = []
    pedido_pdf = None
    numero_pc = None
    chamado_id = None
    chamado_url = None
    if primeiras:
        pasta = primeiras[0]["pasta"]
        notas = todas_notas_ordenadas(notas_por_chamado.get(pasta, []))

        candidato = os.path.join(pasta, "PEDIDO.pdf")
        if os.path.isfile(candidato):
            pedido_pdf = candidato
        numero_pc = extrair_numero_pc_da_pasta(pasta)
        chamado_id, chamado_url = ler_chamado_da_pasta(pasta)
        if chamado_id:
            print(f"Chamado do GLPI: {chamado_id} ({chamado_url})")
        else:
            print("Chamado do GLPI: não encontrado (pasta sem 'chamado.json').")

    # Notas puladas manualmente (botão "Ir para próxima nota" em
    # mostrar_falha, pra quando a MESMA nota fica falhando toda hora) -
    # identificadas pelo nome do arquivo, já que o PDF delas NÃO é
    # apagado (fica na pasta pra revisão manual depois), então precisam
    # ser filtradas à parte das que já foram amarradas de verdade.
    notas_ignoradas = set()

    resultado, nota_falha, pasta = fluxo_protheus(
        pasta, notas, pedido_pdf, numero_pc, chamado_id, chamado_url,
        headless_protheus=headless_protheus, headless_glpi=headless_glpi,
    )
    while resultado in ("tentar_novamente", "pular_nota"):
        if resultado == "pular_nota" and nota_falha:
            notas_ignoradas.add(nota_falha["arquivo"])
            print(f"\n=== Pulando '{nota_falha['arquivo']}' - seguindo pra próxima nota ===")
        else:
            print("\n=== Tentando novamente ===")
        # Refaz o fluxo do zero (login, navegação, etc.), mas recalcula
        # `notas` a partir do que ainda sobrou na pasta: as que já foram
        # amarradas tiveram o PDF renomeado pra "AMARRADA - ..." (ver
        # `fluxo_protheus`), então `eh_nota_fiscal` já as ignora e elas
        # saem de fora sozinhas na releitura, na mesma ordem de antes - sem
        # precisar guardar em que nota a execução anterior parou. As
        # puladas manualmente (`notas_ignoradas`) saem fora também. `pasta`
        # já vem atualizada de `fluxo_protheus` (pode ter sido renomeada
        # pra "AMARRANDO - ..."/"AMARRADA - ..." no meio da execução
        # anterior - ver `_atualizar_pasta_amarracao`), então lê do
        # caminho atual, não do original.
        if pasta:
            notas = [
                n for n in todas_notas_ordenadas(extrair_notas_do_chamado(pasta))
                if n["arquivo"] not in notas_ignoradas
            ]
            # Mesmo motivo do fix dentro de `fluxo_protheus`: `pasta` pode
            # ter sido renomeada ("AMARRANDO - "/"AMARRADA - ") desde que
            # `pedido_pdf` foi calculado a primeira vez - sem recalcular
            # aqui também, o retry abriria o PEDIDO.pdf no caminho antigo
            # e dava FileNotFoundError.
            if pedido_pdf:
                pedido_pdf = os.path.join(pasta, os.path.basename(pedido_pdf))
        resultado, nota_falha, pasta = fluxo_protheus(
        pasta, notas, pedido_pdf, numero_pc, chamado_id, chamado_url,
        headless_protheus=headless_protheus, headless_glpi=headless_glpi,
    )


if __name__ == "__main__":
    main()
