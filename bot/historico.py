"""historico.py — a "memória" do bot entre uma execução e outra.

O GitHub Actions começa cada execução numa máquina NOVA e vazia. Para o bot lembrar
o preço de ontem, salvamos tudo em data/historico.json, e o workflow faz commit desse
arquivo de volta no repositório ao final (veja .github/workflows/monitor.yml).
"""
import json
import os
from datetime import UTC, datetime

CAMINHO_PADRAO = os.path.join("data", "historico.json")
MAX_PONTOS_POR_URL = 90          # ~1 mês com 3 execuções/dia


def agora_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def carregar(caminho: str = CAMINHO_PADRAO) -> dict:
    if not os.path.exists(caminho):
        return {"precos": {}, "alertas_enviados": {}, "falhas_por_site": {}}
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    dados.setdefault("precos", {})
    dados.setdefault("alertas_enviados", {})
    dados.setdefault("falhas_por_site", {})
    return dados


def salvar(dados: dict, caminho: str = CAMINHO_PADRAO):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    # Limpa alertas com mais de 7 dias para o arquivo não crescer para sempre
    limite = datetime.now(UTC).timestamp() - 7 * 86400
    dados["alertas_enviados"] = {k: v for k, v in dados["alertas_enviados"].items()
                                 if datetime.fromisoformat(v).timestamp() >= limite}
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1, sort_keys=True)


def registrar_preco(dados: dict, url: str, preco: float):
    serie = dados["precos"].setdefault(url, [])
    serie.append([agora_iso(), preco])
    del serie[:-MAX_PONTOS_POR_URL]


def ultimo_preco(dados: dict, url: str):
    serie = dados["precos"].get(url) or []
    return serie[-1][1] if serie else None


def ja_alertado(dados: dict, chave: str) -> bool:
    return chave in dados["alertas_enviados"]


def marcar_alertado(dados: dict, chave: str):
    dados["alertas_enviados"][chave] = agora_iso()
