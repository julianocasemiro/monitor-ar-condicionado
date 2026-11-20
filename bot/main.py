"""main.py — o "maestro": chama cada peça na ordem certa.

Como rodar (detalhes no guia):
  python -m bot.main               -> execução normal (busca, avisa, salva histórico)
  python -m bot.main --simular     -> faz tudo, mas NÃO envia nada e NÃO salva (teste seguro)
  python -m bot.main --diagnostico -> testa cada loja e diz quais funcionam
  python -m bot.main --testar telegram | agenda -> valida os segredos
"""
import argparse
import logging
import os
import sys

import yaml

from . import extratores, filtros, historico, notificadores, regras
from .http_educado import ClienteEducado, SiteBloqueado

log = logging.getLogger("bot")


def carregar_config(caminho="config.yaml") -> dict:
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def segredo(nome: str) -> str:
    """Lê um segredo das variáveis de ambiente (no GitHub, vêm dos Secrets)."""
    return (os.getenv(nome) or "").strip()


# --------------------------------------------------------------------------- coleta
def coletar_vtex(cliente, cfg, falhas: dict) -> list:
    ofertas = []
    for loja in cfg["lojas_vtex"]:
        dominio, nome = loja["dominio"], loja["nome"]
        for termo in cfg["termos_busca"]:
            try:
                resp = cliente.get(extratores.url_busca_vtex(dominio),
                                   params=extratores.parametros_busca_vtex(termo))
                produtos = resp.json()
            except SiteBloqueado as motivo:
                falhas[dominio] = str(motivo)
                break                          # não insiste neste site nesta execução
            except ValueError:
                falhas[dominio] = "não respondeu como loja VTEX (resposta não é JSON)"
                break
            novas = extratores.extrair_vtex(produtos, nome, dominio)
            log.info("%s | '%s' -> %d produtos", nome, termo, len(novas))
            ofertas += novas
    return ofertas


def coletar_urls(cliente, cfg, falhas: dict) -> list:
    ofertas = []
    for item in cfg.get("urls_vigiadas") or []:
        try:
            resp = cliente.get(item["url"])
        except SiteBloqueado as motivo:
            falhas[item["url"]] = str(motivo)
            continue
        oferta = extratores.extrair_jsonld(resp.text, item["url"], item.get("loja", ""))
        if oferta:
            ofertas.append(oferta)
        else:
            falhas[item["url"]] = "página sem dados estruturados de produto (JSON-LD)"
    return ofertas


def verificar_frete(cliente, ofertas: list, cep: str):
    """Para ofertas VTEX, simula o carrinho com o seu CEP para saber o frete."""
    if not cep:
        return
    for o in ofertas:
        if o.origem != "vtex":
            continue
        dominio = o.url.split("/")[2]
        try:
            resp = cliente.post(f"https://{dominio}/api/checkout/pub/orderForms/simulation",
                                params={"sc": 1}, json=extratores.corpo_simulacao_frete(o, cep))
            o.frete, o.valor_frete = extratores.interpretar_simulacao_frete(resp.json())
        except (SiteBloqueado, ValueError) as motivo:
            log.info("Frete não verificado em %s: %s", dominio, motivo)


# --------------------------------------------------------------------------- execução
def executar(cfg: dict, simular: bool = False) -> int:
    crit = cfg["criterios"]
    cliente = ClienteEducado(cfg["educacao_http"])
    dados = historico.carregar()
    falhas = {}

    brutas = coletar_vtex(cliente, cfg, falhas) + coletar_urls(cliente, cfg, falhas)

    # Remove duplicadas (mesma URL vinda de termos de busca diferentes)
    unicas = {}
    for o in brutas:
        if o.url not in unicas or o.preco < unicas[o.url].preco:
            unicas[o.url] = o

    aprovadas = []
    for o in unicas.values():
        veredito = filtros.avaliar(o.texto_busca or o.titulo, crit, cfg["modelos"], o.descricao)
        if not veredito["aprovado"]:
            log.debug("Descartada (%s): %s", veredito["motivo"], o.titulo)
            continue
        o.btu = veredito["btu"]
        o.modelo_id = veredito["modelo"]["id"] if veredito["modelo"] else ""
        o.avisos += veredito["avisos"]
        aprovadas.append(o)
    log.info("Coletadas: %d | únicas: %d | passaram nos filtros: %d",
             len(brutas), len(unicas), len(aprovadas))

    candidatas_frete = [o for o in aprovadas if o.preco <= crit["preco_maximo"]]
    cep = "".join(c for c in segredo("CEP_ENTREGA") if c.isdigit())   # aceita "60000-000"
    verificar_frete(cliente, candidatas_frete, cep)

    validas = regras.aplicar_regras_de_compra(aprovadas, crit,
                                              cfg.get("lojas_com_instalacao_inclusa") or [])
    urls_validas = {o.url for o in validas}
    al = cfg["alertas"]
    quedas = regras.detectar_quedas(validas, dados, al["queda_percentual_minima"])
    diferencas = [d for d in regras.detectar_diferencas(
                      [o for o in aprovadas if o.disponivel], dados,
                      al["diferenca_percentual_minima"], al["diferenca_reais_minima"])
                  if d["barata"].url in urls_validas]

    # Sites que falharam 3 execuções seguidas ganham um aviso para você revisar o config
    for site in set(list(falhas) + list(dados["falhas_por_site"])):
        dados["falhas_por_site"][site] = dados["falhas_por_site"].get(site, 0) + 1 if site in falhas else 0
    cronicos = [s for s, n in dados["falhas_por_site"].items() if n >= 3]
    nota = (f"Falharam 3+ execuções seguidas: {', '.join(cronicos)}. "
            "Considere remover do config.yaml.") if cronicos else ""

    mensagem = notificadores.formatar_resumo(validas, quedas, diferencas, falhas, nota)

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
    for q in quedas:
        historico.marcar_alertado(dados, q["chave"])
    for o in aprovadas:
        historico.registrar_preco(dados, o.url, o.preco)
    historico.salvar(dados)

    # Se NENHUMA fonte respondeu, devolvemos erro para o GitHub marcar a execução em vermelho
    return 1 if (not brutas and falhas) else 0


def diagnosticar(cfg: dict) -> int:
    """Testa cada loja uma vez e imprime um relatório. Não envia nada."""
    cliente = ClienteEducado(cfg["educacao_http"])
    print(f"User-Agent usado: {cliente.user_agent}\n")
    for loja in cfg["lojas_vtex"]:
        dom = loja["dominio"]
        busca = extratores.url_busca_vtex(dom)
        sim = f"https://{dom}/api/checkout/pub/orderForms/simulation"
        linha = f"{loja['nome']:<22} | robots busca: {'OK' if cliente.permitido(busca) else 'PROIBIDO'}"
        linha += f" | robots frete: {'OK' if cliente.permitido(sim) else 'PROIBIDO'}"
        try:
            resp = cliente.get(busca, params=extratores.parametros_busca_vtex(cfg["termos_busca"][0]))
            dados = resp.json()
            linha += f" | busca: OK ({len(dados) if isinstance(dados, list) else '?'} produtos)"
        except SiteBloqueado as motivo:
            linha += f" | busca: RECUSADA ({motivo})"
        except ValueError:
            linha += " | busca: NÃO É VTEX (resposta não é JSON)"
        print(linha)
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
    parser.add_argument("--diagnostico", action="store_true", help="testa as lojas")
    parser.add_argument("--testar", choices=["telegram", "agenda"],
                        help="envia uma mensagem/evento de teste e encerra")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostra os descartes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Segurança: o urllib3 (usado pelo requests), em modo DEBUG, escreve a URL completa no log.
    # A URL do Telegram contém o token do bot -> silenciamos esse logger.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if args.testar:
        sys.exit(testar_integracoes(args.testar))
    cfg = carregar_config(args.config)
    sys.exit(diagnosticar(cfg) if args.diagnostico else executar(cfg, simular=args.simular))


if __name__ == "__main__":
    main()
