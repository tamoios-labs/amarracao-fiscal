from selenium.webdriver.chrome.options import Options

# Estado GLOBAL compartilhado entre GLPI e Protheus.
# Mudar aqui muda os DOIS: se True, ambos rodam headless; se False, ambos
# abrem janela visível. É a única fonte de verdade do modo do navegador.
HEADLESS = True


def get_chrome_options(headless=None):
    """
    Options único usado tanto pela extração do GLPI quanto pela
    automação do Protheus. Se `headless` não for passado, usa o estado
    global HEADLESS.
    """
    if headless is None:
        headless = HEADLESS

    options = Options()

    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    return options
