import os
import re
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

import yaml
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

logger = logging.getLogger(__name__)

# ------------------------ Datas ------------------------
_DATETIME_RX = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})")

def _br_date_to_dt(txt: str) -> Optional[datetime]:
    if not txt:
        return None
    txt = txt.strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(txt, fmt)
        except Exception:
            pass
    return None

def _month_bounds(d: datetime) -> Tuple[datetime, datetime]:
    ini = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if d.month == 12:
        prox = d.replace(year=d.year + 1, month=1, day=1)
    else:
        prox = d.replace(month=d.month + 1, day=1)
    fim = prox - timedelta(seconds=1)
    return ini, fim

def _fdate(d: datetime) -> str:
    return d.strftime("%d/%m/%Y")

# ------------------------ Client ------------------------
class PortalClient:
    """
    Compatível com o watcher atual:
      - encontrar_linha_por_data_hora(dt, tipo) -> retorna **href** de /Despesa/Index
      - abrir_despesas_por_href(href) navega até a tela
      - preencher_e_anexar(tipo, valor_centavos, arquivo, data_evento)
    """

    def __init__(self, config_path: str = "config.yaml", headless: bool = True):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f) or {}

        # URLs e seletores (com defaults)
        self.base_url = self.cfg.get("tabela", {}).get(
            "url", "https://mobile.ncratleos.com/sb0121/Deslocamento/Index"
        )
        self.login_url = self.cfg.get("login", {}).get(
            "url", "https://mobile.ncratleos.com/sb0121/"
        )
        self.user_sel = self.cfg.get("login", {}).get("user_selector", "input#UserName")
        self.pass_sel = self.cfg.get("login", {}).get("pass_selector", "input#Password")
        self.submit_sel = self.cfg.get("login", {}).get("submit_selector", "button[type='submit']")
        self.row_selector = self.cfg.get("tabela", {}).get("row_selector", "table tbody tr")
        self.form_anexar_sel = self.cfg.get("form", {}).get("anexar_input_selector", "input[type='file']")

        # Navegador (força geckodriver para evitar Selenium Manager no aarch64)
        o = FirefoxOptions()
        if headless:
            o.add_argument("-headless")
            o.add_argument("-width=1440")
            o.add_argument("-height=900")
        firefox_bin = os.getenv("FIREFOX_BIN")
        if firefox_bin:
            o.binary_location = firefox_bin
        gecko_path = os.getenv("GECKODRIVER", "/usr/local/bin/geckodriver")
        service = FirefoxService(executable_path=gecko_path)
        self.driver = webdriver.Firefox(options=o, service=service)
        self.wait = WebDriverWait(self.driver, 20)

    # ---------- utils ----------
    def _scroll_center(self, el):
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        except Exception:
            pass

    def _js_click(self, el):
        self.driver.execute_script("arguments[0].click();", el)

    def _robust_click(self, el):
        try:
            self._scroll_center(el)
            self.driver.execute_script("arguments[0].click();", el)
        except Exception:
            try:
                el.click()
            except Exception:
                pass

    # ---------- login ----------
    def _is_login_page(self) -> bool:
        try:
            url = (self.driver.current_url or "").rstrip("/")
            if url.endswith("/sb0121") or "/Account/Login" in url:
                return True
            title_ok = "login - fieldmap web" in (self.driver.title or "").lower()
            has_user = bool(self.driver.find_elements(By.CSS_SELECTOR, "input#UserName"))
            has_pass = bool(self.driver.find_elements(By.CSS_SELECTOR, "input#Password"))
            return title_ok and (has_user and has_pass)
        except Exception:
            return False

    def login(self):
        user = os.getenv("PORTAL_USER")
        pwd = os.getenv("PORTAL_PASS")
        if not user or not pwd:
            raise RuntimeError("Credenciais ausentes (.env: PORTAL_USER/PORTAL_PASS).")

        self.driver.get(self.login_url)
        self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, self.user_sel)))
        u = self.driver.find_element(By.CSS_SELECTOR, self.user_sel)
        p = self.driver.find_element(By.CSS_SELECTOR, self.pass_sel)
        u.clear(); u.send_keys(user)
        p.clear(); p.send_keys(pwd)
        try:
            btn = self.driver.find_element(By.CSS_SELECTOR, self.submit_sel)
            self._robust_click(btn)
        except Exception:
            self.driver.execute_script("document.querySelector(arguments[0])?.click()", self.submit_sel)
        self.wait.until(lambda d: not self._is_login_page())

    def ensure_logged(self):
        if self._is_login_page():
            self.login()
            return
        try:
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception:
            self.login()

    # ---------- grade ----------
    def _esperar_grade_pronta(self, timeout: int | None = None) -> bool:
        d = self.driver
        row_sel = self.row_selector
        if timeout is None:
            timeout = int(self.cfg.get("tabela", {}).get("wait_ready_seconds", 30) or 30)

        table_sel = "table, table#datatable, table.dataTable"
        empty_sel = "td.dataTables_empty, tr.dataTables_empty, .dataTables_empty"

        end_time = time.time() + timeout
        last_exc = None
        while time.time() < end_time:
            try:
                WebDriverWait(d, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, table_sel)))
                rows = d.find_elements(By.CSS_SELECTOR, row_sel)
                if rows:
                    return True
                if d.find_elements(By.CSS_SELECTOR, empty_sel):
                    return False
                time.sleep(0.4)
            except (TimeoutException, StaleElementReferenceException) as e:
                last_exc = e
                time.sleep(0.5)
            except Exception as e:
                last_exc = e
                time.sleep(0.3)

        # dumps de debug
        try:
            with open("debug_grade_timeout.html", "w", encoding="utf-8") as f:
                f.write(d.page_source)
            try:
                d.save_screenshot("debug_grade_timeout.png")
            except Exception:
                pass
        except Exception:
            pass

        raise TimeoutException("Grade não ficou pronta (sem linhas nem 'sem registros').") from last_exc

    def ensure_on_deslocamento_index(self):
        self.driver.get(self.base_url)
        if self._is_login_page():
            self.login()
            self.driver.get(self.base_url)
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    # ---------- menu -> href 'Despesas' ----------
    def _open_menu_and_get_despesas(self, row_el, dt_ini: datetime) -> Optional[str]:
        btn = None
        for sel in (
            "button.btn.btn-success.dropdown-toggle",
            "button.btn.dropdown-toggle",
            "button.dropdown-toggle",
        ):
            f = row_el.find_elements(By.CSS_SELECTOR, sel)
            if f:
                btn = f[0]
                break
        if not btn:
            return None

        self._robust_click(btn)

        menu = None
        for sel in (".dropdown-menu.show", "ul.dropdown-menu"):
            try:
                menu = WebDriverWait(self.driver, 4).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, sel))
                )
                break
            except TimeoutException:
                pass
        if not menu:
            return None

        links = menu.find_elements(By.CSS_SELECTOR, "a.dropdown-item, a, button")
        best, fallback = None, None
        enc_date = f"{dt_ini.month:02d}%2F{dt_ini.day:02d}%2F{dt_ini.year}"
        enc_hhmm = f"{dt_ini.hour:02d}%3A{dt_ini.minute:02d}"
        for a in links:
            href = (a.get_attribute("href") or "")
            text = (a.text or "").lower()
            if "/Despesa/" in href:
                if "dataInicio=" in href and enc_date in href and enc_hhmm in href:
                    best = href
                    break
                if fallback is None:
                    fallback = href
            elif "despesa" in text and fallback is None:
                fallback = href
        return best or fallback

    # ---------- localizar por data/hora ----------
    def encontrar_linha_por_data_hora(self, dt_evento: datetime, tipo: str) -> Optional[str]:
        """
        Retorna o **href** para a tela de Despesas da linha correta.
        pedágio: janela [ini, fim] do deslocamento.
        estacionamento: janela [fim_atual, ini_proximo).
        """
        if not dt_evento:
            return None
        d = self.driver
        self.ensure_on_deslocamento_index()
        self._fixar_periodo_do_mes(dt_evento)

        if not self._esperar_grade_pronta(timeout=int(self.cfg.get("tabela", {}).get("wait_ready_seconds", 30) or 30)):
            return None

        row_sel = self.row_selector
        rows = d.find_elements(By.CSS_SELECTOR, row_sel)
        if not rows:
            return None

        def _safe_text(el) -> str:
            for _ in range(2):
                try:
                    return (el.text or "").strip()
                except Exception:
                    time.sleep(0.05)
            return ""

        segmentos = []
        for idx in range(len(rows)):
            try:
                rows_now = d.find_elements(By.CSS_SELECTOR, row_sel)
                if idx >= len(rows_now):
                    break
                tr = rows_now[idx]
                tds = tr.find_elements(By.TAG_NAME, "td")
            except StaleElementReferenceException:
                continue

            ini = None
            fim = None
            if len(tds) >= 2:
                ini = _br_date_to_dt(_safe_text(tds[0]))
                fim = _br_date_to_dt(_safe_text(tds[1]))

            if not ini or not fim:
                bloco = " | ".join(_safe_text(td) for td in tds[:4])
                m = _DATETIME_RX.findall(bloco)
                if m:
                    try:
                        ini = datetime.strptime(" ".join(m[0]), "%d/%m/%Y %H:%M:%S")
                        fim = datetime.strptime(" ".join(m[1] if len(m) > 1 else m[0]), "%d/%m/%Y %H:%M:%S")
                    except Exception:
                        ini = fim = None

            if ini and fim:
                if fim < ini:
                    ini, fim = fim, ini
                segmentos.append((ini, fim, idx))

        if not segmentos:
            return None

        segmentos.sort(key=lambda t: t[0])
        alvo_tipo = (tipo or "").strip().lower()

        # log auxiliar (igual ao que você viu)
        try:
            dump = [f"[{i}] {a[0].strftime('%d/%m %H:%M:%S')}–{a[1].strftime('%d/%m %H:%M:%S')}" for i, a in enumerate(segmentos)]
            logger.info("[FM] dt_evento=%s | segmentos=%s", dt_evento.strftime("%d/%m %H:%M:%S"), ", ".join(dump))
        except Exception:
            pass

        # pedágio
        if "pedag" in alvo_tipo:
            candidatos = [(ini, fim, idx) for (ini, fim, idx) in segmentos if ini <= dt_evento <= fim]
            if not candidatos:
                return None
            candidatos.sort(key=lambda t: (t[1] - t[0]).total_seconds())
            ini, fim, idx = candidatos[0]
            rows_now = d.find_elements(By.CSS_SELECTOR, row_sel)
            if idx >= len(rows_now):
                return None
            tr = rows_now[idx]
            return self._open_menu_and_get_despesas(tr, ini)

        # estacionamento
        if "estacion" in alvo_tipo:
            for i, (ini, fim, idx) in enumerate(segmentos):
                prox_ini = segmentos[i + 1][0] if i + 1 < len(segmentos) else None
                if prox_ini:
                    if fim <= dt_evento < prox_ini:
                        rows_now = d.find_elements(By.CSS_SELECTOR, row_sel)
                        if idx >= len(rows_now):
                            return None
                        tr = rows_now[idx]
                        return self._open_menu_and_get_despesas(tr, ini)
                else:
                    if dt_evento >= fim:
                        rows_now = d.find_elements(By.CSS_SELECTOR, row_sel)
                        if idx >= len(rows_now):
                            return None
                        tr = rows_now[idx]
                        return self._open_menu_and_get_despesas(tr, ini)
            return None

        # desconhecido -> usa janela do deslocamento
        for ini, fim, idx in segmentos:
            if ini <= dt_evento <= fim:
                rows_now = d.find_elements(By.CSS_SELECTOR, row_sel)
                if idx >= len(rows_now):
                    return None
                tr = rows_now[idx]
                return self._open_menu_and_get_despesas(tr, ini)
        return None

    # ---------- filtro período ----------
    def _fixar_periodo_do_mes(self, ref: datetime):
        d = self.driver
        try:
            inp_ini = d.find_element(By.CSS_SELECTOR, "input#dataInicialPesquisa, input[name='dataInicialPesquisa']")
            inp_fim = d.find_element(By.CSS_SELECTOR, "input#dataFinalPesquisa, input[name='dataFinalPesquisa']")
        except Exception:
            return

        ini, fim = _month_bounds(ref)
        self._set_input_value_js(inp_ini, _fdate(ini))
        self._set_input_value_js(inp_fim, _fdate(fim))
        try:
            btn_p = d.find_element(By.CSS_SELECTOR, "button#btnPesquisar, button[type='submit']")
            self._robust_click(btn_p)
            time.sleep(0.6)
        except Exception:
            pass

    def _set_input_value_js(self, el, value_str: str):
        self.driver.execute_script(
            """
            const el = arguments[0], v = arguments[1];
            el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            """,
            el, value_str,
        )

    # ---------- navegar + anexar ----------
    def abrir_despesas_por_href(self, href: str) -> bool:
        self.ensure_logged()
        self.driver.get(href)
        try:
            self.wait.until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
            return True
        except TimeoutException:
            return False

    def _norm(self, s: str) -> str:
        repl = (("á","a"),("à","a"),("â","a"),("ã","a"),
                ("é","e"),("ê","e"),
                ("í","i"),
                ("ó","o"),("ô","o"),("õ","o"),
                ("ú","u"),
                ("ç","c"))
        s = (s or "").lower()
        for a, b in repl:
            s = s.replace(a, b)
        return s

    def _choose_tipo_option(self, select_el, tipo: str) -> bool:
        alvo = (tipo or "").strip().lower()
        is_pedagio = "pedag" in alvo
        textos_possiveis = ["2 - Pedágio", "2 - Pedagio"] if is_pedagio else ["1 - Estacionamento", "1 - Estaciona"]
        valor_alvo = "2" if is_pedagio else "1"

        sel = Select(select_el)
        for t in textos_possiveis:
            try:
                sel.select_by_visible_text(t)
                self._set_input_value_js(select_el, select_el.get_attribute("value") or "")
                return True
            except Exception:
                pass
        try:
            sel.select_by_value(valor_alvo)
            self._set_input_value_js(select_el, valor_alvo)
            return True
        except Exception:
            pass
        for opt in select_el.find_elements(By.TAG_NAME, "option"):
            txt = (opt.text or "").lower()
            if (is_pedagio and "pedag" in txt) or (not is_pedagio and "estacion" in txt):
                self._robust_click(opt)
                self._set_input_value_js(select_el, opt.get_attribute("value") or "")
                return True
        return False

    def preencher_e_anexar(self, tipo: str, valor_centavos: int, arquivo: str, data_evento: Optional[datetime] = None) -> bool:
        d, w = self.driver, self.wait

        url = d.current_url or ""
        if "/Despesa/" not in url:
            return False

        if "/Despesa/Index" in url:
            try:
                btn_sel = "a[href*='/Despesa/New'], a.center-block.btn.btn-success[href*='/Despesa/New']"
                novo = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_sel)))
            except TimeoutException:
                return False
            self._scroll_center(novo)
            self._robust_click(novo)
            w.until(lambda drv: "/Despesa/New" in (drv.current_url or ""))

        tipo_select = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select#Tipo, select[name='Tipo']")))
        if not self._choose_tipo_option(tipo_select, tipo):
            return False

        valor_input = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input#Valor, input[name='Valor']")))
        try:
            valor_input.clear()
        except Exception:
            pass
        valor_fmt = f"{valor_centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        valor_input.send_keys(valor_fmt)

        file_input = w.until(EC.presence_of_element_located((By.CSS_SELECTOR, self.form_anexar_sel)))
        file_input.send_keys(os.path.abspath(arquivo))

        try:
            salvar = w.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
        except TimeoutException:
            return False
        self._scroll_center(salvar)
        self._robust_click(salvar)

        try:
            w.until(lambda drv: "/Despesa/Index" in (drv.current_url or ""))
        except TimeoutException:
            if "/Despesa/Save" in (d.current_url or ""):
                return False

        # valida presença da linha/valor na grade
        try:
            w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table, table#datatable, table.dataTable")))
            is_pedagio = "pedag" in self._norm(tipo)
            alvo_tipo_norm = "pedag" if is_pedagio else "estacion"
            for _ in range(12):
                linhas = d.find_elements(By.CSS_SELECTOR, "table tbody tr")
                for tr in linhas:
                    tds = tr.find_elements(By.TAG_NAME, "td")
                    if not tds:
                        continue
                    txt_norm = self._norm(" | ".join(td.text or "" for td in tds))
                    if (alvo_tipo_norm in txt_norm) and (self._norm(valor_fmt) in txt_norm):
                        return True
                time.sleep(0.6)
        except Exception:
            pass

        return False
