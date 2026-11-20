"""Testes automáticos. Rode com:  python -m pytest -v

Cada teste verifica UMA regra. Se alguém (você no futuro!) mudar o código e quebrar
uma regra, o teste fica vermelho no GitHub antes do bot ir para produção.
"""
import json
import os

import pytest
import yaml

from bot import extratores, filtros, historico, regras

RAIZ = os.path.dirname(os.path.dirname(__file__))
FIX = os.path.join(RAIZ, "tests", "fixtures")


@pytest.fixture(scope="module")
def cfg():
    with open(os.path.join(RAIZ, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def avaliar(texto, cfg):
    return filtros.avaliar(texto, cfg["criterios"], cfg["modelos"])


# ------------------------------------------------------------------ filtros
@pytest.mark.parametrize("texto,btus", [
    ("Split 9.000 BTUs", {9000}), ("split 9000btus", {9000}), ("Gree 9k frio", {9000}),
    ("12 mil btus", {12000}), ("18.000 BTUs", {18000}), ("modelo GWC09ATB", set()),
])
def test_detectar_btus(texto, btus):
    assert filtros.detectar_btus(filtros.normalizar(texto)) == btus


def test_aprova_midea_9k_so_frio(cfg):
    r = avaliar("Ar Condicionado Midea AI Ecomaster 9.000 BTUs Inverter Frio 220V 42EZVCA09M5", cfg)
    assert r["aprovado"] and r["btu"] == 9000
    assert r["modelo"]["id"] == "midea-ai-ecomaster-9k-frio-vca"


@pytest.mark.parametrize("texto,motivo", [
    ("Ar Condicionado Gree 9000 BTUs Inverter Quente e Frio 220V", "quente"),
    ("Ar Condicionado Gree 9000 BTUs Inverter Frio GWH09ATA 220V", "quente"),
    ("Ar Condicionado Samsung 9000 BTUs Inverter Frio 220V", "marca"),
    ("Ar Condicionado Elgin 9000 BTUs Frio 220V", "inverter"),
    ("Ar Condicionado Elgin 18000 BTUs Inverter Frio 220V", "capacidade"),
    ("Ar Condicionado Elgin 9000 BTUs Inverter Frio 127V", "voltagem"),
    ("Kit Instalação Gree 9000 BTUs Inverter", "acessório"),
])
def test_rejeita(texto, motivo, cfg):
    r = avaliar(texto, cfg)
    assert not r["aprovado"], f"deveria rejeitar: {texto}"


def test_voltagem_nao_informada_gera_aviso(cfg):
    r = avaliar("Ar Condicionado Elgin Eco II 9000 BTUs Inverter Frio", cfg)
    assert r["aprovado"] and "voltagem" in r["avisos"][0]


def test_descricao_com_quente_nao_derruba(cfg):
    r = filtros.avaliar("Gree G-Top Auto 9000 BTUs Inverter Só Frio 220V", cfg["criterios"],
                        cfg["modelos"], texto_extra="Perfeito para os dias quentes. GWC09ATB-D6DNA1A")
    assert r["aprovado"] and r["modelo"]["id"] == "gree-gtop-auto-9k-frio"


# ------------------------------------------------------------------ extratores
def test_extrair_vtex():
    with open(os.path.join(FIX, "vtex_busca.json"), encoding="utf-8") as f:
        ofertas = extratores.extrair_vtex(json.load(f), "Loja A", "www.loja-a.com.br")
    assert len(ofertas) == 4                     # o Elgin esgotado fica de fora
    assert ofertas[0].preco == 1799.0 and ofertas[0].item_id == "101"
    assert "42EZVCA09M5" in ofertas[0].texto_busca


def test_extrair_jsonld():
    with open(os.path.join(FIX, "pagina_produto.html"), encoding="utf-8") as f:
        o = extratores.extrair_jsonld(f.read(), "https://x.com/p", "Loja X")
    assert o.preco == 2190.90 and o.disponivel and "GWC09ATB" in o.texto_busca
    assert "quentes" not in o.texto_busca         # descrição fica separada


def test_simulacao_frete():
    with open(os.path.join(FIX, "simulacao_frete.json"), encoding="utf-8") as f:
        assert extratores.interpretar_simulacao_frete(json.load(f)) == ("gratis", 0.0)
    pago = {"logisticsInfo": [{"slas": [{"deliveryChannel": "delivery", "price": 12990}]}]}
    assert extratores.interpretar_simulacao_frete(pago) == ("pago", 129.9)
    assert extratores.interpretar_simulacao_frete({"logisticsInfo": [{"slas": []}]})[0] == "sem_entrega"


# ------------------------------------------------------------------ regras
def _oferta(loja, preco, btu=9000, modelo="m1", frete="gratis"):
    return extratores.Oferta(loja=loja, url=f"https://{loja}/{preco}", titulo="x", preco=preco,
                             btu=btu, modelo_id=modelo, frete=frete)


def test_regra_12k_e_teto_e_frete(cfg):
    ofertas = [_oferta("a", 2000), _oferta("b", 2100, btu=12000), _oferta("c", 1900, btu=12000),
               _oferta("d", 2600), _oferta("e", 1500, frete="pago")]
    validas = regras.aplicar_regras_de_compra(ofertas, cfg["criterios"], [])
    assert [o.loja for o in validas] == ["c", "a"]   # 12k só se mais barato que o melhor 9k


def test_diferenca_grande():
    dados = {"precos": {}, "alertas_enviados": {}, "falhas_por_site": {}}
    achados = regras.detectar_diferencas([_oferta("a", 1700), _oferta("b", 2300)], dados, 15, 250)
    assert len(achados) == 1 and achados[0]["barata"].loja == "a"
    historico.marcar_alertado(dados, achados[0]["chave"])
    assert regras.detectar_diferencas([_oferta("a", 1700), _oferta("b", 2300)], dados, 15, 250) == []


def test_queda_de_preco():
    dados = {"precos": {}, "alertas_enviados": {}, "falhas_por_site": {}}
    o = _oferta("a", 1800)
    historico.registrar_preco(dados, o.url, 2000)
    assert regras.detectar_quedas([o], dados, 8)[0]["pct"] == pytest.approx(10)
