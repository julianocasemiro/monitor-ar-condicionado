"""notificadores.py — Telegram e Google Agenda.

Telegram: API oficial de bots (https://core.telegram.org/bots/api), método sendMessage.
Agenda: chamamos um "webhook" (Web App do Google Apps Script, arquivo apps_script/Codigo.gs)
que roda COM A SUA CONTA e cria o evento no seu calendário principal.
"""
import html
import logging

import requests

LIMITE_TELEGRAM = 4000   # a API aceita até 4096 caracteres por mensagem; deixamos folga

log = logging.getLogger("bot")


def _br(valor: float) -> str:
    """1234.5 -> 'R$ 1.234,50'"""
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


ROTULO_FRETE = {"gratis": "frete grátis", "pago": "frete pago",
                "sem_entrega": "não entrega no CEP", "nao_verificado": "frete ?"}


def formatar_oferta(o) -> str:
    linha = (f"• <b>{_br(o.preco)}</b> — {html.escape(o.titulo[:90])}\n"
             f"  🏬 {html.escape(o.loja)} | 🚚 {ROTULO_FRETE.get(o.frete, o.frete)}")
    if o.avisos:
        linha += "\n  ⚠️ " + html.escape("; ".join(o.avisos))
    return linha + f"\n  <a href=\"{html.escape(o.url, quote=True)}\">abrir anúncio</a>"


def _secoes_de_eventos(quedas, diferencas, retornos, novas) -> list:
    partes = []
    for d in diferencas:
        partes.append(
            f"🔥 <b>DIFERENÇA GRANDE ({d['pct']:.0f}% / {_br(d['dif'])})</b>\n"
            f"{html.escape(d['barata'].titulo[:80])}\n"
            f"Mais barato: {_br(d['barata'].preco)} em {html.escape(d['barata'].loja)}\n"
            f"Mais caro: {_br(d['cara'].preco)} em {html.escape(d['cara'].loja)}\n"
            f"<a href=\"{html.escape(d['barata'].url, quote=True)}\">abrir oferta</a>")
    for q in quedas:
        o = q["oferta"]
        partes.append(f"📉 <b>Caiu {q['pct']:.0f}%</b> (maior preço em 24 h: {_br(q['anterior'])}"
                      f" → agora {_br(o.preco)})\n" + formatar_oferta(o))
    for r in retornos:
        partes.append("🟢 <b>VOLTOU AO ESTOQUE</b> (dentro do teto)\n" + formatar_oferta(r["oferta"]))
    for n in novas:
        partes.append("🆕 <b>OFERTA NOVA</b> dentro dos critérios\n" + formatar_oferta(n["oferta"]))
    return partes


NOTA_PRECO = ("💳 Preço lido da página da loja (em geral o do cartão). "
              "O Pix costuma sair mais barato. Confira frete para Fortaleza no site.")


def formatar_alertas(quedas, diferencas, retornos=(), novas=()) -> str:
    """Mensagem curta, enviada a qualquer hora, só com os eventos."""
    partes = ["🚨 <b>Novidade no monitor de ar-condicionado</b>"]
    partes += _secoes_de_eventos(quedas, diferencas, retornos, novas)
    partes.append(NOTA_PRECO)
    return "\n\n".join(partes)


def formatar_resumo(validas, quedas, diferencas, bloqueios, nao_testadas,
                    retornos=(), esgotadas=0, novas=(),
                    titulo="❄️ <b>Resumo diário — monitor de ar-condicionado</b>") -> str:
    """Resumo completo, enviado uma vez por dia (e na prévia do modo --simular)."""
    partes = [titulo]
    partes += _secoes_de_eventos(quedas, diferencas, retornos, novas)
    if validas:
        partes.append(f"<b>{len(validas)} oferta(s) dentro dos critérios</b> (menor preço primeiro):")
        partes += [formatar_oferta(o) for o in validas[:15]]
    else:
        partes.append("Nenhuma oferta dentro dos critérios agora.")
    if esgotadas:
        partes.append(f"⏸️ {esgotadas} página(s) vigiada(s) sem estoque agora — aviso quando voltarem.")
    if bloqueios:
        partes.append("🚫 Falhas nesta rodada (recusas são respeitadas, sem nova tentativa):\n"
                      + "\n".join(f"• {html.escape(d)}: {html.escape(m)}" for d, m in bloqueios.items()))
    if nao_testadas:
        partes.append("ℹ️ " + html.escape(nao_testadas))
    partes.append(NOTA_PRECO)
    return "\n\n".join(partes)


def enviar_telegram(token: str, chat_id: str, texto: str):
    """Envia em pedaços se passar do limite. Usa a API oficial via HTTPS."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    pedacos, atual = [], ""
    for bloco in texto.split("\n\n"):
        if len(atual) + len(bloco) + 2 > LIMITE_TELEGRAM:
            pedacos.append(atual)
            atual = ""
        atual += ("\n\n" if atual else "") + bloco
    pedacos.append(atual)
    for pedaco in pedacos:
        resp = requests.post(url, timeout=30, json={
            "chat_id": chat_id, "text": pedaco, "parse_mode": "HTML",
            "disable_web_page_preview": True})
        if not resp.ok:
            # NUNCA logamos a URL (ela contém o token). Só o status e a descrição do erro.
            log.error("Telegram recusou a mensagem: HTTP %s %s", resp.status_code,
                      resp.json().get("description", "") if resp.content else "")
            resp.raise_for_status()


def criar_evento_agenda(url_webhook: str, segredo: str, diferenca: dict) -> bool:
    b, c = diferenca["barata"], diferenca["cara"]
    payload = {
        "token": segredo,
        "titulo": f"🔥 AR-CONDICIONADO {diferenca['pct']:.0f}% mais barato na {b.loja}",
        "descricao": (f"{b.titulo}\n\nMais barato: {_br(b.preco)} — {b.loja}\n{b.url}\n\n"
                      f"Mais caro: {_br(c.preco)} — {c.loja}\n{c.url}\n\n"
                      "Criado automaticamente pelo monitor de preços."),
        "link": b.url,
    }
    try:
        # O Apps Script responde com um redirecionamento (302) para entregar o resultado;
        # o requests segue o redirecionamento sozinho.
        resp = requests.post(url_webhook, json=payload, timeout=40)
        ok = resp.ok and '"ok":true' in resp.text.replace(" ", "")
        if not ok:
            log.error("Webhook da agenda respondeu sem sucesso (HTTP %s)", resp.status_code)
        return ok
    except requests.RequestException as erro:
        log.error("Falha ao chamar o webhook da agenda: %s", erro.__class__.__name__)
        return False
