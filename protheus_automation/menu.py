from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

from .waiting import com_contexto_correto, clicar_via_eventos_js


def _achar_item_menu(d, texto):
    """
    Procura o item de menu pelo texto. O Protheus deixa itens de menu
    antigos/ocultos no DOM, então priorizamos os que estão de fato
    visíveis — clicar num item invisível não faz nada e dava a impressão
    de que o clique "não funcionava".
    """
    candidato_oculto = None
    for item in d.find_elements(By.CSS_SELECTOR, "wa-menu-item"):
        try:
            root = item.shadow_root
            if not root:
                continue
            span = root.find_element(By.CSS_SELECTOR, "span.caption")
            titulo = (span.get_attribute("title") or "").strip()
        except Exception:
            continue
        if not titulo.lower().startswith(texto.lower()):
            continue
        try:
            visivel = item.is_displayed() and span.is_displayed()
        except Exception:
            visivel = False
        if visivel:
            return (item, span)
        if candidato_oculto is None:
            candidato_oculto = (item, span)
    return candidato_oculto


def abrir_item_menu(driver, texto, timeout=30, esperar_checked=False, tentativas=4):
    """
    Clica num item do menu lateral do Protheus (wa-menu-item) pelo texto
    visível (ex: "Miscelanea", "Totvs Colabora"). Usa o `title` do
    <span class="caption"> dentro do shadow root do item — vem sem as
    tags <u> de atalho que o atributo `caption` do elemento externo
    carrega, e casa por prefixo pra não quebrar se o número entre
    parênteses (contagem) mudar.

    `esperar_checked=True` é pra itens que EXPANDEM um sub-menu em vez
    de só navegar/executar uma ação (ex: "Totvs Colabora", que revela
    "Monitor" como filho quando expande, ganhando a classe "checked" no
    host). Nesse modo, confirma que o host ganhou "checked" depois do
    clique; se não (o clique pode não registrar se o item estava fora da
    área visível ou o shadow DOM ainda não montou), tenta de novo até
    `tentativas` vezes, e LANÇA TimeoutException se nunca abrir (pra não
    reportar sucesso falso).
    """
    host, span = com_contexto_correto(driver, lambda d: _achar_item_menu(d, texto), timeout)

    if not esperar_checked:
        clicar_via_eventos_js(driver, span, host)
        return span

    if "checked" in (host.get_attribute("class") or ""):
        return span

    for _ in range(tentativas):
        try:
            clicar_via_eventos_js(driver, span, host)
            WebDriverWait(driver, 3, poll_frequency=0.2).until(
                lambda d: "checked" in (host.get_attribute("class") or "")
            )
            return span
        except (TimeoutException, StaleElementReferenceException):
            host, span = com_contexto_correto(driver, lambda d: _achar_item_menu(d, texto), timeout)

    raise TimeoutException(
        f"Item de menu '{texto}' não expandiu (classe 'checked' não apareceu) após {tentativas} tentativas."
    )


def clicar_rotina(driver, titulo, timeout=30):
    """
    Clica numa opção da tela de rotina do Monitor (ex: "Visualizar",
    "Alterar", "Excluir"). São wa-text-view com o atributo `title` igual
    ao texto da opção (o caption vem com "• " na frente). Casa pelo
    `title`, só considera os visíveis, e clica via eventos JS (shadow DOM).
    """

    def achar(d):
        candidato_oculto = None
        for tv in d.find_elements(By.CSS_SELECTOR, "wa-text-view[title]"):
            try:
                if (tv.get_attribute("title") or "").strip().lower() != titulo.strip().lower():
                    continue
                root = tv.shadow_root
                label = root.find_element(By.CSS_SELECTOR, "label") if root else None
            except Exception:
                continue
            try:
                visivel = tv.is_displayed()
            except Exception:
                visivel = False
            if visivel:
                return (tv, label)
            if candidato_oculto is None:
                candidato_oculto = (tv, label)
        return candidato_oculto

    tv, label = com_contexto_correto(driver, achar, timeout)
    clicar_via_eventos_js(driver, label, tv)
    return tv
