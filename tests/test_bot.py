"""Testes automáticos. Rode com:  python -m pytest -v

Cada teste verifica UMA regra. Se alguém (você no futuro!) mudar o código e quebrar
uma regra, o teste fica vermelho no GitHub antes do bot ir para produção.
"""
import os
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from bot import extratores, filtros, historico, main, regras
from bot.http_educado import ClienteEducado, PaginaNaoEncontrada, SiteBloqueado

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
def test_extrair_jsonld():
    with open(os.path.join(FIX, "pagina_produto.html"), encoding="utf-8") as f:
        o = extratores.extrair_jsonld(f.read(), "https://x.com/p", "Loja X")
    assert o.preco == 2190.90 and o.disponivel and "GWC09ATB" in o.texto_busca
    assert "quentes" not in o.texto_busca         # descrição fica separada


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
    dados = historico.carregar("/caminho/que/nao/existe.json")
    achados = regras.detectar_diferencas([_oferta("a", 1700), _oferta("b", 2300)], dados, 15, 250)
    assert len(achados) == 1 and achados[0]["barata"].loja == "a"
    historico.marcar_alertado(dados, achados[0]["chave"])
    assert regras.detectar_diferencas([_oferta("a", 1700), _oferta("b", 2300)], dados, 15, 250) == []


def test_queda_de_preco():
    dados = historico.carregar("/caminho/que/nao/existe.json")
    o = _oferta("a", 1800)
    historico.registrar_preco(dados, o.url, 2000)
    assert regras.detectar_quedas([o], dados, 8)[0]["pct"] == pytest.approx(10)


def test_voltou_ao_estoque():
    dados = historico.carregar("/caminho/que/nao/existe.json")
    o = _oferta("a", 1800)
    historico.registrar_estoque(dados, o.url, False)          # rodada anterior: esgotado
    retornos = regras.detectar_retornos_estoque([o], dados)
    assert len(retornos) == 1
    historico.marcar_alertado(dados, retornos[0]["chave"])
    assert regras.detectar_retornos_estoque([o], dados) == []  # não repete no mesmo dia
    nunca_vista = _oferta("b", 1700)
    assert regras.detectar_retornos_estoque([nunca_vista], dados) == []


def test_modelo_forcado_vale_mais_que_titulo_generico(cfg):
    forcado = main.modelo_por_id(cfg, "midea-ai-ecomaster-9k-frio-vca")
    r = filtros.avaliar("Ar Condicionado 9000 BTUs Inverter | Midea", cfg["criterios"],
                        cfg["modelos"], modelo_forcado=forcado)
    assert r["aprovado"] and r["modelo"]["id"] == "midea-ai-ecomaster-9k-frio-vca"


def test_modelo_conhecido_dispensa_a_palavra_inverter(cfg):
    titulo = "Ar-Condicionado Split 9000 BTUs AI AirVolution Frio Midea"   # título real da Midea
    assert not avaliar(titulo, cfg)["aprovado"]                           # sem modelo: reprova
    forcado = main.modelo_por_id(cfg, "midea-ai-airvolution-9k-frio")
    r = filtros.avaliar(titulo, cfg["criterios"], cfg["modelos"], modelo_forcado=forcado)
    assert r["aprovado"]


def test_voltagem_lida_da_url(cfg):
    r = filtros.avaliar("Ar-Condicionado Split Inverter 9.000 BTUs AI Springer Midea Frio",
                        cfg["criterios"], cfg["modelos"],
                        url="https://www.gazin.com.br/produto/x/11067/sem-cor/220-volts")
    assert r["aprovado"] and r["avisos"] == []


def test_modelo_forcado_errado_gera_aviso(cfg):
    forcado = main.modelo_por_id(cfg, "midea-ai-ecomaster-9k-frio-vca")
    r = filtros.avaliar("Gree G-Top Auto 9000 BTUs Inverter Frio GWC09ATB-D6DNA1A 220V",
                        cfg["criterios"], cfg["modelos"], modelo_forcado=forcado)
    assert any("CONFIRA o config" in a for a in r["avisos"])


# ------------------------------------------------------------------ config
def test_config_do_repositorio_e_valida(cfg):
    main.validar_config(cfg)          # se alguém errar o YAML, o CI fica vermelho aqui


def test_config_com_modelo_inexistente_e_rejeitada(cfg):
    ruim = {**cfg, "urls_vigiadas": [{"loja": "X", "url": "https://x.com/p", "modelo": "nao-existe"}]}
    with pytest.raises(main.ConfigInvalida):
        main.validar_config(ruim)


# ------------------------------------------------------------------ cliente HTTP
class _RespostaFalsa:
    def __init__(self, status, texto="", tipo="text/html"):
        self.status_code, self.text, self.headers = status, texto, {"Content-Type": tipo}


def _cliente_com_resposta(monkeypatch, resposta):
    c = ClienteEducado({"intervalo_segundos": 0, "timeout_segundos": 5,
                        "max_requisicoes_por_site": 5, "respeitar_robots_txt": False})
    monkeypatch.setattr(c.sessao, "request", lambda *a, **k: resposta)
    return c


def test_http_400_vira_recusa_com_mensagem_do_site(monkeypatch):
    c = _cliente_com_resposta(monkeypatch, _RespostaFalsa(400, "Bad Request! Scripts are not allowed!", "text/plain"))
    with pytest.raises(SiteBloqueado, match="Scripts are not allowed"):
        c.get("https://loja.com/api")
    with pytest.raises(SiteBloqueado):                 # o site inteiro fica bloqueado nesta execução
        c.get("https://loja.com/outra-pagina")


def test_http_404_nao_bloqueia_o_site(monkeypatch):
    c = _cliente_com_resposta(monkeypatch, _RespostaFalsa(404))
    with pytest.raises(PaginaNaoEncontrada):
        c.get("https://loja.com/produto-velho")
    assert "loja.com" not in c.bloqueados


# ------------------------------------------------------------------ ponta a ponta
def test_execucao_simulada_ponta_a_ponta(cfg, monkeypatch, capsys, tmp_path):
    """Roda o fluxo inteiro com uma página falsa, sem internet: coleta -> filtros -> mensagem."""
    monkeypatch.chdir(tmp_path)                        # histórico vazio, num diretório temporário
    with open(os.path.join(FIX, "pagina_produto.html"), encoding="utf-8") as f:
        html = f.read()
    monkeypatch.setattr(ClienteEducado, "get", lambda self, url, **k: _RespostaFalsa(200, html))
    mini = {**cfg, "urls_vigiadas": [{"loja": "Loja X", "url": "https://x.com/p",
                                      "modelo": "gree-gtop-auto-9k-frio"}]}
    assert main.executar(mini, simular=True) == 0
    saida = capsys.readouterr().out
    assert "1 oferta(s) dentro dos critérios" in saida and "R$ 2.190,90" in saida


# ------------------------------------------------------------------ v2: alertas por evento
def _iso(horas_atras):
    return (datetime.now(UTC) - timedelta(hours=horas_atras)).isoformat(timespec="seconds")


def test_preco_so_e_gravado_quando_muda():
    dados = historico.carregar("/caminho/que/nao/existe.json")
    assert historico.registrar_preco(dados, "u", 2000) is True
    assert historico.registrar_preco(dados, "u", 2000) is False     # igual: não grava
    assert historico.registrar_preco(dados, "u", 1900) is True
    assert [p for _, p in dados["precos"]["u"]] == [2000, 1900]


@pytest.mark.parametrize("serie,esperado", [
    ([(30, 2500), (5, 2000)], 2500),   # 2.500 vigorou até 5 h atrás: conta na janela
    ([(48, 2500), (30, 2000)], 2000),  # 2.500 saiu de vigor há 30 h: não conta
    ([(3, 1900)], 1900),
])
def test_maior_preco_das_ultimas_24h(serie, esperado):
    dados = historico.carregar("/caminho/que/nao/existe.json")
    for horas, preco in serie:
        historico.registrar_preco(dados, "u", preco, quando=_iso(horas))
    assert historico.maior_preco_recente(dados, "u", 24) == esperado


def test_queda_em_degraus_e_percebida():
    """3 quedas pequenas seguidas: nenhuma passa de 8% sozinha, mas juntas passam."""
    dados = historico.carregar("/caminho/que/nao/existe.json")
    o = _oferta("a", 1830)
    for horas, preco in [(6, 2000), (4, 1940), (2, 1885)]:
        historico.registrar_preco(dados, o.url, preco, quando=_iso(horas))
    quedas = regras.detectar_quedas([o], dados, 8, 24)
    assert len(quedas) == 1 and quedas[0]["anterior"] == 2000


def test_oferta_nova():
    dados = historico.carregar("/caminho/que/nao/existe.json")
    historico.marcar_validas_vistas(dados, ["https://a/1800"])
    novas = regras.detectar_ofertas_novas([_oferta("a", 1800), _oferta("b", 1700)], dados)
    assert [n["oferta"].loja for n in novas] == ["b"]


@pytest.mark.parametrize("ultimo,hora_agora,devido", [
    ("", 18, True), ("", 17, False), ("", 23, True),   # a partir das 18 h, se ainda não saiu
    ("2026-11-28", 19, False),                         # já saiu hoje
    ("2026-11-27", 20, True),                          # o último foi ontem
])
def test_resumo_diario_devido(ultimo, hora_agora, devido):
    agora = datetime(2026, 11, 28, hora_agora, 17, tzinfo=regras.FORTALEZA)
    assert regras.resumo_diario_devido(ultimo, 18, agora) is devido


def test_falha_cronica_medida_em_horas():
    dados = historico.carregar("/caminho/que/nao/existe.json")
    historico.atualizar_falhas(dados, {"site-a", "site-b"})
    dados["falhas_desde"]["site-a"] = _iso(25)          # falha há 25 h
    assert historico.falhas_cronicas(dados, 24) == ["site-a"]
    historico.atualizar_falhas(dados, {"site-b"})       # site-a voltou a funcionar
    assert "site-a" not in dados["falhas_desde"]


def test_historico_antigo_e_migrado(tmp_path):
    caminho = tmp_path / "historico.json"
    caminho.write_text('{"precos": {}, "falhas_por_site": {"x": 2}}', encoding="utf-8")
    dados = historico.carregar(str(caminho))
    assert "falhas_por_site" not in dados and dados["falhas_desde"] == {}
    assert "validas_vistas" not in dados     # sinal de "primeira execução no formato novo"


@pytest.mark.parametrize("texto,aprovado", [
    ("Ar Condicionado Split Inverter TCL T-Pro 2.0 9000 BTUs Só Frio TAC-09CTG2-INV 220V", True),
    ("Ar Condicionado Split TCL T-Pro 2.0 Inverter 9000 BTUs 220V TAC-09CHTG2", False),
    ("Ar Condicionado Split Inverter Agratto Zen Top 9000 BTU/h ZICST9QF 220 Volts", False),
    ("Ar Condicionado Split Inverter Agratto Zen Top 9000 BTU/h Frio ZICST9F 220 Volts", True),
    ("Ar Condicionado Philco Split Inverter 9000BTUs Quente/Frio PAC9FB 220v", False),
    ("Ar-Condicionado Split Inverter 9000 BTUs LG Dual Inverter Voice Só Frio S3-Q09AA31F 220V", True),
])
def test_marcas_novas(texto, aprovado, cfg):
    assert avaliar(texto, cfg)["aprovado"] is aprovado


def test_lg_nao_casa_dentro_de_elgin(cfg):
    t = filtros.normalizar("Ar Condicionado Elgin Eco Inverter 9000")
    assert filtros.detectar_marca(t, cfg["criterios"]["marcas"]) == "elgin"


def test_config_com_hora_do_resumo_invalida(cfg):
    ruim = {**cfg, "notificacoes": {**cfg["notificacoes"], "resumo_diario_hora": 25}}
    with pytest.raises(main.ConfigInvalida, match="resumo_diario_hora"):
        main.validar_config(ruim)


def test_so_avisa_quando_ha_evento(cfg, monkeypatch, tmp_path):
    """3 execuções reais (Telegram falso): 1ª registra em silêncio, 2ª nada muda,
    3ª o preço cai 10% -> só então sai mensagem."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(regras, "resumo_diario_devido", lambda *a: False)
    enviadas = []
    monkeypatch.setattr(main.notificadores, "enviar_telegram", lambda t, c, msg: enviadas.append(msg))
    with open(os.path.join(FIX, "pagina_produto.html"), encoding="utf-8") as f:
        pagina = f.read()
    estado = {"html": pagina}
    monkeypatch.setattr(ClienteEducado, "get", lambda self, url, **k: _RespostaFalsa(200, estado["html"]))
    mini = {**cfg, "urls_vigiadas": [{"loja": "Loja X", "url": "https://x.com/p",
                                      "modelo": "gree-gtop-auto-9k-frio"}]}

    assert main.executar(mini) == 0 and enviadas == []        # 1ª: registra em silêncio
    assert main.executar(mini) == 0 and enviadas == []        # 2ª: nada mudou
    estado["html"] = pagina.replace("2190.90", "1971.81")      # 3ª: caiu 10%
    assert main.executar(mini) == 0
    assert len(enviadas) == 1 and "Caiu 10%" in enviadas[0]
    assert main.executar(mini) == 0 and len(enviadas) == 1    # 4ª: mesmo preço, não repete
    dados = historico.carregar()
    assert [p for _, p in dados["precos"]["https://x.com/p"]] == [2190.90, 1971.81]
