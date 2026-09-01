import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, WebDriverException

from .waiting import com_contexto_correto, esperar

# Nome interno -> texto exato da coluna na grade (via <th><label>). Lê
# por NOME, não por posição, pra não quebrar se a ordem das colunas
# mudar (a grade de itens da NF tem ~80 colunas, a maioria irrelevante
# pra amarração).
COLUNAS_ITEM = {
    "item": "Item",
    "produto": "Produto",
    "descricao": "Descrição",
    "quantidade": "Quantidade",
    "valor_unitario": "Vlr.Unitario",
    "cfop_xml": "CFOP XML",
}


def _achar_grid(d, data_advpl):
    for grid in d.find_elements(By.CSS_SELECTOR, f'wa-tgetdados[data-advpl="{data_advpl}"]'):
        try:
            if grid.is_displayed():
                return grid
        except Exception:
            continue
    return None


def grade_esta_aberta(driver, data_advpl="msbrgetdbase", timeout=15):
    """
    Confirma que a grade de itens da nota (wa-tgetdados/msbrgetdbase) já
    está renderizada e visível - ou seja, que a tela de edição REALMENTE
    abriu. Serve pra validar depois de clicar em "Alterar"/"Visualizar",
    em vez de só confiar que o clique não lançou exceção (que não
    garante que a tela de fato mudou).
    """
    try:
        com_contexto_correto(driver, lambda d: _achar_grid(d, data_advpl), timeout)
        return True
    except TimeoutException:
        return False


def monitor_esta_aberto(driver, data_advpl="tgrid", timeout=15):
    """
    Confirma que a grade do Monitor (wa-tgrid/tgrid - a LISTA de notas,
    ver `ler_status_nota_monitor`) já voltou a estar renderizada e
    visível - serve pra esperar a tela voltar pro Monitor depois de
    "Confirmar" fechar a dialog "Importador XML - ALTERAR", antes de
    tentar clicar em "Outras Ações" (a da toolbar do Monitor) - sem
    isso, dava pra tentar clicar cedo demais, ainda com a dialog
    aberta (`clicar_botao_browse` não espera nada depois do clique).
    """
    try:
        com_contexto_correto(driver, lambda d: _achar_tgrid(d, data_advpl), timeout)
        return True
    except TimeoutException:
        return False


def ler_itens_nota(driver, data_advpl="msbrgetdbase", timeout=30):
    """
    Lê a grade de itens da nota fiscal (wa-tgetdados/msbrgetdbase) da
    tela de Alterar/Visualizar do Monitor. Devolve uma lista de dicts,
    um por item, na ordem em que aparecem na grade:

        {"item": "0001", "produto": "CVA00B51",
         "descricao": "CIMENTO ASFALTICO...", "quantidade": "1,280000",
         "valor_unitario": "6.091,0000", "status": "verde"}

    Os valores vêm exatamente como o texto exibido na grade (string,
    com vírgula decimal do formato brasileiro) - conversão pra número
    fica por conta de quem for usar o dado, dependendo do que precisar.

    `status` vem da coluna sem título com uma bolinha colorida por
    linha (célula com `class="image-cell"`, cor identificada pelo nome
    do arquivo de imagem de fundo: "br_verde_mdi.png" -> "verde",
    "br_vermelho_mdi.png" -> "vermelho"). É `None` se a linha não tiver
    essa bolinha.

    IMPORTANTE: se a grade tiver mais itens do que cabe na área visível
    (virtualização/scroll), só os itens RENDERIZADOS no momento entram
    aqui - notas com muitos itens podem precisar de scroll antes de
    chamar essa função pra garantir que tudo já está no DOM.
    """
    grid = com_contexto_correto(driver, lambda d: _achar_grid(d, data_advpl), timeout)

    linhas = driver.execute_script(
        """
        const host = arguments[0];
        const root = host.shadowRoot;
        const headers = {};
        root.querySelectorAll('thead th').forEach(th => {
            const label = th.querySelector('label');
            headers[th.id] = label ? label.textContent.trim() : '';
        });
        const linhas = [];
        root.querySelectorAll('tbody tr').forEach(tr => {
            const linha = {};
            tr.querySelectorAll('td').forEach(td => {
                const nome = headers[td.id];
                if (nome === undefined) return;
                const label = td.querySelector('label');
                linha[nome] = label ? label.textContent.trim() : '';
            });
            const bolinha = tr.querySelector('div.image-cell');
            let status = null;
            if (bolinha) {
                const bg = bolinha.style.backgroundImage || '';
                if (bg.includes('verde')) status = 'verde';
                else if (bg.includes('vermelho')) status = 'vermelho';
            }
            linha.__status = status;
            linhas.push(linha);
        });
        return linhas;
        """,
        grid,
    )

    itens = []
    for linha in linhas:
        item = {
            chave: linha.get(nome_coluna, "")
            for chave, nome_coluna in COLUNAS_ITEM.items()
        }
        item["status"] = linha.get("__status")
        itens.append(item)
    return itens


def selecionar_celula(driver, indice_linha, nome_coluna, data_advpl="msbrgetdbase", timeout=30, tentativas=3):
    """
    Clica na célula de uma linha específica da grade de itens, na coluna
    identificada por `nome_coluna` (texto do cabeçalho, ex: "Num.
    Pedido"), selecionando-a - a grade usa selection-mode="cell".

    `indice_linha` é 0-based, na mesma ordem em que as linhas aparecem
    no DOM/são devolvidas por `ler_itens_nota`.

    Relocaliza a grade do zero em cada tentativa: se ela tiver ficado
    stale entre ser localizada e ser usada no `execute_script` (a tela
    re-renderizou nesse meio tempo), tenta de novo em vez de deixar a
    exceção do WebDriver subir e derrubar o fluxo inteiro.
    """
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            grid = com_contexto_correto(driver, lambda d: _achar_grid(d, data_advpl), timeout)

            ok = driver.execute_script(
                """
                const host = arguments[0];
                const indiceLinha = arguments[1];
                const nomeColuna = arguments[2];
                const root = host.shadowRoot;

                const headers = {};
                root.querySelectorAll('thead th').forEach(th => {
                    const label = th.querySelector('label');
                    headers[th.id] = label ? label.textContent.trim() : '';
                });
                let idColuna = null;
                for (const [id, nome] of Object.entries(headers)) {
                    if (nome === nomeColuna) { idColuna = id; break; }
                }
                if (idColuna === null) return false;

                const linhas = root.querySelectorAll('tbody tr');
                const tr = linhas[indiceLinha];
                if (!tr) return false;

                const td = tr.querySelector(`td[id="${idColuna}"]`);
                if (!td) return false;

                td.scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});
                const opts = {bubbles: true, cancelable: true, composed: true, view: window};
                for (const tipo of ['pointerover', 'pointerenter', 'pointerdown', 'pointerup']) {
                    td.dispatchEvent(new PointerEvent(tipo, opts));
                }
                for (const tipo of ['mouseover', 'mousedown', 'mouseup', 'click']) {
                    td.dispatchEvent(new MouseEvent(tipo, opts));
                }
                return true;
                """,
                grid,
                indice_linha,
                nome_coluna,
            )
        except WebDriverException as e:
            ultimo_erro = e
            continue

        if ok:
            return

        ultimo_erro = RuntimeError(
            f"Não foi possível selecionar a célula da coluna '{nome_coluna}' na linha {indice_linha}."
        )

    raise ultimo_erro


def ler_celula(driver, indice_linha, nome_coluna, data_advpl="msbrgetdbase", timeout=15):
    """
    Lê o texto de uma célula específica (linha 0-based + nome da coluna
    do cabeçalho) da grade de itens da nota. Devolve a string (pode ser
    vazia), ou None se a linha/coluna não existir.
    """
    grid = com_contexto_correto(driver, lambda d: _achar_grid(d, data_advpl), timeout)

    return driver.execute_script(
        """
        const host = arguments[0];
        const indiceLinha = arguments[1];
        const nomeColuna = arguments[2];
        const root = host.shadowRoot;

        const headers = {};
        root.querySelectorAll('thead th').forEach(th => {
            const label = th.querySelector('label');
            headers[th.id] = label ? label.textContent.trim() : '';
        });
        let idColuna = null;
        for (const [id, nome] of Object.entries(headers)) {
            if (nome === nomeColuna) { idColuna = id; break; }
        }
        if (idColuna === null) return null;

        const linhas = root.querySelectorAll('tbody tr');
        const tr = linhas[indiceLinha];
        if (!tr) return null;

        const td = tr.querySelector(`td[id="${idColuna}"]`);
        if (!td) return null;
        const label = td.querySelector('label');
        return label ? label.textContent.trim() : '';
        """,
        grid,
        indice_linha,
        nome_coluna,
    )


def aguardar_celula_preenchida(driver, indice_linha, nome_coluna, valor_esperado=None,
                                data_advpl="msbrgetdbase", timeout=15, intervalo=0.5):
    """
    Espera até a célula (linha + coluna) da grade de itens ficar
    preenchida - ou, se `valor_esperado` for passado, até o valor bater
    com ele exatamente. Serve pra confirmar que o vínculo com o pedido
    de compra realmente aplicou na grade depois de clicar "Ok": o
    preenchimento do "Num. Pedido"/"Item PC" NÃO é instantâneo, pode
    levar alguns segundos.

    Devolve o valor final lido. Lança TimeoutException se não preencher
    (ou não bater com `valor_esperado`) dentro de `timeout`.
    """
    fim = time.time() + timeout
    valor = ""
    while time.time() < fim:
        valor = ler_celula(driver, indice_linha, nome_coluna, data_advpl, timeout=5) or ""
        if valor_esperado is not None:
            if valor.strip() == valor_esperado:
                return valor
        elif valor.strip():
            return valor
        time.sleep(intervalo)

    detalhe_esperado = f", esperado '{valor_esperado}'" if valor_esperado is not None else ""
    raise TimeoutException(
        f"Célula '{nome_coluna}' da linha {indice_linha} não preencheu a tempo "
        f"(valor final: '{valor}'{detalhe_esperado}) depois de {timeout}s."
    )


def _achar_tcbrowse(d, data_advpl):
    for grid in d.find_elements(By.CSS_SELECTOR, f'wa-tcbrowse[data-advpl="{data_advpl}"]'):
        try:
            if grid.is_displayed():
                return grid
        except Exception:
            continue
    return None


_JS_LER_LINHAS_VISIVEIS_TCBROWSE = """
    const host = arguments[0];
    const root = host.shadowRoot;
    const headers = {};
    root.querySelectorAll('thead th').forEach(th => {
        const label = th.querySelector('label');
        headers[th.id] = label ? label.textContent.trim() : '';
    });
    const linhas = [];
    root.querySelectorAll('tbody tr').forEach(tr => {
        const linha = {};
        tr.querySelectorAll('td').forEach(td => {
            const nome = headers[td.id];
            if (!nome) return;
            const label = td.querySelector('label');
            linha[nome] = label ? label.textContent.trim() : '';
        });
        linhas.push(linha);
    });
    return linhas;
"""


def _achar_seta_scroll_tcbrowse(grid, seletor):
    """
    Acha a seta pra cima/baixo (`.vcup`/`.vcdown`) do scroll customizado
    (wa-scroll) - elemento SEPARADO, dentro do shadow root do <wa-scroll>
    que por sua vez está dentro do shadow root da própria grade (dois
    níveis de shadow DOM). Devolve o elemento ou None.
    """
    try:
        wa_scroll = grid.shadow_root.find_element(By.CSS_SELECTOR, "wa-scroll")
        return wa_scroll.shadow_root.find_element(By.CSS_SELECTOR, seletor)
    except Exception:
        return None


def _clicar_seta_scroll_tcbrowse(driver, grid, seletor, cliques):
    """
    Clica de verdade (ActionChains, com a janela em foco) na seta de
    scroll `cliques` vezes. Nem `scrollTop` nem eventos de `wheel`
    sintéticos movem essa barra de rolagem customizada - só clique real
    no elemento da seta funciona (mesma lição de `abrir_menu_outras_acoes`:
    esse app às vezes só reage a interação de verdade, não a eventos
    JS genéricos).
    """
    seta = _achar_seta_scroll_tcbrowse(grid, seletor)
    if seta is None:
        return False

    driver.switch_to.window(driver.current_window_handle)
    for _ in range(cliques):
        try:
            ActionChains(driver).move_to_element(seta).click().perform()
        except WebDriverException:
            try:
                seta.click()
            except WebDriverException:
                return False
    return True


def _rolar_tcbrowse_para_topo(driver, grid):
    """Garante que a grade está no topo, clicando na seta pra cima várias vezes (mais que o necessário, de sobra)."""
    _clicar_seta_scroll_tcbrowse(driver, grid, ".vcup", cliques=50)


def _rolar_tcbrowse_uma_pagina(driver, grid):
    """Rola a grade pra baixo, clicando na seta pra baixo algumas vezes."""
    _clicar_seta_scroll_tcbrowse(driver, grid, ".vcdown", cliques=5)


def ler_linhas_tcbrowse(driver, data_advpl="tcbrowse", timeout=15, max_rolagens=40, rodadas_sem_novidade=3):
    """
    Lê TODAS as linhas de uma grade wa-tcbrowse (ex: a lista de PCs
    candidatos na dialog "Vínculo com Pedido de Compra", aberta via
    "Outras Ações" -> "PC (Item)") - componente diferente do
    wa-tgetdados usado pra grade de itens da nota (esse aqui usa
    selection-mode="row": clicar em qualquer célula seleciona a linha
    inteira).

    Essa grade é VIRTUALIZADA: só mantém no DOM as linhas que cabem na
    área visível no momento - o `id` da linha (ex: "row-0") é
    reaproveitado com conteúdo diferente conforme rola, então ler só uma
    vez (sem rolar) perde linhas fora da área visível inicial. Por isso
    rola via evento de `wheel` (ver `_rolar_tcbrowse_uma_pagina`) de cima
    a baixo, acumulando as linhas vistas em cada posição, deduplicando
    pelo CONTEÚDO da linha (não pelo id, que se repete).

    Para de rolar quando `rodadas_sem_novidade` rolagens seguidas não
    trazem nenhuma linha nova (mais confiável que comparar `scrollTop`,
    que pode nem estar refletindo a rolagem de verdade nessa grade).

    Lê TODAS as colunas pelo nome do cabeçalho (sem mapear pra nomes
    internos fixos, já que essa grade muda de colunas conforme a tela).
    Devolve uma lista de dicts, um por linha, na ordem em que foram
    encontradas rolando.
    """
    grid = com_contexto_correto(driver, lambda d: _achar_tcbrowse(d, data_advpl), timeout)
    _rolar_tcbrowse_para_topo(driver, grid)

    vistas = set()
    linhas_unicas = []
    sem_novidade = 0
    for _ in range(max_rolagens):
        novidade = False
        for linha in driver.execute_script(_JS_LER_LINHAS_VISIVEIS_TCBROWSE, grid):
            chave = tuple(sorted(linha.items()))
            if chave not in vistas:
                vistas.add(chave)
                linhas_unicas.append(linha)
                novidade = True

        sem_novidade = 0 if novidade else sem_novidade + 1
        if sem_novidade >= rodadas_sem_novidade:
            break

        _rolar_tcbrowse_uma_pagina(driver, grid)

    return linhas_unicas


_JS_ACHAR_LINHA_TCBROWSE = """
    const host = arguments[0];
    const valores = arguments[1];
    const root = host.shadowRoot;

    const headers = {};
    root.querySelectorAll('thead th').forEach(th => {
        const label = th.querySelector('label');
        headers[th.id] = label ? label.textContent.trim() : '';
    });

    const linhasVistas = [];
    const linhasDom = root.querySelectorAll('tbody tr');
    for (const tr of linhasDom) {
        const linha = {};
        tr.querySelectorAll('td').forEach(td => {
            const nome = headers[td.id];
            if (!nome) return;
            const label = td.querySelector('label');
            linha[nome] = label ? label.textContent.trim() : '';
        });
        linhasVistas.push(linha);

        let bate = true;
        for (const [coluna, valorEsperado] of Object.entries(valores)) {
            if ((linha[coluna] || '').trim() !== valorEsperado) { bate = false; break; }
        }
        if (!bate) continue;

        // Mira SEMPRE na própria linha achada (`tr`), nunca na anterior -
        // ver `selecionar_linha_tcbrowse` pro porquê de não existir mais
        // uma compensação de offset aqui.
        const td0 = tr.querySelector('td');
        if (!td0) continue;

        td0.scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});
        return {alvoTd: td0, linha: linha, vistas: linhasVistas};
    }
    return {alvoTd: null, linha: null, vistas: linhasVistas};
"""


def _achar_linha_marcada_tcbrowse(driver, grid):
    """
    Varre as linhas ATUALMENTE renderizadas da grade (sem rolar) atrás de
    alguma com o checkbox MARCADO ("lbok" no background-image da
    `div.image-cell`), seja ela qual for - não precisa bater com nenhum
    critério de coluna.

    Devolve None se nenhuma linha estiver marcada, ou um dict com:
      - "linha": os dados da linha marcada (coluna -> texto);
      - "tdMarcado": o <td> dessa linha (pra poder DESMARCAR - duplo
        clique de novo alterna o checkbox);
      - "tdAnterior": o <td> da linha logo ACIMA dela no DOM atual, ou
        None se ela já for a primeira linha renderizada.

    Usada tanto pra diagnosticar (depois que a confirmação da linha ALVO
    falha em `selecionar_linha_tcbrowse`, distingue "nenhuma linha
    marcou" de "marcou a linha ERRADA") quanto pra guiar a autocorreção:
    quando marca a linha errada, a estratégia é desmarcar essa linha e
    tentar a linha ANTERIOR a ela (ver `tdAnterior`), não a anterior ao
    alvo original - repetindo até acertar ou desistir.
    """
    return driver.execute_script(
        """
        const host = arguments[0];
        const root = host.shadowRoot;
        const headers = {};
        root.querySelectorAll('thead th').forEach(th => {
            const label = th.querySelector('label');
            headers[th.id] = label ? label.textContent.trim() : '';
        });
        for (const tr of root.querySelectorAll('tbody tr')) {
            const div = tr.querySelector('div.image-cell');
            const bg = div ? (div.style.backgroundImage || '') : '';
            if (!bg.includes('lbok')) continue;
            const linha = {};
            tr.querySelectorAll('td').forEach(td => {
                const nome = headers[td.id];
                if (!nome) return;
                const label = td.querySelector('label');
                linha[nome] = label ? label.textContent.trim() : '';
            });
            let tdAnterior = null;
            if (tr.previousElementSibling && tr.previousElementSibling.tagName === 'TR') {
                tdAnterior = tr.previousElementSibling.querySelector('td');
            }
            return {linha: linha, tdMarcado: tr.querySelector('td'), tdAnterior: tdAnterior};
        }
        return null;
        """,
        grid,
    )


def selecionar_linha_tcbrowse(driver, valores, data_advpl="tcbrowse", timeout=15, max_rolagens=40,
                               rodadas_sem_novidade=3, mostrar_linhas_vistas=False, max_correcoes=5):
    """
    Rola a grade wa-tcbrowse de cima a baixo procurando a PRIMEIRA linha
    cujas colunas batem EXATAMENTE com `valores` (dict coluna -> valor
    esperado, ex: {"Numero PC": "061476", "Item": "0006"}) e clica nela
    assim que encontra.

    A localização (achar a linha certa, dado o scroll atual) acontece
    inteira dentro de uma única chamada de `execute_script`, que devolve
    o <td> alvo como referência de elemento (não um índice) - evita reler
    por índice depois (a grade é virtualizada e pode se re-renderizar
    sozinha entre uma chamada JS e outra).

    Um clique só SELECIONA/destaca a linha (sempre tem alguma linha em
    azul, mesmo sem a gente ter feito nada), mas não marca o checkbox de
    verdade - precisa de DUPLO clique (`ActionChains.double_click`, que
    gera o evento `dblclick` de verdade; dois `.click()` separados não
    bastam, a viagem de rede entre comandos do Selenium não garante o
    timing de duplo clique que o navegador reconhece). O PRIMEIRO clique
    é sempre na própria linha achada (nunca numa vizinha "de propósito").

    MESMO clicando na linha certa, o duplo clique às vezes marca o
    checkbox de uma linha VIZINHA por engano (efeito colateral do próprio
    componente, confirmado ao vivo) - o que significaria vincular o item
    ao PC errado SEM erro nenhum se ninguém conferisse. Por isso, depois
    de CADA clique a função relê a linha pelo MESMO critério (`valores`)
    e confere - via POLLING (`esperar`, até alguns segundos: o navegador
    reconhece o duplo clique mas demora um pouco pra repintar o ícone, e
    checar cedo demais pegava o estado ainda antigo e dava falso alarme
    num vínculo que, na verdade, tinha dado certo) - se ela, e só ela,
    ficou com o checkbox marcado ("...lbok_mdi.png" no background-image
    da `div.image-cell`, mesma convenção de `selecionar_primeira_linha_
    monitor`).

    Se confirmar errado (marcou outra linha, não a `valores`), em vez de
    desistir na hora a função se AUTOCORRIGE, até `max_correcoes` vezes
    (ver `_achar_linha_marcada_tcbrowse`): desmarca (duplo clique de novo
    - alterna o checkbox) a linha que ficou marcada por engano, tenta a
    linha logo ACIMA dela (não a acima do alvo original - o erro pode
    ter se acumulado por mais de uma linha) e confere de novo. Só lança
    RuntimeError se esgotar `max_correcoes` tentativas, se em algum ponto
    não sobrar linha anterior pra tentar (chegou no topo do que está
    renderizado), ou se não conseguir nem desmarcar a linha errada -
    nesses casos fica pro chamador decidir (mostrar a falha, deixar
    tentar de novo), sem arriscar deixar um vínculo errado no ar sozinho.

    Se `mostrar_linhas_vistas=True` (padrão), imprime no console as
    linhas visíveis em cada posição de rolagem - útil pra acompanhar o
    que o algoritmo está enxergando.

    Mesmo critério de parada de `ler_linhas_tcbrowse`: desiste depois de
    `rodadas_sem_novidade` rolagens seguidas sem nenhuma linha nova
    aparecer.

    Devolve a linha (dict) com o checkbox CONFIRMADO marcado, ou None se
    não achar nenhuma linha batendo com `valores` depois de rolar tudo.
    Lança RuntimeError se achou a linha mas não conseguiu confirmar o
    vínculo certo mesmo depois de tentar corrigir.
    """
    grid = com_contexto_correto(driver, lambda d: _achar_tcbrowse(d, data_advpl), timeout)
    _rolar_tcbrowse_para_topo(driver, grid)

    vistas = set()
    sem_novidade = 0
    for pagina in range(max_rolagens):
        resultado = driver.execute_script(_JS_ACHAR_LINHA_TCBROWSE, grid, valores)

        if mostrar_linhas_vistas:
            print(f"     [scroll pág. {pagina}] {len(resultado['vistas'])} linha(s) visível(is): "
                  f"{resultado['vistas']}")

        if resultado["alvoTd"] is not None:
            linha = resultado["linha"]

            # Duplo clique: um clique só SELECIONA/destaca a linha (por
            # isso sempre tem alguma linha em azul, mesmo sem a gente ter
            # feito nada ainda), mas não marca o checkbox de verdade -
            # precisa de duplo clique. Usa ActionChains.double_click, que
            # gera o evento `dblclick` de verdade (dois `.click()`
            # separados não bastam - o navegador só reconhece como duplo
            # clique se as duas batidas acontecerem rápido o suficiente,
            # e a viagem de rede entre comandos do Selenium não garante
            # isso).
            def _checkbox_do_alvo_marcado(d):
                # Relocaliza pelo MESMO critério (`valores`) e olha o
                # background-image do checkbox - não reusa `resultado`
                # antigo porque a grade pode ter se re-renderizado.
                verificacao = d.execute_script(_JS_ACHAR_LINHA_TCBROWSE, grid, valores)
                if verificacao["alvoTd"] is None:
                    return False
                bg = d.execute_script(
                    "const div = arguments[0].querySelector('div.image-cell'); "
                    "return div ? (div.style.backgroundImage || '') : '';",
                    verificacao["alvoTd"],
                )
                return "lbok" in bg

            def _linha_ainda_marcada(d, linha_marcada_antes):
                atual = _achar_linha_marcada_tcbrowse(d, grid)
                return atual is not None and atual["linha"] == linha_marcada_antes

            td_alvo = resultado["alvoTd"]
            correcoes_feitas = 0
            while True:
                ActionChains(driver).double_click(td_alvo).perform()

                # Faz POLLING em vez de checar uma vez só (nem que fosse
                # depois de um sleep fixo): o duplo clique é reconhecido na
                # hora, mas o ícone do checkbox demora um pouco pra ser
                # repintado - checar cedo demais pega o estado ainda antigo
                # e dava falso alarme num vínculo que, na verdade, tinha
                # dado certo (confirmado ao vivo).
                try:
                    esperar(driver, _checkbox_do_alvo_marcado, timeout=5, intervalo=0.3)
                    return linha
                except TimeoutException:
                    pass

                # Não confirmou - antes de desistir, vê se marcou o
                # checkbox de OUTRA linha por engano (bug documentado
                # acima). Se não marcou NADA, não tem o que desmarcar/
                # corrigir - desiste na hora.
                errada = _achar_linha_marcada_tcbrowse(driver, grid)
                if errada is None:
                    raise RuntimeError(
                        f"Cliquei na linha {linha} mas nenhum checkbox confirmou marcado depois "
                        "de esperar - o vínculo desse item não foi confirmado."
                    )

                if correcoes_feitas >= max_correcoes:
                    raise RuntimeError(
                        f"Cliquei pra marcar a linha {linha}, mas depois de {max_correcoes} "
                        f"tentativa(s) de correção a última linha errada marcada continua sendo "
                        f"{errada['linha']} - desisto pra não arriscar deixar um vínculo errado "
                        "no ar."
                    )

                if errada["tdAnterior"] is None:
                    raise RuntimeError(
                        f"Cliquei pra marcar a linha {linha}, mas quem ficou marcada foi a linha "
                        f"errada {errada['linha']}, e não sobra linha anterior renderizada pra "
                        "tentar corrigir (chegou no topo da grade)."
                    )

                correcoes_feitas += 1
                print(f"  Marcou a linha vizinha por engano - corrigindo automaticamente ({correcoes_feitas}/{max_correcoes})...")

                # Desmarca a linha errada (duplo clique de novo alterna o
                # checkbox) e confirma que desmarcou de verdade antes de
                # tentar a próxima - senão a próxima rodada podia achar
                # DUAS linhas marcadas ao mesmo tempo.
                ActionChains(driver).double_click(errada["tdMarcado"]).perform()
                try:
                    esperar(
                        driver,
                        lambda d: not _linha_ainda_marcada(d, errada["linha"]),
                        timeout=5,
                        intervalo=0.3,
                    )
                except TimeoutException:
                    raise RuntimeError(
                        f"Tentei desmarcar a linha errada {errada['linha']} (marcada no lugar de "
                        f"{linha}) pra corrigir, mas ela continuou marcada depois de esperar - "
                        "desisto pra não arriscar deixar um vínculo errado no ar."
                    )

                # Próxima tentativa mira na linha ANTERIOR à que acabou de
                # ser desmarcada - não na anterior ao alvo original, pra
                # continuar subindo se o erro se repetir mais de uma vez.
                td_alvo = errada["tdAnterior"]

        novidade = False
        for linha in resultado["vistas"]:
            chave = tuple(sorted(linha.items()))
            if chave not in vistas:
                vistas.add(chave)
                novidade = True

        sem_novidade = 0 if novidade else sem_novidade + 1
        if sem_novidade >= rodadas_sem_novidade:
            break

        _rolar_tcbrowse_uma_pagina(driver, grid)

    return None


def _achar_tgrid(d, data_advpl):
    for grid in d.find_elements(By.CSS_SELECTOR, f'wa-tgrid[data-advpl="{data_advpl}"]'):
        try:
            if grid.is_displayed():
                return grid
        except Exception:
            continue
    return None


_JS_LOCALIZAR_DIV_CHECKBOX_TGRID = """
    const host = arguments[0];
    const root = host.shadowRoot;
    const tr = root.querySelector('tbody tr');
    if (!tr) return null;
    const td = tr.querySelector('td[id="0"]') || tr.querySelector('td');
    if (!td) return null;
    const div = td.querySelector('div.image-cell');
    if (!div) return null;
    div.scrollIntoView({behavior: 'instant', block: 'center', inline: 'center'});
    return div;
"""

_JS_ESTADO_LINHA_TGRID = """
    const host = arguments[0];
    const root = host.shadowRoot;
    const tr = root.querySelector('tbody tr');
    if (!tr) return null;
    const td = tr.querySelector('td[id="0"]') || tr.querySelector('td');
    if (!td) return null;

    // A coluna 0 é o checkbox de cada linha, mas NÃO é um <input> real -
    // é uma <div class="image-cell"> com o "quadradinho" desenhado via
    // background-image (mesmo padrão de `ler_status_nota_monitor`/
    // `ler_itens_nota`, que leem a bolinha de status pelo nome do
    // arquivo de imagem: "br_verde"/"br_vermelho"). Confirmado direto
    // no DOM: desmarcado usa "...lbno_mdi.png" ("lb" + "NO"), marcado
    // usa "...lbok_mdi.png" ("lb" + "OK") - o <slot> dentro do <td> fica
    // sempre vazio (não existe elemento de checkbox de verdade
    // projetado nele, por isso a tentativa anterior de ler `.checked`
    // nunca achava nada).
    const div = td.querySelector('div.image-cell');
    const bg = div ? (div.style.backgroundImage || '') : '';
    let checkboxChecked = null;
    if (bg.includes('lbok')) checkboxChecked = true;
    else if (bg.includes('lbno')) checkboxChecked = false;

    return {
        checkboxChecked: checkboxChecked,
        backgroundImage: bg,
        trId: tr.id,
        trClasse: tr.className || '',
    };
"""


def _linha_tgrid_parece_selecionada(estado):
    """
    Interpreta o dict devolvido por `_JS_ESTADO_LINHA_TGRID`: True só
    quando a imagem de fundo do checkbox da linha bateu com "lbok"
    (marcado) - "lbno" (desmarcado) ou nenhuma das duas (imagem ainda
    não carregou/nome mudou) contam como False.
    """
    if estado is None:
        return False
    return estado.get("checkboxChecked") is True


def selecionar_primeira_linha_monitor(driver, data_advpl="tgrid", timeout=15, tentativas=5, pausa_entre_cliques=0.6):
    """
    Marca o checkbox da primeira linha da grade do Monitor (wa-tgrid/
    tgrid - a mesma lida por `ler_status_nota_monitor`) pra SELECIONAR
    essa linha.

    Depois de pesquisar a NF (ela vem em primeiro na grade) ou de
    "Confirmar" salvar e a tela voltar pra essa lista, a linha NÃO fica
    selecionada sozinha - sem marcar o checkbox antes, "Outras Ações"
    (toolbar do Monitor) -> "Gerar Docto" agia sobre nada, em vez de
    sobre a nota da linha.

    Confirmado ao vivo: só o DUPLO clique de verdade (`ActionChains.
    double_click`, evento `dblclick`) marca o checkbox aqui - um clique
    único (via ActionChains OU evento JS sintético) foi testado em várias
    rodadas e nunca mudou nada. Por isso essa função só faz duplo clique,
    sem alternar estratégia.

    Clica na `<div class="image-cell">` de dentro do `<td>` (o ícone em
    si), NÃO no `<td>` inteiro - se a div for menor que a célula
    (ícone alinhado a um canto, célula mais larga), um clique no centro
    do `<td>` pode cair fora da área realmente clicável; clicar na div
    garante que o clique acerta o ícone. Cada tentativa relocaliza o
    elemento do zero (a grade pode se re-renderizar entre uma tentativa
    e outra) e RELÊ o estado de verdade do elemento depois de clicar
    (`_JS_ESTADO_LINHA_TGRID`/`_linha_tgrid_parece_selecionada`), em vez
    de assumir que o clique funcionou - só clica de novo se ainda não
    confirmou. Espera `pausa_entre_cliques` antes de conferir - o ícone
    pode demorar um instante pra repintar depois do clique, e checar
    cedo demais faria a próxima tentativa clicar de novo achando que o
    clique atual falhou, quando na verdade só ainda não tinha repintado.

    Devolve True se confirmou a linha selecionada dentro de
    `tentativas`, False se a grade estava vazia OU se esgotou as
    tentativas sem confirmar a seleção.
    """
    grid = com_contexto_correto(driver, lambda d: _achar_tgrid(d, data_advpl), timeout)

    estado_inicial = driver.execute_script(_JS_ESTADO_LINHA_TGRID, grid)
    if estado_inicial is None:
        return False

    for _tentativa in range(1, tentativas + 1):
        div = driver.execute_script(_JS_LOCALIZAR_DIV_CHECKBOX_TGRID, grid)
        if div is None:
            return False

        ActionChains(driver).double_click(div).perform()
        time.sleep(pausa_entre_cliques)

        estado = driver.execute_script(_JS_ESTADO_LINHA_TGRID, grid)
        if _linha_tgrid_parece_selecionada(estado):
            return True

    return False


def ler_status_nota_monitor(driver, nf_busca, data_advpl="tgrid", timeout=15):
    """
    Lê a grade de notas do Monitor (wa-tgrid/tgrid - a LISTA onde se
    pesquisa a NF e clica em "Alterar"; diferente da grade de ITENS de
    uma nota específica, lida por `ler_itens_nota`) e devolve o status
    (bolinha colorida, mesma convenção de `ler_itens_nota`: cor
    identificada pelo nome do arquivo de imagem de fundo - "br_verde_
    mdi.png" -> "verde", "br_vermelho_mdi.png" -> "vermelho",
    "br_preto_mdi.png" -> "preto") da linha cuja coluna do número do
    documento bate com `nf_busca` (9 dígitos, mesmo formato usado em
    `pesquisar`).

    O nome dessa coluna no cabeçalho às vezes vem "Num. Doc" (com
    espaço) e às vezes "Num.Doc" (sem espaço) - confirmado ao vivo que
    isso causava falso negativo (a nota tinha a bolinha VERDE de
    verdade, mas a função devolvia None porque comparava com a grafia
    errada). Por isso os nomes de coluna são normalizados (tira TODO
    espaço em branco) tanto no cabeçalho quanto na hora de comparar -
    "Num. Doc" e "Num.Doc" viram a mesma chave "Num.Doc".

    Confere TODAS as `div.image-cell` da linha, não só a primeira
    (diferente de `ler_itens_nota`) - essa grade tem outra bolinha
    ANTES da de status (coluna 0, sempre a mesma imagem, não muda de
    cor) - pegar só a primeira pegaria a bolinha errada.

    Devolve "verde", "vermelho", "preto" ou None (nota não encontrada na
    grade, ou linha sem bolinha de status).
    """
    grid = com_contexto_correto(driver, lambda d: _achar_tgrid(d, data_advpl), timeout)

    linhas = driver.execute_script(
        """
        const host = arguments[0];
        const root = host.shadowRoot;
        const headers = {};
        root.querySelectorAll('thead th').forEach(th => {
            const label = th.querySelector('label');
            const texto = label ? label.textContent.trim() : '';
            headers[th.id] = texto.replace(/\\s+/g, '');
        });
        const linhas = [];
        root.querySelectorAll('tbody tr').forEach(tr => {
            const linha = {};
            tr.querySelectorAll('td').forEach(td => {
                const nome = headers[td.id];
                if (nome === undefined) return;
                const label = td.querySelector('label');
                linha[nome] = label ? label.textContent.trim() : '';
            });
            let status = null;
            tr.querySelectorAll('div.image-cell').forEach(div => {
                const bg = div.style.backgroundImage || '';
                if (bg.includes('verde')) status = 'verde';
                else if (bg.includes('vermelho')) status = 'vermelho';
                else if (bg.includes('preto')) status = 'preto';
            });
            linha.__status = status;
            linhas.push(linha);
        });
        return linhas;
        """,
        grid,
    )

    for linha in linhas:
        if linha.get("Num.Doc", "").strip() == str(nf_busca):
            return linha.get("__status")
    return None
