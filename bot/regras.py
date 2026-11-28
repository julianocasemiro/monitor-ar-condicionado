"""regras.py — depois de filtrar, decide o que vira aviso.

O bot roda de hora em hora, mas só manda mensagem quando há um EVENTO:
  📉 Queda: a MESMA URL ficou X% mais barata que o MAIOR preço das últimas 24 h.
  🟢 Voltou ao estoque: a página estava esgotada e voltou, dentro do teto.
  🆕 Oferta nova: uma página que nunca tinha sido oferta válida passou a ser.
  🔥 Diferença grande: o MESMO modelo custa muito menos numa loja do que em outra
     (Telegram + evento no Google Agenda).
Além disso, uma vez por dia sai o resumo completo ("batimento cardíaco": prova que o
bot está vivo mesmo quando não há novidade).
"""
from datetime import datetime, timedelta, timezone

from . import historico

FORTALEZA = timezone(timedelta(hours=-3))


def aplicar_regras_de_compra(ofertas: list, criterios: dict, lojas_instalacao: list) -> list:
    """Teto de preço, regra do 12k e regra de frete. Devolve as ofertas que sobram."""
    teto = criterios["preco_maximo"]
    validas = [o for o in ofertas if o.disponivel and o.preco <= teto]

    if criterios.get("exigir_frete_gratis"):
        mantidas = []
        for o in validas:
            if o.loja in lojas_instalacao:
                o.avisos.append("loja com instalação inclusa")
                mantidas.append(o)
            elif o.frete in ("pago", "sem_entrega"):
                continue                       # confirmado que não serve
            else:
                if o.frete == "nao_verificado":
                    o.avisos.append("frete não verificado — confira no site")
                mantidas.append(o)
        validas = mantidas

    if criterios.get("regra_12k") == "somente_se_mais_barato_que_9k":
        precos_9k = [o.preco for o in validas if o.btu == 9000]
        melhor_9k = min(precos_9k) if precos_9k else None
        validas = [o for o in validas
                   if o.btu != 12000 or melhor_9k is None or o.preco < melhor_9k]
    return sorted(validas, key=lambda o: o.preco)


def detectar_quedas(ofertas: list, dados: dict, queda_min_pct: float,
                    janela_horas: float = 24) -> list:
    """Compara com o MAIOR preço das últimas `janela_horas`, e não com a leitura anterior:
    assim uma queda em degraus (3% + 3% + 3%) também é percebida."""
    quedas = []
    for o in ofertas:
        referencia = historico.maior_preco_recente(dados, o.url, janela_horas)
        if not referencia or o.preco >= referencia:
            continue
        pct = (referencia - o.preco) / referencia * 100
        chave = f"queda|{o.url}|{o.preco:.0f}"
        if pct >= queda_min_pct and not historico.ja_alertado(dados, chave):
            quedas.append({"oferta": o, "anterior": referencia, "pct": pct, "chave": chave})
    return quedas


def detectar_ofertas_novas(validas: list, dados: dict) -> list:
    """Páginas que viraram oferta válida pela primeira vez."""
    vistas = dados.get("validas_vistas") or {}
    return [{"oferta": o, "chave": f"nova|{o.url}"} for o in validas if o.url not in vistas]


def resumo_diario_devido(ultimo_resumo: str, hora_resumo: int, agora_local: datetime) -> bool:
    """True na PRIMEIRA execução do dia a partir da hora marcada.
    "A partir de" (e não "exatamente às") porque o GitHub pode atrasar ou pular uma execução."""
    return agora_local.hour >= hora_resumo and ultimo_resumo != agora_local.date().isoformat()


def detectar_retornos_estoque(validas: list, dados: dict) -> list:
    """Ofertas válidas (disponíveis e dentro do teto) cuja página estava ESGOTADA antes."""
    hoje = datetime.now(FORTALEZA).date().isoformat()
    retornos = []
    for o in validas:
        chave = f"estoque|{o.url}|{hoje}"
        if historico.estoque_anterior(dados, o.url) is False and not historico.ja_alertado(dados, chave):
            retornos.append({"oferta": o, "chave": chave})
    return retornos


def detectar_diferencas(ofertas: list, dados: dict, pct_min: float, reais_min: float) -> list:
    """Agrupa por modelo (código de fábrica) e compara a loja mais barata com a mais cara."""
    grupos = {}
    for o in ofertas:
        if o.modelo_id:
            grupos.setdefault(o.modelo_id, []).append(o)

    hoje = datetime.now(FORTALEZA).date().isoformat()
    achados = []
    for modelo_id, lista in grupos.items():
        # menor preço de cada loja (a mesma loja pode ter 2 anúncios do modelo)
        por_loja = {}
        for o in lista:
            if o.loja not in por_loja or o.preco < por_loja[o.loja].preco:
                por_loja[o.loja] = o
        if len(por_loja) < 2:
            continue
        barata = min(por_loja.values(), key=lambda o: o.preco)
        cara = max(por_loja.values(), key=lambda o: o.preco)
        dif = cara.preco - barata.preco
        pct = dif / cara.preco * 100
        chave = f"dif|{modelo_id}|{barata.loja}|{barata.preco:.0f}|{hoje}"
        if pct >= pct_min and dif >= reais_min and not historico.ja_alertado(dados, chave):
            achados.append({"modelo_id": modelo_id, "barata": barata, "cara": cara,
                            "dif": dif, "pct": pct, "chave": chave})
    return achados
