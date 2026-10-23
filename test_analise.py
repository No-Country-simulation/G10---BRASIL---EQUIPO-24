"""
test_analise.py: testes do analise.py SEM chave de API e SEM internet.

Usamos um "Gemini de mentira": um cliente falso que devolve respostas prontas.
Assim dá para conferir que a nossa lógica (validação, retentativa, erros)
funciona, mesmo sem chamar o Gemini de verdade.

COMO RODAR:
    python test_analise.py
(ou, se tiver o pytest instalado:  pytest test_analise.py)
"""

import json

from analise import AnaliseDocumento, analisar_documento
from extractor import ErroExtracao
from schemas import TipoDocumento


# ---------------------------------------------------------------------------
# O "Gemini de mentira"
# ---------------------------------------------------------------------------
class RespostaFalsa:
    def __init__(self, texto: str):
        self.text = texto


class ModelosFalsos:
    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = 0

    def generate_content(self, **kwargs):
        self.chamadas += 1
        return RespostaFalsa(self._respostas.pop(0))


class ClienteFalso:
    def __init__(self, *respostas):
        self.models = ModelosFalsos(respostas)


# ---------------------------------------------------------------------------
# Respostas de exemplo (baseadas no exemplo do desafio: laudo de TEP)
# ---------------------------------------------------------------------------
def resposta_tep(tipo="Laudo de Exame de Imagem", com_cercas=False) -> str:
    corpo = json.dumps(
        {
            "tipo_documento": tipo,
            "especialidade": "Radiologia / Pneumologia",
            "paciente": {"nome": "Carlos Eduardo Mendes", "idade": 52},
            "medico_solicitante": {"nome": "Dra. Renata Silveira", "crm": "CRM-SP 145892"},
            "exame_realizado": "Tomografia de Torax com contraste",
            "diagnostico_principal": "Tromboembolismo Pulmonar Agudo (TEP)",
            "cid_sugerido": "I26.9",
            "medicamentos": [],
        },
        ensure_ascii=False,
    )
    return f"```json\n{corpo}\n```" if com_cercas else corpo


TEXTO_LAUDO = "HOSPITAL SANTA LUCIA - LAUDO DE EXAME RADIOLOGICO. (texto de exemplo)"


# ---------------------------------------------------------------------------
# Os testes
# ---------------------------------------------------------------------------
def test_classifica_e_extrai_em_uma_chamada():
    cliente = ClienteFalso(resposta_tep())
    analise = analisar_documento(TEXTO_LAUDO, client=cliente)

    assert isinstance(analise, AnaliseDocumento)
    assert analise.tipo_documento == TipoDocumento.LAUDO_IMAGEM
    assert analise.especialidade == "Radiologia / Pneumologia"
    assert analise.dados_extraidos.paciente.nome == "Carlos Eduardo Mendes"
    assert analise.dados_extraidos.medico_solicitante.crm == "145892-SP"  # padronizado
    assert analise.dados_extraidos.cid_sugerido == "I26.9"
    assert cliente.models.chamadas == 1  # UMA chamada só


def test_aceita_resposta_com_cercas_de_markdown():
    cliente = ClienteFalso(resposta_tep(com_cercas=True))
    analise = analisar_documento(TEXTO_LAUDO, client=cliente)
    assert analise.tipo_documento == TipoDocumento.LAUDO_IMAGEM


def test_tipo_desconhecido_e_aceito():
    cliente = ClienteFalso(resposta_tep(tipo="Desconhecido"))
    analise = analisar_documento(TEXTO_LAUDO, client=cliente)
    assert analise.tipo_documento == TipoDocumento.DESCONHECIDO


def test_tipo_invalido_gera_retentativa_e_corrige():
    cliente = ClienteFalso(resposta_tep(tipo="Exame qualquer"), resposta_tep())
    analise = analisar_documento(TEXTO_LAUDO, client=cliente)
    assert analise.tipo_documento == TipoDocumento.LAUDO_IMAGEM
    assert cliente.models.chamadas == 2


def test_duas_respostas_invalidas_levantam_erro_extracao():
    cliente = ClienteFalso("isto não é JSON", resposta_tep(tipo="Outro tipo"))
    try:
        analisar_documento(TEXTO_LAUDO, client=cliente)
    except ErroExtracao:
        return
    raise AssertionError("Era esperado um ErroExtracao.")


def test_documento_vazio_levanta_erro_extracao():
    try:
        analisar_documento("   ", client=ClienteFalso())
    except ErroExtracao:
        return
    raise AssertionError("Era esperado um ErroExtracao.")


if __name__ == "__main__":
    testes = [valor for nome, valor in sorted(globals().items()) if nome.startswith("test_")]
    for teste in testes:
        teste()
        print(f"OK  {teste.__name__}")
    print(f"\n{len(testes)} testes passaram.")
