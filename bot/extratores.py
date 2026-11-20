"""extratores.py — transforma respostas dos sites em "ofertas" padronizadas.

Duas fontes de dados, ambas PÚBLICAS e estruturadas (nada de "raspar" layout visual):
  A) Busca de catálogo VTEX: /api/catalog_system/pub/products/search
     (endpoint público documentado pela VTEX, o mesmo que a vitrine da loja usa).
  B) JSON-LD (schema.org/Product) embutido nas páginas de produto. É o bloco de dados
     que as lojas publicam DE PROPÓSITO para o Google entender preço e disponibilidade.
"""
import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse


@dataclass
class Oferta:
    loja: str
    url: str
    titulo: str
    preco: float
    disponivel: bool = True
    origem: str = ""               # "vtex" ou "jsonld"
    texto_busca: str = ""          # título + marca + códigos, usado nos filtros
    descricao: str = ""            # descrição longa (só para achar código de fábrica)
    item_id: str = ""              # só VTEX (necessário para simular frete)
    seller_id: str = ""
    frete: str = "nao_verificado"  # "gratis" | "pago" | "sem_entrega" | "nao_verificado"
    valor_frete: float = None
    avisos: list = field(default_factory=list)
    modelo_id: str = ""
    btu: int = 0


# ----------------------------------------------------------------------------- A) VTEX
def url_busca_vtex(dominio: str) -> str:
    return f"https://{dominio}/api/catalog_system/pub/products/search"


def parametros_busca_vtex(termo: str) -> dict:
    # _from/_to: paginação (máx. 50 itens por página); O: ordenação por preço crescente
    return {"ft": termo, "_from": 0, "_to": 49, "O": "OrderByPriceASC"}


def extrair_vtex(produtos: list, loja: str, dominio: str) -> list:
    """Converte a lista JSON da busca VTEX em Ofertas (uma por produto, no menor preço)."""
    ofertas = []
    if not isinstance(produtos, list):
        return ofertas
    for p in produtos:
        melhor = None
        textos = [p.get("productName", ""), p.get("brand", ""), p.get("productReference", "")]
        for item in p.get("items", []) or []:
            textos.append(item.get("nameComplete", "") or item.get("name", ""))
            for ref in item.get("referenceId", []) or []:
                textos.append(str(ref.get("Value", "")))
            for seller in item.get("sellers", []) or []:
                oferta_c = seller.get("commertialOffer", {}) or {}   # sim, a VTEX escreve "commertial"
                preco = oferta_c.get("Price") or 0
                disponivel = (oferta_c.get("AvailableQuantity") or 0) > 0
                if preco and disponivel and (melhor is None or preco < melhor[0]):
                    melhor = (float(preco), item.get("itemId", ""), seller.get("sellerId", "1"))
        if not melhor:
            continue
        link = p.get("link") or f"https://{dominio}/{p.get('linkText', '')}/p"
        ofertas.append(Oferta(loja=loja, url=link, titulo=p.get("productName", ""),
                              preco=melhor[0], origem="vtex",
                              texto_busca=" ".join(t for t in textos if t),
                              item_id=str(melhor[1]), seller_id=str(melhor[2])))
    return ofertas


def corpo_simulacao_frete(oferta: Oferta, cep: str) -> dict:
    return {"items": [{"id": oferta.item_id, "quantity": 1, "seller": oferta.seller_id}],
            "postalCode": cep, "country": "BRA"}


def interpretar_simulacao_frete(resposta: dict):
    """Lê a resposta da simulação de carrinho VTEX e devolve (status, valor_em_reais)."""
    infos = (resposta or {}).get("logisticsInfo") or []
    if not infos:
        return "nao_verificado", None
    entregas = [s for s in infos[0].get("slas", []) or []
                if s.get("deliveryChannel", "delivery") == "delivery"]
    if not entregas:
        return "sem_entrega", None
    menor = min(s.get("price", 0) for s in entregas) / 100     # VTEX usa centavos
    return ("gratis", 0.0) if menor == 0 else ("pago", menor)


# ----------------------------------------------------------------------------- B) JSON-LD
class _ColetorJsonLd(HTMLParser):
    """Lê o HTML e guarda o conteúdo de cada <script type="application/ld+json">."""

    def __init__(self):
        super().__init__()
        self.blocos, self._dentro = [], False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._dentro = True
            self.blocos.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._dentro = False

    def handle_data(self, data):
        if self._dentro:
            self.blocos[-1] += data


def _achatar(no):
    """JSON-LD pode vir como objeto, lista ou {'@graph': [...]}. Devolve tudo numa lista."""
    if isinstance(no, list):
        for x in no:
            yield from _achatar(x)
    elif isinstance(no, dict):
        if "@graph" in no:
            yield from _achatar(no["@graph"])
        yield no


def _e_produto(no: dict) -> bool:
    tipo = no.get("@type", "")
    return tipo == "Product" or (isinstance(tipo, list) and "Product" in tipo)


def _preco_de(offers):
    """Aceita Offer, AggregateOffer ou lista de Offers. Devolve (preco, disponivel)."""
    candidatos = []
    for o in _achatar(offers):
        preco = o.get("price") or o.get("lowPrice")
        if preco is None and isinstance(o.get("priceSpecification"), dict):
            preco = o["priceSpecification"].get("price")
        if preco is None:
            continue
        try:
            valor = float(str(preco).replace(",", "."))
        except ValueError:
            continue
        disp = "outofstock" not in str(o.get("availability", "")).lower()
        candidatos.append((valor, disp))
    disponiveis = [c for c in candidatos if c[1]] or candidatos
    return min(disponiveis) if disponiveis else (None, False)


def extrair_jsonld(html: str, url: str, loja: str):
    """Devolve uma Oferta a partir da página de produto, ou None se não houver dados."""
    coletor = _ColetorJsonLd()
    coletor.feed(html)
    for bloco in coletor.blocos:
        try:
            dados = json.loads(bloco.strip())
        except json.JSONDecodeError:
            continue
        for no in _achatar(dados):
            if not _e_produto(no):
                continue
            preco, disponivel = _preco_de(no.get("offers", {}))
            if preco is None:
                continue
            marca = no.get("brand", "")
            marca = marca.get("name", "") if isinstance(marca, dict) else str(marca)
            textos = [no.get("name", ""), marca, str(no.get("sku", "")), str(no.get("mpn", "")),
                      str(no.get("model", ""))]
            return Oferta(loja=loja or urlparse(url).netloc, url=url, titulo=no.get("name", ""),
                          preco=preco, disponivel=disponivel, origem="jsonld",
                          texto_busca=" ".join(t for t in textos if t),
                          descricao=(no.get("description", "") or "")[:1500])
    return None
