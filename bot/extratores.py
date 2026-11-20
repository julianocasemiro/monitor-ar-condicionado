"""extratores.py — transforma páginas de produto em "ofertas" padronizadas.

Fonte de dados: o JSON-LD (JSON for Linked Data, padrão schema.org/Product) embutido nas
páginas de produto. É o bloco de dados que as lojas publicam DE PROPÓSITO para o Google
entender nome, preço e estoque. Nada de "raspar" o layout visual da página.

(A busca de catálogo VTEX da v1 foi removida: as lojas recusaram buscas automáticas.
 Detalhes em docs/DECISOES.md, ADR-008.)
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
    origem: str = ""               # hoje sempre "jsonld"
    texto_busca: str = ""          # título + marca + códigos, usado nos filtros
    descricao: str = ""            # descrição longa (só para achar código de fábrica)
    frete: str = "nao_verificado"  # "gratis" | "pago" | "sem_entrega" | "nao_verificado"
    avisos: list = field(default_factory=list)
    modelo_id: str = ""
    btu: int = 0


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
