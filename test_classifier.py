"""Testes da Etapa 3 (classifier.py) com Gemini falso."""
import pytest

from classifier import PROMPT_CLASSIFICACAO, classificar_documento
from extractor import ErroExtracao
from fakes import ClienteRoteado, URGENCIA


def test_classificacao_feliz():
    c = classificar_documento("laudo de TEP", client=ClienteRoteado(URGENCIA[0], {}))
    assert c.tipo_documento.value == "Laudo de Exame de Imagem"
    assert c.nivel_prioridade.value == "Urgente" and c.score_confianca_classificacao == 0.99


def test_tipo_fora_da_lista_e_rejeitado_apos_retentativa():
    ruim = {**URGENCIA[0], "tipo_documento": "Nota Fiscal"}
    cliente = ClienteRoteado(ruim, {})
    with pytest.raises(ErroExtracao):
        classificar_documento("texto", client=cliente)
    assert cliente.models.chamadas == 2  # 1 tentativa + 1 retentativa


def test_score_fora_de_0_a_1_e_rejeitado():
    with pytest.raises(ErroExtracao):
        classificar_documento("texto", client=ClienteRoteado({**URGENCIA[0], "score_confianca_classificacao": 7}, {}))


def test_documento_vazio_nao_chama_o_gemini():
    cliente = ClienteRoteado(URGENCIA[0], {})
    with pytest.raises(ErroExtracao):
        classificar_documento("  ", client=cliente)
    assert cliente.models.chamadas == 0


def test_prompt_lista_todos_os_tipos_do_enum():
    from schemas import TipoDocumento
    assert all(t.value in PROMPT_CLASSIFICACAO for t in TipoDocumento)
