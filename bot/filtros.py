"""filtros.py — decide se um anúncio é o ar-condicionado que você quer.

Toda função aqui é "pura": recebe texto, devolve resposta, não acessa rede.
Isso facilita testar (veja tests/test_filtros.py).
"""
import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento e com espaços simples: 'Só Frio' -> 'so frio'."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.lower()).strip()


# Capacidades possíveis em anúncios de split. A ordem importa: testamos as maiores
# primeiro para "12000" não ser confundido com outra coisa.
_CAPACIDADES = [60000, 58000, 48000, 36000, 30000, 24000, 22000, 18000, 12000, 9000, 7000, 7500]


def detectar_btus(texto_norm: str) -> set:
    """Devolve o conjunto de capacidades citadas: '9.000 btus', '9000', '9k', '9 mil'."""
    achadas = set()
    for cap in _CAPACIDADES:
        mil = cap // 1000                      # 9000 -> 9
        meio = f"{cap:,}".replace(",", ".")    # 9000 -> "9.000"
        padroes = [
            rf"(?<![\d.]){cap}(?!\d)",                       # 9000
            rf"(?<![\d.]){re.escape(meio)}(?!\d)",           # 9.000
            rf"(?<![\d.]){mil}\s?k(?![a-z0-9])",              # 9k / 9 k
            rf"(?<![\d.]){mil}\s?mil\b",                      # 9 mil
        ]
        if any(re.search(p, texto_norm) for p in padroes):
            achadas.add(cap)
    return achadas


def detectar_marca(texto_norm: str, marcas: dict):
    """Devolve a chave da marca ('gree', 'midea'...) ou None."""
    for marca, apelidos in marcas.items():
        for apelido in apelidos:
            if re.search(rf"\b{re.escape(apelido)}\b", texto_norm):
                return marca
    return None


_QUENTE = [r"\bquente\b", r"\bq\s?/\s?f\b", r"\bqf\b", r"aquecimento", r"\bheat\b", r"\bciclo reverso\b"]


def e_quente_frio(texto_norm: str, codigos_quente_frio: list) -> bool:
    if any(re.search(p, texto_norm) for p in _QUENTE):
        return True
    return any(cod in texto_norm for cod in codigos_quente_frio)


def voltagem_ok(texto_norm: str, voltagem: int):
    """True = 220V citado; False = só 110/127V citado; None = não informado."""
    tem_220 = re.search(rf"\b{voltagem}\s?v?\b|\b{voltagem}\s?volts\b", texto_norm) is not None
    tem_outra = re.search(r"\b(110|127)\s?v(olts)?\b", texto_norm) is not None
    if tem_220:
        return True
    if tem_outra:
        return False
    return None


def identificar_modelo(texto_norm: str, modelos: list):
    """Procura códigos de fábrica no texto e devolve o modelo (dict) ou None."""
    compacto = texto_norm.replace(" ", "")
    for modelo in modelos:
        for codigo in modelo["codigos"]:
            if codigo in compacto:
                return modelo
    return None


def avaliar(titulo: str, criterios: dict, modelos: list, texto_extra: str = "",
            modelo_forcado: dict | None = None) -> dict:
    """Aplica TODOS os critérios e devolve um dict com o veredito e o motivo.

    titulo      -> nome do produto + marca + códigos (onde aplicamos todas as regras)
    texto_extra -> descrição longa; usada SÓ para achar o código de fábrica, porque
                   descrições falam "dias quentes" e confundiriam a regra de só frio.
    modelo_forcado -> modelo informado por você no config.yaml (campo `modelo` da URL).
                   Tem prioridade sobre a detecção automática por código de fábrica,
                   porque algumas lojas publicam títulos genéricos, sem o código.

    Exemplo de retorno:
      {"aprovado": True, "marca": "gree", "btu": 9000, "modelo": {...},
       "avisos": ["voltagem não informada"], "motivo": ""}
    """
    t = normalizar(titulo)
    resultado = {"aprovado": False, "marca": None, "btu": None, "modelo": None,
                 "avisos": [], "motivo": ""}

    for palavra in criterios["palavras_proibidas"]:
        if normalizar(palavra) in t:
            resultado["motivo"] = f"parece peça/acessório ou usado ('{palavra}')"
            return resultado

    marca = detectar_marca(t, criterios["marcas"])
    if not marca:
        resultado["motivo"] = "marca fora da lista"
        return resultado
    resultado["marca"] = marca

    if criterios.get("exigir_inverter") and "inverter" not in t:
        resultado["motivo"] = "não é inverter"
        return resultado

    detectado = identificar_modelo(t + " " + normalizar(texto_extra), modelos)
    modelo = modelo_forcado or detectado
    if modelo_forcado and detectado and detectado["id"] != modelo_forcado["id"]:
        # Proteção contra erro humano no config: a página mostra o código de OUTRO modelo
        resultado["avisos"].append(f"CONFIRA o config: a página traz o código de '{detectado['id']}', "
                                   f"mas a URL está marcada como '{modelo_forcado['id']}'")
    btus = detectar_btus(t)
    if modelo:                      # o código de fábrica é mais confiável que o texto
        btus = {modelo["btu"]}
    permitidas = set(criterios["capacidades_btu"])
    if not btus:
        resultado["motivo"] = "capacidade (BTU) não identificada"
        return resultado
    if not btus <= permitidas:      # citou alguma capacidade fora de 9k/12k
        resultado["motivo"] = f"capacidade fora do desejado: {sorted(btus)}"
        return resultado
    resultado["btu"] = min(btus)
    resultado["modelo"] = modelo

    if criterios.get("exigir_so_frio") and e_quente_frio(t, criterios["codigos_quente_frio"]):
        resultado["motivo"] = "versão quente e frio"
        return resultado

    v = voltagem_ok(t, criterios["voltagem"])
    if v is False:
        resultado["motivo"] = "voltagem diferente de 220V"
        return resultado
    if v is None:
        resultado["avisos"].append("voltagem não informada no anúncio")

    resultado["aprovado"] = True
    return resultado
