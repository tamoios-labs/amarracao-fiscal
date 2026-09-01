import re
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, WebDriverException

from .waiting import com_contexto_correto, clicar_via_eventos_js


def pegar_botao_ok(driver):
    """
    Atravessa os shadow roots aninhados:
      wa-dialog.startParameters (shadow root)
        -> wa-button[part="btn-ok"] (shadow root)
             -> button
    """
    dialog = driver.find_element(By.CSS_SELECTOR, "wa-dialog.startParameters")
    wa_button = dialog.shadow_root.find_element(By.CSS_SELECTOR, 'wa-button[part="btn-ok"]')
    botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
    return botao


def _achar_botao_fechar_aviso(d):
    """
    Só considera dialogs com o atributo `opened` (visíveis de verdade) e
    confirma que o botão "Fechar" está renderizado e visível antes de
    devolvê-lo, pra não clicar num elemento escondido/obsoleto no DOM.
    Devolve (wa_button, button_interno) pra dar pro clique via JS.
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, "wa-dialog.dict-msdialog[opened]"):
        try:
            wa_button = dialog.find_element(By.CSS_SELECTOR, 'wa-button[caption="Fechar"]')
            if not wa_button.shadow_root:
                continue
            botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
            if botao.is_displayed():
                return (wa_button, botao)
        except Exception:
            continue
    return None


def fechar_aviso_resolucao(driver, timeout=15):
    """
    Avisos genéricos (wa-dialog.dict-msdialog com botão "Fechar") que
    aparecem em vários pontos do fluxo (resolução mínima, "Release
    Expirado", "Reforma Tributária", "Importador XML", etc.), com atraso
    variável. Procura em QUALQUER contexto (documento principal ou
    iframes) via com_contexto_correto, faz polling até `timeout`
    segundos; retorna False se nenhum aparecer (é opcional).
    """
    try:
        wa_button, botao = com_contexto_correto(driver, _achar_botao_fechar_aviso, timeout)
    except TimeoutException:
        return False

    clicar_via_eventos_js(driver, botao, wa_button)

    # espera a dialog realmente sumir (animação de fechamento) pra não
    # clicar em outro elemento por baixo do overlay ainda visível.
    # (o driver já está no contexto certo, deixado por com_contexto_correto)
    fim = time.time() + 5
    while time.time() < fim:
        if not driver.find_elements(By.CSS_SELECTOR, "wa-dialog.dict-msdialog[opened]"):
            break
        time.sleep(0.2)

    return True


def fechar_todos_avisos(driver, max_avisos=6, timeout_primeiro=20, timeout_resto=5):
    """
    Vários avisos (ex: "Release Expirado", "Reforma Tributária",
    "Importador XML") podem aparecer em sequência, e o PRIMEIRO costuma
    demorar a surgir (aparece com atraso depois que a tela termina de
    carregar). Por isso esperamos bem mais pelo primeiro (`timeout_
    primeiro`) e menos pelos seguintes (`timeout_resto`). Fecha em loop
    até não sobrar nenhum ou até `max_avisos`.
    """
    fechados = 0
    for i in range(max_avisos):
        timeout = timeout_primeiro if i == 0 else timeout_resto
        if fechar_aviso_resolucao(driver, timeout=timeout):
            fechados += 1
        else:
            break
    return fechados


def _achar_aviso_atencao(d):
    """
    Procura um popup "Atenção" aberto (ex: "Este item já possui vínculo
    com pedido, favor utilizar um item que não possua vínculo com
    nenhum pedido.") - diferente do aviso genérico de
    `fechar_todos_avisos` (botão "Fechar"), esse usa um botão "Ok".
    Devolve (wa_button, botao_interno, texto_da_mensagem) ou None.
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, "wa-dialog[opened]"):
        try:
            if not dialog.find_elements(By.CSS_SELECTOR, 'wa-text-view[caption="Atenção"]'):
                continue
        except Exception:
            continue

        texto = ""
        for tv in dialog.find_elements(By.CSS_SELECTOR, "wa-text-view"):
            try:
                # O caption às vezes vem com tags HTML (ex: "<b>TOTVS
                # Linha Protheus</b>") - sem isso, a mensagem vaza HTML
                # cru pra quem lê o log do console.
                cap = _texto_sem_tags(tv.get_attribute("caption")).strip()
            except Exception:
                continue
            if cap and cap != "Atenção":
                texto = cap
                break

        for wa_button in dialog.find_elements(By.CSS_SELECTOR, "wa-button"):
            try:
                cap = _texto_sem_tags(wa_button.get_attribute("caption")).strip().lower()
                if cap != "ok":
                    continue
                if not wa_button.shadow_root:
                    continue
                botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
                if botao.is_displayed():
                    return (wa_button, botao, texto)
            except Exception:
                continue
    return None


def fechar_aviso_atencao(driver, timeout=3):
    """
    Fecha o popup "Atenção" (ver `_achar_aviso_atencao`) se ele estiver
    aberto - ex: quando se tenta vincular um item da nota que já foi
    amarrado a um pedido antes.

    Devolve o texto da mensagem se o aviso apareceu e foi fechado, ou
    None se não apareceu dentro de `timeout` (não é erro - a maioria das
    vezes esse aviso não aparece).
    """
    try:
        wa_button, botao, texto = com_contexto_correto(driver, _achar_aviso_atencao, timeout)
    except TimeoutException:
        return None

    clicar_via_eventos_js(driver, botao, wa_button)
    return texto or "Aviso 'Atenção' fechado."


def _achar_botao_por_caption(d, alvo):
    """
    Procura, em qualquer wa-dialog[opened], um wa-button cujo caption
    seja `alvo` (já em minúsculas, sem espaços em volta) e que esteja
    visível. Devolve (wa_button, button_interno) ou None.

    O caption de alguns botões de dialog vem com tags `<u>` em volta da
    letra de atalho (ex: "<u>S</u>im", mesmo formato dos tbrowsebutton -
    ver `_texto_sem_tags`) - sem tirar essas tags antes de comparar,
    "sim" nunca batia com "<u>s</u>im" e o botão nunca era achado (bug
    real: "Sim" na dialog "Confirma a geração de documento..." depois de
    "Gerar Docto" nunca foi clicado por causa disso).

    IMPORTANTE: exclui `wa-dialog[data-advpl="twindow"]` da busca - essa
    é a JANELA INTEIRA do TOTVS (não uma dialog de verdade), também tem
    `opened=""`, e como a busca por `wa-button` dentro de cada dialog
    pega TODOS os descendentes (não só diretos), incluir a twindow fazia
    a busca virar, na prática, sem escopo nenhum pela página inteira -
    confirmado ao vivo: pra captions que existem em mais de um lugar da
    tela (ex: "Outras Ações", que tem uma cópia na toolbar do Monitor E
    outra na dialog de edição do item), isso clicava na cópia errada,
    sempre a que aparece primeiro no DOM global, não necessariamente a
    da dialog que a gente queria. Captions que só existem em UM lugar da
    página (ex: "Sim"/"Ok"/"Ignorar" das dialogs de confirmação) nunca
    expuseram esse bug, porque não tinha ambiguidade nenhuma pra errar.
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, 'wa-dialog[opened]:not([data-advpl="twindow"])'):
        for wa_button in dialog.find_elements(By.CSS_SELECTOR, "wa-button"):
            try:
                cap = _texto_sem_tags(wa_button.get_attribute("caption")).strip().lower()
                if cap != alvo:
                    continue
                if not wa_button.shadow_root:
                    continue
                botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
                if botao.is_displayed():
                    return (wa_button, botao)
            except Exception:
                continue
    return None


def clicar_botao_dialog(driver, caption, timeout=30, tentativas=3):
    """
    Clica num wa-button cujo `caption` bate com `caption` (case-
    insensitive), dentro de qualquer wa-dialog[opened] visível, e
    CONFIRMA que o botão sumiu (dialog fechou) depois. Serve pra
    confirmar dialogs de verdade (ex: tela de "Parametros", botão "OK").

    Botões de dialog normais (tbrowsebutton) não ficam clipados e
    reagem melhor ao clique nativo do Selenium do que a eventos JS
    sintéticos - por isso tenta o clique nativo primeiro; se a dialog
    não fechar, tenta de novo e, por fim, cai pro clique via eventos JS.

    Se a página re-renderizar bem no meio do processo (entre localizar
    o botão e clicar), o elemento fica "stale" e TANTO o clique nativo
    QUANTO o fallback via JS falham com StaleElementReferenceException
    (os dois usam a MESMA referência morta) - relocaliza o botão e
    tenta de novo em vez de deixar a exceção subir.
    """
    alvo = caption.strip().lower()

    def relocalizar():
        """
        Volta pro default_content e procura de novo em qualquer contexto
        (com_contexto_correto), em vez de reusar o contexto atual - que
        pode ser exatamente o frame que acabou de ficar inválido/stale.
        Devolve (wa_button, botao) ou None se a dialog já fechou.
        """
        try:
            return com_contexto_correto(driver, lambda d: _achar_botao_por_caption(d, alvo), 5)
        except TimeoutException:
            return None

    wa_button, botao = com_contexto_correto(driver, lambda d: _achar_botao_por_caption(d, alvo), timeout)

    for i in range(tentativas):
        try:
            if i < tentativas - 1:
                botao.click()  # clique nativo (mais fiel pra botão de dialog)
            else:
                clicar_via_eventos_js(driver, botao, wa_button)  # fallback
        except StaleElementReferenceException:
            # elemento (ou o frame inteiro) morreu entre localizar e clicar -
            # a pagina re-renderizou/trocou de contexto no meio do processo.
            achado = relocalizar()
            if achado is None:
                return None  # dialog já fechou sozinha nesse meio tempo
            wa_button, botao = achado
            continue
        except Exception:
            try:
                clicar_via_eventos_js(driver, botao, wa_button)
            except StaleElementReferenceException:
                achado = relocalizar()
                if achado is None:
                    return None
                wa_button, botao = achado
                continue

        # confirma que o botão/dialog sumiu (fechou de verdade)
        fim = time.time() + 5
        while time.time() < fim:
            try:
                if not _achar_botao_por_caption(driver, alvo):
                    return botao
            except StaleElementReferenceException:
                return botao  # frame trocou = a dialog fechou/a tela avançou
            time.sleep(0.3)

        # ainda aberto: relocaliza e tenta de novo
        achado = relocalizar()
        if achado is None:
            return botao
        wa_button, botao = achado

    raise TimeoutException(f"Botão '{caption}' foi clicado mas a dialog não fechou após {tentativas} tentativas.")


def _dialog_divergencia_fiscal_aberta(d):
    """
    True se a dialog "Divergência Fiscal" está aberta - pode aparecer
    depois de confirmar "Gerar Docto", quando os itens importados têm
    divergência fiscal. Identificada pelo `wa-text-view[caption=
    "Divergência Fiscal"]` do cabeçalho, NÃO pelo atributo `title` da
    `wa-dialog` (vem vazio nessa dialog, ao contrário de outras - por
    isso não dá pra usar `dialog_com_titulo_esta_aberta` aqui).
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, "wa-dialog[opened]"):
        try:
            if dialog.find_elements(By.CSS_SELECTOR, 'wa-text-view[caption="Divergência Fiscal"]'):
                return True
        except Exception:
            continue
    return False


def tratar_dialog_divergencia_fiscal(driver, timeout=15):
    """
    Espera até `timeout` segundos pela dialog "Divergência Fiscal" (ver
    `_dialog_divergencia_fiscal_aberta`) e clica em "Ignorar" (segue
    importando o documento mesmo com a divergência - as outras opções
    são "Cancelar", que sai sem gerar nada, e "Conformes", que importa
    só os documentos SEM divergência; nenhuma das duas é o que a
    automação quer aqui) se ela aparecer.

    NÃO é erro ela não aparecer - a maioria das notas não tem
    divergência fiscal, então na maioria das vezes essa função só
    espera os `timeout` segundos e devolve False sem fazer nada, em vez
    de propagar a TimeoutException que `clicar_botao_dialog` sozinho
    lançaria se o botão nunca aparecesse.

    O clique em si reusa `clicar_botao_dialog` (o botão "Ignorar" é um
    `tbrowsebutton` normal, mesma categoria de "Alterar"/"Confirmar") -
    essa função só adiciona a espera tolerante por cima.

    Devolve True se a dialog apareceu e "Ignorar" foi clicado, False se
    não apareceu dentro de `timeout`.
    """
    try:
        com_contexto_correto(driver, _dialog_divergencia_fiscal_aberta, timeout)
    except TimeoutException:
        return False

    clicar_botao_dialog(driver, "Ignorar")
    return True


def _dialog_divergencia_nf_pedido_aberta(d):
    """
    True se a dialog "Divergência NF - Pedido" está aberta - DIFERENTE
    de "Divergência Fiscal" (mensagem e botões diferentes: "Existem
    divergências entre o valor unitário e/ou quantidade da(s) NF(s) com
    o(s) Pedido(s) relacionado(s)", botões "Ok"/"Cancelar"), embora
    possa aparecer no mesmo ponto do fluxo, depois de confirmar "Gerar
    Docto". Identificada pelo `wa-text-view[caption="Divergência NF -
    Pedido"]` do cabeçalho, mesmo padrão de `_dialog_divergencia_fiscal_
    aberta` (o atributo `title` da `wa-dialog` também vem vazio aqui).
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, "wa-dialog[opened]"):
        try:
            if dialog.find_elements(By.CSS_SELECTOR, 'wa-text-view[caption="Divergência NF - Pedido"]'):
                return True
        except Exception:
            continue
    return False


def tratar_dialog_divergencia_nf_pedido(driver, timeout_aparecer=15, tentativas_fechar=6):
    """
    Espera até `timeout_aparecer` segundos pela dialog "Divergência NF -
    Pedido" (ver `_dialog_divergencia_nf_pedido_aberta`) - pode aparecer
    depois de confirmar "Gerar Docto", quando há divergência de valor
    unitário e/ou quantidade entre a NF e o Pedido relacionado.

    Se aparecer, clica em "Ok" (gera o documento COM as divergências -
    a outra opção, "Cancelar", pede pra fazer o ajuste manualmente, o
    que não serve pra automação) via `clicar_botao_dialog`, mas com
    `tentativas_fechar=6` (~30s de espera no total pra CONFIRMAR que a
    dialog fechou de verdade, 5s por tentativa - o padrão de 3
    tentativas de `clicar_botao_dialog`, ~15s, nem sempre é suficiente
    aqui: gerar o documento com divergência registrada pode demorar
    mais que os outros cliques de dialog do fluxo).

    NÃO é erro ela não aparecer - devolve False sem fazer nada nesse
    caso, em vez de propagar a TimeoutException que `clicar_botao_
    dialog` sozinho lançaria se o botão nunca aparecesse.

    Devolve True se a dialog apareceu e "Ok" foi clicado (e confirmado
    fechado dentro do orçamento de `tentativas_fechar`), False se não
    apareceu dentro de `timeout_aparecer`.
    """
    try:
        com_contexto_correto(driver, _dialog_divergencia_nf_pedido_aberta, timeout_aparecer)
    except TimeoutException:
        return False

    clicar_botao_dialog(driver, "Ok", tentativas=tentativas_fechar)
    return True


def _dialog_saldo_pedido_aberta(d):
    """
    True se a dialog "TOTVS" de saldo insuficiente do item do pedido
    está aberta - pode aparecer depois de clicar "Ok" na dialog "Vínculo
    com Pedido de Compra" (ver `selecionar_linha_tcbrowse`), quando a
    quantidade do item do pedido é menor que a quantidade do item da NF
    (mensagem: "Quantidade do pedidoitem: ... é inferior ao item da NF:
    ... Deseja incluir o saldo em uma nova linha?").

    O cabeçalho dessa dialog é só "TOTVS" (genérico demais pra
    identificar sozinho, ao contrário de "Divergência Fiscal"/
    "Divergência NF - Pedido") - por isso identifica pelo corpo da
    mensagem, procurando um trecho ESTÁVEL do texto ("incluir o saldo em
    uma nova linha", que não muda - só o número do pedido/item/NF muda a
    cada vez).
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, 'wa-dialog[opened]:not([data-advpl="twindow"])'):
        for tv in dialog.find_elements(By.CSS_SELECTOR, "wa-text-view"):
            try:
                texto = _texto_sem_tags(tv.get_attribute("caption") or "")
            except Exception:
                continue
            if "incluir o saldo em uma nova linha" in texto:
                return True
    return False


def tratar_dialog_saldo_pedido_insuficiente(driver, timeout_aparecer=10):
    """
    Espera até `timeout_aparecer` segundos pela dialog "TOTVS" de saldo
    insuficiente do item do pedido (ver `_dialog_saldo_pedido_aberta`) -
    pode aparecer depois de clicar "Ok" na dialog "Vínculo com Pedido de
    Compra", quando a quantidade vinculada no pedido é menor que a da
    nota.

    Se aparecer, clica em "Não" (não inclui o saldo numa linha nova -
    decisão da automação: não criar linha extra sozinha sem alguém
    conferir). NÃO é erro ela não aparecer - devolve False sem fazer
    nada nesse caso, em vez de propagar a TimeoutException que `clicar_
    botao_dialog` sozinho lançaria se o botão nunca aparecesse.

    Devolve True se a dialog apareceu e "Não" foi clicado, False se não
    apareceu dentro de `timeout_aparecer`.
    """
    try:
        com_contexto_correto(driver, _dialog_saldo_pedido_aberta, timeout_aparecer)
    except TimeoutException:
        return False

    clicar_botao_dialog(driver, "Não")
    return True


def dialog_com_titulo_esta_aberta(driver, titulo, timeout=2):
    """
    Confere se existe uma `wa-dialog[opened]` com o `title` exato
    (atributo `title` do próprio `<wa-dialog>`, ex: "Vínculo com Pedido
    de Compra") em QUALQUER contexto (documento principal ou iframes).

    Serve como checagem DEFENSIVA antes de abrir uma ação nova (ex:
    "Outras Ações" pro próximo item) - se uma dialog dessas ficou presa
    aberta de uma tentativa anterior (ex: o "Ok" foi clicado mas não
    confirmou o vínculo de verdade), a ação seguinte falha de um jeito
    que não deixa óbvio o motivo real ("'Outras Ações' não abriu", sem
    dizer que já tinha outra dialog no caminho). `timeout` curto (2s
    por padrão): aqui só queremos checar o estado ATUAL, não esperar a
    dialog aparecer.

    Devolve True/False.
    """
    def _achar(d):
        return bool(d.find_elements(By.CSS_SELECTOR, f'wa-dialog[opened][title="{titulo}"]'))

    try:
        return bool(com_contexto_correto(driver, _achar, timeout))
    except TimeoutException:
        return False


def _texto_sem_tags(html):
    """'<u>A</u>lterar' -> 'Alterar'. O caption dos tbrowsebutton vem com <u> em volta da letra de atalho."""
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _achar_botao_browse(d, alvo):
    """
    Procura, em QUALQUER lugar da tela (não só dentro de uma wa-dialog
    aberta, diferente de `_achar_botao_por_caption`), um wa-button
    visível cujo caption - já sem as tags <u> do atalho de teclado -
    bata com `alvo`. Serve pra botões de toolbar de browse/grid
    (tbrowsebutton), como "Alterar", "Visualizar", que ficam na tela
    principal, não numa dialog.
    """
    candidato_oculto = None
    for wa_button in d.find_elements(By.CSS_SELECTOR, "wa-button"):
        try:
            cap = _texto_sem_tags(wa_button.get_attribute("caption")).lower()
            if cap != alvo:
                continue
            if not wa_button.shadow_root:
                continue
            botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
        except Exception:
            continue
        try:
            visivel = botao.is_displayed()
        except Exception:
            visivel = False
        if visivel:
            return (wa_button, botao)
        if candidato_oculto is None:
            candidato_oculto = (wa_button, botao)
    return candidato_oculto


def clicar_botao_browse(driver, caption, timeout=30):
    """
    Clica num wa-button de toolbar de browse/grid (tbrowsebutton) pelo
    caption (ex: "Alterar", "Visualizar") - fora de qualquer wa-dialog,
    diferente de `clicar_botao_dialog`.

    Diferente de `clicar_botao_dialog`, NÃO confirma "o botão sumiu"
    como sinal de sucesso: esse botão de toolbar continua presente no
    DOM mesmo depois de abrir a tela de edição por cima dele (a tela
    nova é um overlay, não substitui a toolbar) - confirmado
    visualmente. Clique nativo primeiro; se lançar exceção, cai pro
    clique via eventos JS.
    """
    alvo = caption.strip().lower()

    wa_button, botao = com_contexto_correto(driver, lambda d: _achar_botao_browse(d, alvo), timeout)

    try:
        botao.click()
    except Exception:
        clicar_via_eventos_js(driver, botao, wa_button)

    return botao


def _menu_popup_aberto(d):
    """Devolve o primeiro wa-menu-popup (tmenu) visível com `active`, ou None."""
    for popup in d.find_elements(By.CSS_SELECTOR, 'wa-menu-popup[data-advpl="tmenu"]'):
        try:
            if popup.get_attribute("active") is None:
                continue
            if popup.is_displayed():
                return popup
        except Exception:
            continue
    return None


def _achar_botao_na_dialog_da_grade_itens(d, alvo):
    """
    Como `_achar_botao_por_caption`, mas escopado especificamente à
    `wa-dialog[opened]` que CONTÉM a grade de itens da nota
    (`wa-tgetdados[data-advpl="msbrgetdbase"]`, a mesma grade lida por
    `ler_itens_nota`/`grade_esta_aberta`) - ou seja, a dialog "Importador
    XML - ALTERAR" de verdade, identificada pelo CONTEÚDO dela, não pela
    ordem em que aparece no DOM.

    Existe porque `_achar_botao_por_caption` (primeiro `wa-dialog[opened]`
    que bate, seja qual for) estava achando e clicando um "Outras Ações"
    ERRADO - confirmado ao vivo: o botão clicado ficava em x=353,y=116,
    bem longe da posição real do botão visível na tela (topo direito da
    dialog, ao lado de "Cancelar"/"Confirmar") - ele nunca abria porque
    não era o botão certo, não por causa de nenhum jeito de clicar
    (várias estratégias de clique foram tentadas sem sucesso antes de
    perceber que o problema era achar o elemento errado, não clicar
    errado). CAUSA RAIZ real (confirmada vendo o body inteiro da
    página): a JANELA INTEIRA do TOTVS também é um `<wa-dialog
    data-advpl="twindow" opened="">` - contém tudo, inclusive a grade de
    itens, então SEM excluir ela explicitamente, mesmo esse filtro por
    conteúdo bateria com ela primeiro e voltaria a buscar sem escopo
    nenhum pela página inteira (por isso o `:not([data-advpl="twindow"])`
    abaixo, mesmo fix aplicado em `_achar_botao_por_caption`).
    """
    for dialog in d.find_elements(By.CSS_SELECTOR, 'wa-dialog[opened]:not([data-advpl="twindow"])'):
        if not dialog.find_elements(By.CSS_SELECTOR, 'wa-tgetdados[data-advpl="msbrgetdbase"]'):
            continue
        for wa_button in dialog.find_elements(By.CSS_SELECTOR, "wa-button"):
            try:
                cap = _texto_sem_tags(wa_button.get_attribute("caption")).strip().lower()
                if cap != alvo:
                    continue
                if not wa_button.shadow_root:
                    continue
                botao = wa_button.shadow_root.find_element(By.CSS_SELECTOR, "button")
                if botao.is_displayed():
                    return (wa_button, botao)
            except Exception:
                continue
    return None


def abrir_menu_outras_acoes(driver, timeout=15, tentativas=3, pausa=1.0):
    """
    Abre o menu "Outras Ações" da tela "Importador XML - ALTERAR" (a
    dialog que abre ao clicar em "Alterar" na NF - onde fica a grade de
    itens), ao lado dos botões "Cancelar"/"Confirmar".

    IMPORTANTE: existe OUTRO botão com o mesmo caption "Outras Ações" na
    toolbar do browse do Monitor (a tela de busca, por fora dessa
    dialog) - são dois botões DIFERENTES. Por isso usa `_achar_botao_na_
    dialog_da_grade_itens` (escopado à dialog que contém a grade de
    itens da nota, identificada pelo CONTEÚDO, não pela ordem no DOM) em
    vez de `_achar_botao_browse` (procura em QUALQUER lugar da página) -
    usar a busca não-escopada corria o risco de achar e clicar no botão
    de fora, que não tem nada a ver com o item selecionado na grade.

    HISTÓRICO (causa raiz real, achada vendo o body inteiro da página em
    live debug): a princípio usava `_achar_botao_por_caption` (escopado
    a `wa-dialog[opened]` genérico, igual `clicar_botao_dialog`), e
    várias tentativas de TROCAR O JEITO DE CLICAR (ActionChains, eventos
    JS, clique nativo) não resolviam nada - o clique sempre "funcionava"
    (sem exceção) mas nenhum popup abria em nenhum contexto da página. Um
    diagnóstico temporário (removido depois de achar a causa) revelou
    que o botão clicado ficava em x=353,y=116 - bem longe da posição
    real do botão na tela (topo direito da dialog). Causa raiz: a JANELA
    INTEIRA do TOTVS também é um `<wa-dialog data-advpl="twindow"
    opened="">`, contém tudo
    (inclusive os dois "Outras Ações"), e como a busca por `wa-button`
    pega TODOS os descendentes, incluir a twindow na varredura fazia a
    busca virar, na prática, sem escopo nenhum - sempre voltava o
    "Outras Ações" que aparece primeiro no DOM GLOBAL (o do Monitor), não
    o da dialog certa. Por isso agora, tanto aqui quanto em `_achar_
    botao_por_caption`, a busca exclui explicitamente `[data-advpl=
    "twindow"]`.

    Checa se o popup (wa-menu-popup) abriu depois de cada tentativa. NÃO
    lança exceção se não abrir - devolve o elemento aberto, ou `None`
    depois de esgotar `tentativas`. Quem chama decide o que fazer (ex:
    pausar pra inspeção manual em vez de derrubar o fluxo).
    """
    alvo = "outras ações"

    def popup_aberto():
        try:
            return com_contexto_correto(driver, _menu_popup_aberto, 3)
        except TimeoutException:
            return None

    for _tentativa in range(1, tentativas + 1):
        try:
            wa_button, botao = com_contexto_correto(
                driver, lambda d: _achar_botao_na_dialog_da_grade_itens(d, alvo), timeout
            )
            driver.switch_to.window(driver.current_window_handle)
            try:
                botao.click()  # clique nativo simples primeiro (mesma ordem de clicar_botao_dialog/clicar_botao_browse)
            except WebDriverException:
                clicar_via_eventos_js(driver, botao, wa_button)  # fallback
        except WebDriverException:
            pass

        time.sleep(pausa)
        popup = popup_aberto()
        if popup:
            return popup

    return None


def abrir_menu_outras_acoes_monitor(driver, timeout=15, tentativas=3, pausa=1.0):
    """
    Abre o menu "Outras Ações" da TOOLBAR do Monitor (a tela de busca/
    lista de notas, fora de qualquer dialog) - ex: depois de
    "Confirmar" salvar a nota e a tela voltar pra essa lista, pra
    clicar em "Gerar Docto" na sequência.

    Mesmo caption "Outras Ações" do menu de dentro da dialog de edição
    (`abrir_menu_outras_acoes`), mas são DOIS botões DIFERENTES - esse
    aqui usa `_achar_botao_browse` (busca em QUALQUER lugar da tela,
    não só dentro de uma `wa-dialog` aberta), igual "Alterar"/
    "Cancelar"/"Confirmar" (`clicar_botao_browse`) - NÃO reusa
    `abrir_menu_outras_acoes` pra não arriscar achar o botão errado
    (ver o aviso no docstring dele).

    Mesma lógica de tentativas/confirmação de popup que
    `abrir_menu_outras_acoes` (confere se o popup abriu de verdade antes
    de devolver) - clique via ActionChains aqui, já que esse botão fica
    numa toolbar comum (não tem o problema de largura/scroll do botão de
    dentro da dialog de edição, que precisou trocar pra `clicar_via_
    eventos_js`).
    """
    alvo = "outras ações"

    def popup_aberto():
        try:
            return com_contexto_correto(driver, _menu_popup_aberto, 3)
        except TimeoutException:
            return None

    for _ in range(tentativas):
        try:
            wa_button, botao = com_contexto_correto(driver, lambda d: _achar_botao_browse(d, alvo), timeout)
            driver.switch_to.window(driver.current_window_handle)
            ActionChains(driver).move_to_element(botao).pause(0.2).click().perform()
        except WebDriverException:
            pass

        time.sleep(pausa)
        popup = popup_aberto()
        if popup:
            return popup

    return None


def _achar_item_menu_popup(d, alvo):
    """
    Procura, dentro de QUALQUER wa-menu-popup aberto, um
    wa-menu-popup-item visível cujo `caption` bata com `alvo`
    (case-insensitive). Devolve (item, span_interno) ou None.

    `alvo` já vem sem as tags <u> do atalho de teclado (ver
    `clicar_item_menu_popup`) - o caption real do item também tem
    essas tags (ex: "<u>G</u>erar Docto" pro "Gerar Docto"), então
    precisa tirar aqui também antes de comparar (`_texto_sem_tags`,
    mesma função usada por `_achar_botao_browse`/`_achar_botao_por_
    caption` pros botões) - sem isso, itens com atalho de teclado
    (a maioria) nunca bateriam com o alvo.
    """
    for item in d.find_elements(By.CSS_SELECTOR, 'wa-menu-popup-item[data-advpl="tmenuitem"]'):
        try:
            cap = _texto_sem_tags(item.get_attribute("caption")).strip().lower()
            if cap != alvo:
                continue
            if not item.shadow_root:
                continue
            span = item.shadow_root.find_element(By.CSS_SELECTOR, "span.caption")
        except Exception:
            continue
        try:
            visivel = item.is_displayed() and span.is_displayed()
        except Exception:
            visivel = False
        if visivel:
            return (item, span)
    return None


def clicar_item_menu_popup(driver, caption, timeout=15):
    """
    Clica num item (wa-menu-popup-item) de um menu popup já aberto (ex:
    pelo `abrir_menu_outras_acoes`) pelo `caption` exato (ex: "PC
    (Item)", "Desvincular"), case-insensitive.
    """
    alvo = caption.strip().lower()
    item, span = com_contexto_correto(driver, lambda d: _achar_item_menu_popup(d, alvo), timeout)
    clicar_via_eventos_js(driver, span, item)
    return item
