"""http_educado.py — o "cliente HTTP bem-educado" do bot.

Filosofia (detalhada em docs/DECISOES.md, ADR-003): o bot NÃO tenta enganar
firewall/WAF. Ele evita ser incômodo:
  1. Diz quem é (User-Agent honesto, com link para o repositório).
  2. Lê e obedece o robots.txt de cada site.
  3. Espera um intervalo mínimo entre requisições ao mesmo site.
  4. Tem teto de requisições por site em cada execução.
  5. Se o site disser "não" (403, 429, 503, CAPTCHA), ele PARA de acessar aquele site
     nesta execução e te avisa. Não troca de identidade, não usa proxy, não insiste.
"""
import os
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from .filtros import normalizar

# Frases típicas de páginas de bloqueio. Não usamos "captcha" sozinho porque muitas
# páginas normais carregam o script do reCAPTCHA no formulário de login (falso positivo).
SINAIS_DE_BLOQUEIO = ("access denied", "acesso negado", "are you a robot", "are you human",
                      "verifique se voce e humano", "attention required", "request blocked",
                      "pardon our interruption")


class SiteBloqueado(Exception):
    """O site recusou o bot. Tratamos como um 'não' educado e seguimos em frente."""


class ClienteEducado:
    def __init__(self, cfg_http: dict):
        repo = os.getenv("GITHUB_REPOSITORY", "projeto-pessoal")
        self.user_agent = (f"MonitorArCondicionadoBot/1.0 (+https://github.com/{repo}; "
                           "uso pessoal, poucas consultas por dia)")
        self.intervalo = cfg_http["intervalo_segundos"]
        self.timeout = cfg_http["timeout_segundos"]
        self.maximo = cfg_http["max_requisicoes_por_site"]
        self.respeitar_robots = cfg_http["respeitar_robots_txt"]
        self.sessao = requests.Session()
        self.sessao.headers.update({"User-Agent": self.user_agent,
                                    "Accept-Language": "pt-BR,pt;q=0.9"})
        self._ultimo_acesso = {}      # dominio -> horário da última requisição
        self._contagem = {}           # dominio -> nº de requisições nesta execução
        self._robots = {}             # dominio -> RobotFileParser (cache)
        self.bloqueados = {}          # dominio -> motivo

    # ---------- robots.txt ----------
    def _robots_de(self, dominio: str):
        if dominio in self._robots:
            return self._robots[dominio]
        rp = urllib.robotparser.RobotFileParser()
        url = f"https://{dominio}/robots.txt"
        try:
            resp = self.sessao.get(url, timeout=self.timeout)
            if resp.status_code >= 500:
                rp.disallow_all = True        # site com erro: na dúvida, não acessa
            elif resp.status_code >= 400:
                rp.allow_all = True           # sem robots.txt = sem restrição (padrão RFC 9309)
            else:
                rp.parse(resp.text.splitlines())
        except requests.RequestException:
            rp.disallow_all = True
        self._robots[dominio] = rp
        return rp

    def permitido(self, url: str) -> bool:
        if not self.respeitar_robots:
            return True
        dominio = urlparse(url).netloc
        return self._robots_de(dominio).can_fetch(self.user_agent, url)

    # ---------- ritmo ----------
    def _esperar_vez(self, dominio: str):
        rp = self._robots.get(dominio)
        atraso_robots = rp.crawl_delay(self.user_agent) if rp else None
        intervalo = max(self.intervalo, float(atraso_robots or 0))
        decorrido = time.monotonic() - self._ultimo_acesso.get(dominio, 0)
        if decorrido < intervalo:
            time.sleep(intervalo - decorrido)

    # ---------- requisição ----------
    def requisitar(self, metodo: str, url: str, **kwargs) -> requests.Response:
        dominio = urlparse(url).netloc
        if dominio in self.bloqueados:
            raise SiteBloqueado(self.bloqueados[dominio])
        if not self.permitido(url):
            raise SiteBloqueado("robots.txt não permite este caminho")
        if self._contagem.get(dominio, 0) >= self.maximo:
            raise SiteBloqueado("teto de requisições desta execução atingido")

        self._esperar_vez(dominio)
        self._contagem[dominio] = self._contagem.get(dominio, 0) + 1
        try:
            resp = self.sessao.request(metodo, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as erro:
            raise SiteBloqueado(f"falha de rede: {erro.__class__.__name__}") from erro
        finally:
            self._ultimo_acesso[dominio] = time.monotonic()

        if resp.status_code in (401, 403, 429, 503):
            self.bloqueados[dominio] = f"HTTP {resp.status_code}"
            raise SiteBloqueado(self.bloqueados[dominio])
        trecho = normalizar(resp.text[:3000]) if "html" in resp.headers.get("Content-Type", "") else ""
        if any(sinal in trecho for sinal in SINAIS_DE_BLOQUEIO):
            self.bloqueados[dominio] = "página de verificação anti-robô"
            raise SiteBloqueado(self.bloqueados[dominio])
        return resp

    def get(self, url, **kw):
        return self.requisitar("GET", url, **kw)

    def post(self, url, **kw):
        return self.requisitar("POST", url, **kw)
