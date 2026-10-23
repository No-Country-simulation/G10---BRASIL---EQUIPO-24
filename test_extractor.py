"""
Testes da Etapa 2. Rode com:  pytest -v

TRUQUE DIDÁTICO: "cliente falso" (fake / mock)
Em vez de chamar o Gemini de verdade (lento, consome cota e pode variar),
usamos um objeto que imita o Gemini e devolve respostas que nós escolhemos.
Assim testamos NOSSA lógica (validação, retentativa, erros) de forma rápida,
grátis e sempre com o mesmo resultado.
"""

import json

import pytest

from extractor import ErroExtracao, extrair_dados, limpar_json, mime_por_extensao


# ----- o "Gemini de mentira" -----
class _Resposta:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []  # guardamos o que foi enviado, para conferir depois

    def generate_content(self, model, contents, config=None):
        self.chamadas.append(contents)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Resposta(r)


class ClienteFalso:
    def __init__(self, respostas):
        self.models = _Models(respostas)


JSON_OK = json.dumps(
    {
        "paciente": {"nome": "Carlos Eduardo Mendes", "idade": 52},
        "medico_solicitante": {"nome": "Dra. Renata Silveira", "crm": "CRM-SP 145892"},
        "exame_realizado": "Tomografia de Torax com contraste",
        "diagnostico_principal": "Tromboembolismo Pulmonar Agudo (TEP)",
        "cid_sugerido": "I26.9",
        "medicamentos": [],
    }
)

JSON_CID_INVALIDO = JSON_OK.replace("I26.9", "XYZ")


def test_extracao_feliz_texto():
    cliente = ClienteFalso([JSON_OK])
    dados = extrair_dados("LAUDO ... Paciente: Carlos Eduardo Mendes", client=cliente)
    assert dados.paciente.nome == "Carlos Eduardo Mendes"
    assert dados.medico_solicitante.crm == "145892-SP"  # padronizado pelo schema da Etapa 1
    assert dados.cid_sugerido == "I26.9"
    assert len(cliente.models.chamadas) == 1


def test_json_dentro_de_cercas_markdown_e_aceito():
    cliente = ClienteFalso([f"```json\n{JSON_OK}\n```"])
    assert extrair_dados("texto", client=cliente).paciente.idade == 52


def test_retentativa_corrige_resposta_invalida():
    cliente = ClienteFalso([JSON_CID_INVALIDO, JSON_OK])
    dados = extrair_dados("texto", client=cliente)
    assert dados.cid_sugerido == "I26.9"
    assert len(cliente.models.chamadas) == 2
    # na 2ª chamada, o erro foi explicado ao modelo
    assert "rejeitada" in str(cliente.models.chamadas[1][-1])


def test_falha_apos_duas_tentativas_levanta_erro():
    cliente = ClienteFalso([JSON_CID_INVALIDO, "isto nem é JSON"])
    with pytest.raises(ErroExtracao):
        extrair_dados("texto", client=cliente)


def test_falha_de_rede_vira_erro_extracao():
    cliente = ClienteFalso([RuntimeError("sem internet")])
    with pytest.raises(ErroExtracao, match="Falha ao chamar o Gemini"):
        extrair_dados("texto", client=cliente)


def test_documento_vazio_e_rejeitado_sem_chamar_gemini():
    cliente = ClienteFalso([])
    with pytest.raises(ErroExtracao):
        extrair_dados("   ", client=cliente)
    assert cliente.models.chamadas == []


def test_bytes_sem_mime_type_e_rejeitado():
    with pytest.raises(ErroExtracao):
        extrair_dados(b"%PDF-1.4 ...", client=ClienteFalso([]))


def test_pdf_em_bytes_e_enviado_ao_gemini():
    pytest.importorskip("google.genai")  # só roda se o SDK estiver instalado
    cliente = ClienteFalso([JSON_OK])
    dados = extrair_dados(b"%PDF-1.4 fake", mime_type="application/pdf", client=cliente)
    assert dados.paciente.nome == "Carlos Eduardo Mendes"
    assert len(cliente.models.chamadas[0]) == 3  # prompt + arquivo + instrução


def test_limpar_json():
    assert limpar_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert limpar_json('{"a": 1}') == '{"a": 1}'


def test_mime_por_extensao():
    assert mime_por_extensao("laudo.PDF") == "application/pdf"
    assert mime_por_extensao("foto.jpeg") == "image/jpeg"
    with pytest.raises(ErroExtracao):
        mime_por_extensao("planilha.xlsx")


def test_saida_do_extrator_bate_com_o_json_esperado_do_enunciado():
    """Extrai o laudo de TEP e compara com o bloco 'dados_extraidos' esperado."""
    esperado = {
        "paciente": {"nome": "Carlos Eduardo Mendes", "idade": 52},
        "medico_solicitante": {"nome": "Dra. Renata Silveira", "crm": "145892-SP"},
        "exame_realizado": "Tomografia de Torax com contraste",
        "diagnostico_principal": "Tromboembolismo Pulmonar Agudo (TEP)",
        "cid_sugerido": "I26.9",
    }
    cliente = ClienteFalso([json.dumps({**esperado, "medicamentos": []})])
    dados = extrair_dados("laudo de TEP", client=cliente)
    assert json.loads(dados.model_dump_json()) == esperado  # sem 'medicamentos'


def test_prompt_contem_as_regras_de_diagnostico_e_cid():
    from extractor import PROMPT_EXTRACAO

    assert "sigla" in PROMPT_EXTRACAO and "(TEP)" in PROMPT_EXTRACAO
    assert "ÚNICO campo que você pode inferir" in PROMPT_EXTRACAO
