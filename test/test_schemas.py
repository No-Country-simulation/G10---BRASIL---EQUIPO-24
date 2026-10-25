"""
Testes da Etapa 1. Rode com:  pytest -v

Um teste é um "experimento automático": você diz o que ESPERA que aconteça
e o computador confirma. Assim, se mudarmos algo depois, descobrimos na hora
se quebramos alguma coisa.
"""

import pytest
from pydantic import ValidationError

from schemas import (
    DadosExtraidos,
    DestinoRoteamento,
    MedicoSolicitante,
    RequisicaoTriagem,
    RespostaTriagem,
)


def test_requisicao_do_enunciado_e_valida():
    req = RequisicaoTriagem(
        documento_id="DOC-CLIN-2026-8942",
        tipo_arquivo="PDF",
        documento_texto="HOSPITAL SANTA LUCIA - LAUDO DE EXAME RADIOLOGICO...",
        canal_origem="Pronto_Socorro",
    )
    assert req.tipo_arquivo.value == "PDF"


def test_tipo_arquivo_invalido_e_rejeitado():
    with pytest.raises(ValidationError):
        RequisicaoTriagem(documento_id="X", tipo_arquivo="DOCX")


@pytest.mark.parametrize(
    "entrada",
    ["CRM-SP 145892", "CRM/SP 145892", "145892-SP", "crm sp 145892"],
)
def test_crm_e_padronizado(entrada):
    assert MedicoSolicitante(crm=entrada).crm == "145892-SP"


def test_cid_invalido_e_rejeitado():
    with pytest.raises(ValidationError):
        DadosExtraidos(cid_sugerido="XYZ")


def test_cid_valido_e_aceito():
    assert DadosExtraidos(cid_sugerido="i26.9").cid_sugerido == "I26.9"


def test_idade_negativa_e_rejeitada():
    with pytest.raises(ValidationError):
        DadosExtraidos(paciente={"nome": "Fulano", "idade": -5})


def test_resposta_completa_do_enunciado():
    """Valida o JSON de saída de exemplo do enunciado do hackathon."""
    resposta = RespostaTriagem.model_validate(
        {
            "status": "processado",
            "documento_id": "DOC-CLIN-2026-8942",
            "classificacao": {
                "tipo_documento": "Laudo de Exame de Imagem",
                "especialidade": "Radiologia / Pneumologia",
                "nivel_prioridade": "Urgente",
                "score_confianca_classificacao": 0.99,
            },
            "dados_extraidos": {
                "paciente": {"nome": "Carlos Eduardo Mendes", "idade": 52},
                "medico_solicitante": {"nome": "Dra. Renata Silveira", "crm": "145892-SP"},
                "exame_realizado": "Tomografia de Torax com contraste",
                "diagnostico_principal": "Tromboembolismo Pulmonar Agudo (TEP)",
                "cid_sugerido": "I26.9",
            },
            "decisao_roteamento": {
                "destino_principal": "Fila_Emergencia_Medica",
                "requer_auditoria_humana": False,
                "justificativa_roteamento": "Achado critico de alta gravidade (TEP agudo).",
                "notificacao_gerada": {
                    "canal": "Alerta_Plantao_Medico",
                    "mensagem": "ALERTA URGENTE: Laudo critico de TEP Agudo.",
                },
            },
            "armazenamento_oci": {
                "bucket": "mediflow-documentos-clinicos",
                "caminho_objeto": "processados/urgentes/DOC-CLIN-2026-8942.json",
                "status_backup": "sucesso",
            },
        }
    )
    assert resposta.decisao_roteamento.destino_principal == DestinoRoteamento.FILA_EMERGENCIA


def test_laudo_sem_medicamentos_omite_o_campo_na_saida():
    """O JSON esperado do enunciado (laudo de TEP) não traz 'medicamentos'."""
    dados = DadosExtraidos(cid_sugerido="I26.9")
    assert "medicamentos" not in dados.model_dump()
    assert "medicamentos" not in dados.model_dump_json()


def test_receita_com_medicamentos_mantem_o_campo_na_saida():
    dados = DadosExtraidos(medicamentos=[{"nome": "Amoxicilina", "dosagem": "500 mg"}])
    assert dados.model_dump()["medicamentos"][0]["nome"] == "Amoxicilina"
