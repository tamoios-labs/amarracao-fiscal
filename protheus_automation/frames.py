import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException

from .waiting import esperar


def entrar_iframe_webview(driver, timeout=30):
    """
    A tela de login (po-password etc.) fica dentro de um <iframe> que
    está dentro do shadow root do componente <wa-webview> — a janela
    externa "TOTVS Serviços" hospeda o app real via iframe. Sem trocar
    de contexto com switch_to.frame, o Selenium nunca acha esses
    elementos, não importa quanto tempo se espere.
    """

    def achar_iframe(d):
        try:
            webview = d.find_element(By.CSS_SELECTOR, "wa-webview")
            if not webview.shadow_root:
                return None
            return webview.shadow_root.find_element(By.CSS_SELECTOR, "iframe")
        except Exception:
            return None

    iframe = esperar(driver, achar_iframe, timeout)
    driver.switch_to.frame(iframe)


def aguardar_carregamento(driver, timeout=90, espera_inicial=2):
    """
    Depois do login, o Protheus mostra uma barra de progresso
    ("Aguarde para utilizar o TOTVS Linha Protheus" / wa-meter) enquanto
    carrega o ambiente. Espera ela sumir antes de mexer no menu.

    Antes de concluir "carregado", dá uma folga curta (`espera_inicial`)
    checando se o meter chega a aparecer: sem isso, "nenhum meter
    encontrado" no instante exato da chamada era tratado como
    "carregamento concluído" mesmo quando na verdade ainda nem tinha
    começado - falso positivo que fazia o próximo passo (ex: abrir o
    menu) rodar cedo demais e falhar com "elemento não encontrado".

    Se a tela estiver bem no meio de um re-render grande (ex: chamada
    logo depois de fechar uma dialog grande, como "Divergência NF -
    Pedido"), o `meter` que acabou de ser achado por `find_elements`
    pode já não existir mais no instante de checar `is_displayed()` -
    confirmado ao vivo: isso estourava StaleElementReferenceException e
    derrubava o fluxo inteiro como se tivesse dado erro de verdade,
    mesmo quando a amarração/geração por trás tinha dado certo (a
    exceção acontecia ANTES da checagem real de conclusão, não por causa
    dela). Trata como "esse meter não conta mais" e segue pros
    próximos, em vez de deixar a exceção subir.
    """

    def sem_meter_ativo(d):
        for meter in d.find_elements(By.CSS_SELECTOR, "wa-meter[data-advpl='tmeter']"):
            try:
                if meter.is_displayed():
                    return False
            except StaleElementReferenceException:
                continue
        return True

    def concluido(d):
        driver.switch_to.default_content()
        if not sem_meter_ativo(d):
            return False
        for webview in d.find_elements(By.CSS_SELECTOR, "wa-webview"):
            try:
                if not webview.shadow_root:
                    continue
                iframe = webview.shadow_root.find_element(By.CSS_SELECTOR, "iframe")
            except Exception:
                continue
            driver.switch_to.frame(iframe)
            ativo = not sem_meter_ativo(d)
            driver.switch_to.default_content()
            if ativo:
                return False
        return True

    fim_espera_inicial = time.time() + espera_inicial
    meter_apareceu = False
    while time.time() < fim_espera_inicial:
        if not concluido(driver):
            meter_apareceu = True
            break
        time.sleep(0.1)

    if meter_apareceu:
        esperar(driver, concluido, timeout)

    driver.switch_to.default_content()
