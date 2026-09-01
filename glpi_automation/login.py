import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from .config import GLPI_LOGIN_URL, GLPI_USUARIO, GLPI_SENHA
from .log import ok, aviso, info


def a_logar(driver, max_tentativas=3):
    for tentativa in range(1, max_tentativas + 1):
        info(f"Login no GLPI (tentativa {tentativa}/{max_tentativas})...")
        driver.get(GLPI_LOGIN_URL)

        username = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "login_name"))
        )
        username.clear()
        username.send_keys(GLPI_USUARIO)

        password = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
        )
        driver.execute_script("arguments[0].value = arguments[1];", password, GLPI_SENHA)

        submit_btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "submit"))
        )
        driver.execute_script("arguments[0].click();", submit_btn)

        time.sleep(2)

        # verifica se apareceu erro de login
        erros = driver.find_elements(By.CSS_SELECTOR, ".alert-warning")
        if erros:
            aviso(f"Erro de login detectado na tentativa {tentativa} - tentando novamente...")
            try:
                btn_tentar_novamente = driver.find_element(By.CSS_SELECTOR, ".alert-warning .btn-primary")
                btn_tentar_novamente.click()
                time.sleep(1)
            except Exception:
                pass
            if tentativa == max_tentativas:
                raise Exception("Login no GLPI falhou após todas as tentativas.")
            continue

        ok("Login no GLPI realizado com sucesso.")
        return
