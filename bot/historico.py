"""historico.py — a "memória" do bot entre uma execução e outra.

O GitHub Actions começa cada execução numa máquina NOVA e vazia. Para o bot lembrar
o preço de ontem, salvamos tudo num arquivo JSON, e o workflow faz commit dele no ramo
"dados" ao final (veja .github/workflows/monitor.yml e docs/DECISOES.md, ADR-009).

Para o arquivo só mudar quando algo muda de verdade (e o Git só ganhar commit nesse
caso), o preço é gravado apenas quando DIFERE do último registrado, e as falhas guardam
"desde quando" em vez de um contador que subiria a cada execução.
"""
import json
import os
from datetime import UTC, datetime, timedelta

# No GitHub Actions o workflow aponta para o ramo "dados" via HISTORICO_ARQUIVO;
# no seu PC, sem a variável, vale o caminho de sempre.
CAMINHO_PADRAO = os.getenv("HISTORICO_ARQUIVO") or os.path.join("data", "historico.json")
MAX_PONTOS_POR_URL = 200         # pontos = MUDANÇAS de preço, não leituras


def agora_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def carregar(caminho: str = CAMINHO_PADRAO) -> dict:
    if not os.path.exists(caminho):
        return {"precos": {}, "estoque": {}, "alertas_enviados": {}, "falhas_desde": {},
                "ultimo_resumo": ""}
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    dados.setdefault("precos", {})
    dados.setdefault("estoque", {})      # url -> True (disponível) / False (esgotado)
    dados.setdefault("alertas_enviados", {})
    dados.setdefault("falhas_desde", {})  # site ou url -> quando começou a falhar
    dados.setdefault("ultimo_resumo", "")  # data (Fortaleza) do último resumo diário
    dados.pop("falhas_por_site", None)   # formato antigo (contador por execução)
    # "validas_vistas" NÃO é criada aqui de propósito: a ausência dela avisa o main.py de
    # que é a primeira execução no formato novo (ele registra as ofertas atuais em silêncio).
    return dados


def salvar(dados: dict, caminho: str = CAMINHO_PADRAO):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    # Limpa alertas com mais de 7 dias para o arquivo não crescer para sempre
    limite = datetime.now(UTC).timestamp() - 7 * 86400
    dados["alertas_enviados"] = {k: v for k, v in dados["alertas_enviados"].items()
                                 if datetime.fromisoformat(v).timestamp() >= limite}
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1, sort_keys=True)


def registrar_preco(dados: dict, url: str, preco: float, quando: str = "") -> bool:
    """Grava o preço SÓ se ele mudou. Devolve True se gravou."""
    serie = dados["precos"].setdefault(url, [])
    if serie and serie[-1][1] == preco:
        return False
    serie.append([quando or agora_iso(), preco])
    del serie[:-MAX_PONTOS_POR_URL]
    return True


def maior_preco_recente(dados: dict, url: str, horas: float = 24, agora: datetime | None = None):
    """Maior preço EM VIGOR nas últimas `horas` (None = página sem histórico).

    Como só gravamos mudanças, o último ponto ANTES da janela ainda valia no início dela:
    por isso ele também entra na conta. Ex.: 2.500 há 30 h e 2.000 há 5 h -> 2.500 vigorou
    até 5 h atrás, então a referência das últimas 24 h é 2.500.
    """
    serie = dados["precos"].get(url) or []
    if not serie:
        return None
    limite = (agora or datetime.now(UTC)) - timedelta(hours=horas)
    anterior, na_janela = None, []
    for quando, preco in serie:
        if datetime.fromisoformat(quando) < limite:
            anterior = preco
        else:
            na_janela.append(preco)
    if anterior is not None:
        na_janela.append(anterior)
    return max(na_janela)


def ultimo_preco(dados: dict, url: str):
    serie = dados["precos"].get(url) or []
    return serie[-1][1] if serie else None


def estoque_anterior(dados: dict, url: str):
    """True/False = como estava na última execução; None = página nunca vista."""
    return dados["estoque"].get(url)


def registrar_estoque(dados: dict, url: str, disponivel: bool):
    dados["estoque"][url] = bool(disponivel)


def ja_alertado(dados: dict, chave: str) -> bool:
    return chave in dados["alertas_enviados"]


def marcar_alertado(dados: dict, chave: str):
    dados["alertas_enviados"][chave] = agora_iso()


def atualizar_falhas(dados: dict, chaves_com_falha):
    """Guarda DESDE QUANDO cada site/URL falha; apaga quem voltou a funcionar."""
    desde = dados["falhas_desde"]
    for chave in list(desde):
        if chave not in chaves_com_falha:
            del desde[chave]
    for chave in chaves_com_falha:
        desde.setdefault(chave, agora_iso())


def falhas_cronicas(dados: dict, horas: float, agora: datetime | None = None) -> list:
    """Sites/URLs que falham sem parar há pelo menos `horas`."""
    limite = (agora or datetime.now(UTC)) - timedelta(hours=horas)
    return sorted(k for k, v in dados["falhas_desde"].items()
                  if datetime.fromisoformat(v) <= limite)


def marcar_validas_vistas(dados: dict, urls):
    """Lembra as páginas que já foram oferta válida (base do alerta 🆕)."""
    vistas = dados.setdefault("validas_vistas", {})
    for url in urls:
        vistas.setdefault(url, agora_iso())
