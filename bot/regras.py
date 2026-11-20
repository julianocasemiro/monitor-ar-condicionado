"""regras.py — depois de filtrar, decide o que vira aviso.

Três tipos de saída:
  1. Resumo: todas as ofertas válidas (vai no Telegram, ordenado por preço).
  2. Queda: a MESMA URL ficou X% mais barata que na última execução (Telegram).
  3. Diferença grande: o MESMO modelo custa muito menos numa loja do que em outra
     (Telegram 🔥 + evento no Google Agenda).
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


def detectar_quedas(ofertas: list, dados: dict, queda_min_pct: float) -> list:
    quedas = []
    for o in ofertas:
        anterior = historico.ultimo_preco(dados, o.url)
        if not anterior or o.preco >= anterior:
            continue
        pct = (anterior - o.preco) / anterior * 100
        chave = f"queda|{o.url}|{o.preco:.0f}"
        if pct >= queda_min_pct and not historico.ja_alertado(dados, chave):
            quedas.append({"oferta": o, "anterior": anterior, "pct": pct, "chave": chave})
    return quedas


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
