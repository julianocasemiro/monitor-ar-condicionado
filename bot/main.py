"""main.py — o "maestro": chama cada peça na ordem certa.

Como rodar (detalhes no guia):
  python -m bot.main               -> execução normal (vigia, avisa, salva histórico)
  python -m bot.main --simular     -> faz tudo, mas NÃO envia nada e NÃO salva (teste seguro)
  python -m bot.main --diagnostico -> testa cada página vigiada e mostra o que voltou
  python -m bot.main --testar telegram | agenda -> valida os segredos
"""
import argparse
import logging
import os
import sys
from urllib.parse import urlparse

import yaml

from . import extratores, filtros, historico, notificadores, regras
from .http_educado import ClienteEducado, PaginaNaoEncontrada, SiteBloqueado

log = logging.getLogger("bot")


class ConfigInvalida(Exception):
    """Erro no config.yaml, detectado ANTES de qualquer acesso à internet."""


def carregar_config(caminho="config.yaml") -> dict:
    with open(caminho, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validar_config(cfg)
    return cfg


def validar_config(cfg: dict):
    """Falha cedo e com mensagem clara (princípio "fail fast"): é melhor o CI acusar
    um erro de digitação no YAML do que o bot rodar errado em produção."""
    ids = {m["id"] for m in cfg.get("modelos") or []}
    for i, item in enumerate(cfg.get("urls_vigiadas") or [], start=1):
        if not str(item.get("url", "")).startswith("https://"):
            raise ConfigInvalida(f"urls_vigiadas item {i}: a url precisa começar com https://")
        if not item.get("loja"):
            raise ConfigInvalida(f"urls_vigiadas item {i}: falta o campo loja")
        if item.get("modelo") and item["modelo"] not in ids:
            raise ConfigInvalida(f"urls_vigiadas item {i}: modelo '{item['modelo']}' "
                                 "não existe na lista modelos")


def segredo(nome: str) -> str:
    """Lê um segredo das variáveis de ambiente (no GitHub, vêm dos Secrets)."""
    return (os.getenv(nome) or "").strip()


def modelo_por_id(cfg: dict, modelo_id: str):
    return next((m for m in cfg["modelos"] if m["id"] == modelo_id), None)


# --------------------------------------------------------------------------- coleta
def coletar(cliente, cfg, falhas: dict) -> list:
    """Visita cada página vigiada e devolve as ofertas lidas do JSON-LD."""
    ofertas = []
    for item in cfg.get("urls_vigiadas") or []:
        url, dominio = item["url"], urlparse(item["url"]).netloc
        try:
            resp = cliente.get(url)
        except PaginaNaoEncontrada:
            falhas[url] = "página não encontrada (404): URL desatualizada, troque no config.yaml"
            continue
        except SiteBloqueado as motivo:
            falhas[dominio] = str(motivo)        # chave = site: a recusa vale para a loja toda
            continue
        oferta = extratores.extrair_jsonld(resp.text, url, item["loja"])
        if not oferta:
            falhas[url] = "página sem dados estruturados de produto (JSON-LD)"
            continue
        oferta.loja = item["loja"]
        oferta.modelo_id = item.get("modelo", "")
        ofertas.append(oferta)
    return ofertas


# --------------------------------------------------------------------------- execução
def executar(cfg: dict, simular: bool = False) -> int:
    crit = cfg["criterios"]
    cliente = ClienteEducado(cfg["educacao_http"])
    dados = historico.carregar()
    falhas = {}

    brutas = coletar(cliente, cfg, falhas)

    aprovadas = []
    for o in brutas:
        forcado = modelo_por_id(cfg, o.modelo_id) if o.modelo_id else None
        veredito = filtros.avaliar(o.texto_busca or o.titulo, crit, cfg["modelos"],
                                   o.descricao, modelo_forcado=forcado)
        if not veredito["aprovado"]:
            log.info("Descartada (%s): %s", veredito["motivo"], o.titulo)
            continue
        o.btu = veredito["btu"]
        o.modelo_id = veredito["modelo"]["id"] if veredito["modelo"] else ""
        o.avisos += veredito["avisos"]
        aprovadas.append(o)
    esgotadas = sum(1 for o in aprovadas if not o.disponivel)
    log.info("Páginas lidas: %d | passaram nos filtros: %d | esgotadas: %d",
             len(brutas), len(aprovadas), esgotadas)

    validas = regras.aplicar_regras_de_compra(aprovadas, crit,
                                              cfg.get("lojas_com_instalacao_inclusa") or [])
    urls_validas = {o.url for o in validas}
    al = cfg["alertas"]
    quedas = regras.detectar_quedas(validas, dados, al["queda_percentual_minima"])
    retornos = regras.detectar_retornos_estoque(validas, dados)
    diferencas = [d for d in regras.detectar_diferencas(
                      [o for o in aprovadas if o.disponivel], dados,
                      al["diferenca_percentual_minima"], al["diferenca_reais_minima"])
                  if d["barata"].url in urls_validas]

    # Falhas que se repetem 3 execuções seguidas ganham um aviso para você revisar o config
    for chave in set(list(falhas) + list(dados["falhas_por_site"])):
        dados["falhas_por_site"][chave] = (dados["falhas_por_site"].get(chave, 0) + 1
                                           if chave in falhas else 0)
    cronicos = [s for s, n in dados["falhas_por_site"].items() if n >= 3]
    nota = (f"Falharam 3+ execuções seguidas: {', '.join(cronicos)}. "
            "Considere remover ou atualizar no config.yaml.") if cronicos else ""

    mensagem = notificadores.formatar_resumo(validas, quedas, diferencas, falhas, nota,
                                             retornos=retornos, esgotadas=esgotadas)

    if simular:
        print("\n===== MENSAGEM QUE SERIA ENVIADA (modo --simular) =====\n")
        print(mensagem)
        return 0

    token, chat = segredo("TELEGRAM_TOKEN"), segredo("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log.error("Faltam os segredos TELEGRAM_TOKEN e/ou TELEGRAM_CHAT_ID.")
        return 2
    notificadores.enviar_telegram(token, chat, mensagem)

    url_agenda, seg_agenda = segredo("AGENDA_WEBHOOK_URL"), segredo("AGENDA_WEBHOOK_TOKEN")
    for d in diferencas:
        if al.get("criar_evento_agenda") and url_agenda and seg_agenda:
            notificadores.criar_evento_agenda(url_agenda, seg_agenda, d)
        historico.marcar_alertado(dados, d["chave"])
    for alerta in quedas + retornos:
        historico.marcar_alertado(dados, alerta["chave"])
    for o in aprovadas:
        historico.registrar_preco(dados, o.url, o.preco)
        historico.registrar_estoque(dados, o.url, o.disponivel)
    historico.salvar(dados)

    # Se NENHUMA página pôde ser lida, devolvemos erro para o GitHub marcar a execução em vermelho
    return 1 if (not brutas and falhas) else 0


def diagnosticar(cfg: dict) -> int:
    """Visita cada página vigiada UMA vez e mostra o que voltou. Não envia nada."""
    cliente = ClienteEducado(cfg["educacao_http"])
    print(f"User-Agent usado: {cliente.user_agent}\n")
    for item in cfg.get("urls_vigiadas") or []:
        url = item["url"]
        print("=" * 72)
        print(f"{item['loja']} | modelo no config: {item.get('modelo') or '(nenhum)'}")
        print(url)
        if not cliente.permitido(url):
            print("  robots.txt: PROIBIDO -> o bot não acessa esta página")
            continue
        try:
            resp = cliente.get(url)
        except (SiteBloqueado, PaginaNaoEncontrada) as motivo:
            r = cliente.ultima_resposta
            detalhe = f" | Content-Type: {r.headers.get('Content-Type')}" if r is not None else ""
            print(f"  {type(motivo).__name__}: {motivo}{detalhe}")
            continue
        print(f"  HTTP {resp.status_code} | Content-Type: {resp.headers.get('Content-Type')}")
        o = extratores.extrair_jsonld(resp.text, url, item["loja"])
        if not o:
            print("  Sem JSON-LD de produto. Início:", resp.text[:150].strip().replace("\n", " "))
            continue
        forcado = modelo_por_id(cfg, item["modelo"]) if item.get("modelo") else None
        v = filtros.avaliar(o.texto_busca or o.titulo, cfg["criterios"], cfg["modelos"],
                            o.descricao, modelo_forcado=forcado)
        print(f"  Título: {o.titulo[:80]}")
        print(f"  Preço: R$ {o.preco:.2f} | Em estoque: {'sim' if o.disponivel else 'NÃO'}")
        print(f"  Filtros: {'APROVADA' if v['aprovado'] else 'REPROVADA (' + v['motivo'] + ')'}"
              f" | modelo detectado: {v['modelo']['id'] if v['modelo'] else '(nenhum)'}")
        for aviso in v["avisos"]:
            print(f"  ⚠️ {aviso}")
    return 0


def testar_integracoes(qual: str) -> int:
    """Manda uma mensagem/evento de TESTE, para validar os segredos sem esperar uma oferta."""
    if qual == "telegram":
        token, chat = segredo("TELEGRAM_TOKEN"), segredo("TELEGRAM_CHAT_ID")
        if not (token and chat):
            log.error("Faltam TELEGRAM_TOKEN e/ou TELEGRAM_CHAT_ID.")
            return 2
        notificadores.enviar_telegram(token, chat, "✅ Teste: o bot consegue falar com você no Telegram.")
        log.info("Mensagem de teste enviada ao Telegram.")
        return 0
    url, seg = segredo("AGENDA_WEBHOOK_URL"), segredo("AGENDA_WEBHOOK_TOKEN")
    if not (url and seg):
        log.error("Faltam AGENDA_WEBHOOK_URL e/ou AGENDA_WEBHOOK_TOKEN.")
        return 2
    falsa = extratores.Oferta(loja="Loja Teste", url="https://example.com", titulo="TESTE", preco=1000)
    cara = extratores.Oferta(loja="Outra Loja", url="https://example.com", titulo="TESTE", preco=1500)
    ok = notificadores.criar_evento_agenda(url, seg, {"barata": falsa, "cara": cara,
                                                      "dif": 500, "pct": 33})
    log.info("Evento de teste na agenda: %s", "criado" if ok else "FALHOU")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Monitor de preços de ar-condicionado")
    parser.add_argument("--simular", action="store_true", help="não envia e não salva nada")
    parser.add_argument("--diagnostico", action="store_true", help="testa as páginas vigiadas")
    parser.add_argument("--testar", choices=["telegram", "agenda"],
                        help="envia uma mensagem/evento de teste e encerra")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Segurança: o urllib3 (usado pelo requests), em modo DEBUG, escreve a URL completa no log.
    # A URL do Telegram contém o token do bot -> mantemos esse logger só em WARNING.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if args.testar:
        sys.exit(testar_integracoes(args.testar))
    cfg = carregar_config(args.config)
    sys.exit(diagnosticar(cfg) if args.diagnostico else executar(cfg, simular=args.simular))


if __name__ == "__main__":
    main()
