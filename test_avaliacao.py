"""
test_avaliacao.py: testes da Etapa 4 (avaliacao.py) SEM chave de API e SEM internet.

Como a avaliação é só regra em Python (não chama o Gemini), basta montar documentos
de exemplo e conferir a resposta.

COMO RODAR:
    python test_avaliacao.py
(ou, se tiver o pytest instalado:  pytest test_avaliacao.py)
"""

import avaliacao
from avaliacao import avaliar_extracao
from schemas import DadosExtraidos, Medicamento, MedicoSolicitante, Paciente, TipoDocumento


# ---------------------------------------------------------------------------
# Documentos de exemplo
# ---------------------------------------------------------------------------
def receita_completa() -> DadosExtraidos:
    return DadosExtraidos(
        paciente=Paciente(nome="Maria Aparecida Souza", idade=34),
        medico_solicitante=MedicoSolicitante(nome="Dr. Joao Pedro Alves", crm="123456-SP"),
        diagnostico_principal="Faringite Aguda",
        cid_sugerido="J02.9",
        medicamentos=[
            Medicamento(nome="Amoxicilina", dosagem="500 mg", posologia="1 capsula a cada 8 horas"),
            Medicamento(nome="Dipirona", dosagem="500 mg", posologia="1 comprimido a cada 6 horas"),
        ],
    )


def laudo_tep_completo() -> DadosExtraidos:
    return DadosExtraidos(
        paciente=Paciente(nome="Carlos Eduardo Mendes", idade=52),
        medico_solicitante=MedicoSolicitante(nome="Dra. Renata Silveira", crm="145892-SP"),
        exame_realizado="Tomografia de Torax com contraste",
        diagnostico_principal="Tromboembolismo Pulmonar Agudo (TEP)",
        cid_sugerido="I26.9",
    )


def perto(a: float, b: float) -> bool:
    return abs(a - b) < 0.011


# ---------------------------------------------------------------------------
# Os testes
# ---------------------------------------------------------------------------
def test_receita_completa_nao_precisa_de_revisao():
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, receita_completa(), 0.98)
    assert r.campos_ausentes == []
    assert r.conflitos == []
    assert r.score_extracao == 1.0
    assert r.score_final == 0.98  # o menor entre 0.98 e 1.0
    assert r.requer_revisao_humana is False
    assert r.motivos_revisao == []


def test_laudo_tep_completo_nao_precisa_de_revisao():
    r = avaliar_extracao(TipoDocumento.LAUDO_IMAGEM, laudo_tep_completo(), 0.99)
    assert r.requer_revisao_humana is False
    assert r.score_final == 0.99


def test_receita_sem_crm_vai_para_revisao():
    dados = receita_completa()
    dados.medico_solicitante = MedicoSolicitante(nome="Dr. Joao Pedro Alves", crm=None)
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, dados, 0.98)
    assert r.campos_ausentes == ["medico.crm"]
    assert r.requer_revisao_humana is True
    assert perto(r.score_extracao, 1 - avaliacao.PENALIDADE_OBRIGATORIO_AUSENTE)
    assert any("CRM" in motivo for motivo in r.motivos_revisao)


def test_receita_sem_medicamentos_e_conflito():
    dados = receita_completa()
    dados.medicamentos = []
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, dados, 0.98)
    assert "Receituário sem nenhum medicamento." in r.conflitos
    assert r.requer_revisao_humana is True


def test_medicamento_ilegivel_vai_para_revisao():
    dados = receita_completa()
    dados.medicamentos = [Medicamento(nome="Amoxi???", legivel=False)]
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, dados, 0.98)
    assert r.requer_revisao_humana is True
    assert any("ilegível" in motivo for motivo in r.motivos_revisao)


def test_crm_fora_do_padrao_e_conflito():
    dados = laudo_tep_completo()
    dados.medico_solicitante = MedicoSolicitante(nome="Dra. Renata Silveira", crm="ABC")
    r = avaliar_extracao(TipoDocumento.LAUDO_IMAGEM, dados, 0.99)
    assert len(r.conflitos) == 1
    assert "CRM fora do padrão" in r.conflitos[0]
    assert r.requer_revisao_humana is True


def test_laudo_sem_exame_e_sem_diagnostico_e_conflito():
    dados = DadosExtraidos(paciente=Paciente(nome="Fulano de Tal", idade=40))
    r = avaliar_extracao(TipoDocumento.LAUDO_IMAGEM, dados, 0.90)
    assert "exame_realizado" in r.campos_ausentes
    assert any("sem exame e sem diagnóstico" in c for c in r.conflitos)
    assert r.requer_revisao_humana is True


def test_tipo_desconhecido_vai_para_revisao():
    r = avaliar_extracao(TipoDocumento.DESCONHECIDO, laudo_tep_completo(), 0.99)
    assert r.requer_revisao_humana is True
    assert "Tipo de documento não identificado." in r.motivos_revisao


def test_score_de_classificacao_baixo_vai_para_revisao():
    r = avaliar_extracao(TipoDocumento.LAUDO_IMAGEM, laudo_tep_completo(), 0.50)
    assert r.score_final == 0.5  # o menor entre 0.5 e 1.0
    assert r.requer_revisao_humana is True
    assert any("Confiança baixa" in motivo for motivo in r.motivos_revisao)


def test_campo_desejavel_ausente_so_avisa():
    dados = laudo_tep_completo()
    dados.paciente = Paciente(nome="Carlos Eduardo Mendes", idade=None)
    r = avaliar_extracao(TipoDocumento.LAUDO_IMAGEM, dados, 0.99)
    assert r.requer_revisao_humana is False  # só baixa um pouco o score
    assert any("idade" in alerta for alerta in r.alertas)
    assert r.score_extracao < 1.0


def test_medicamento_de_alto_risco_e_detectado_quando_a_lista_existe():
    original = avaliacao.MEDICAMENTOS_ALTO_RISCO
    try:
        avaliacao.MEDICAMENTOS_ALTO_RISCO = {"amoxicilina"}  # só para o teste
        r = avaliar_extracao(TipoDocumento.RECEITUARIO, receita_completa(), 0.98)
        assert r.medicamentos_alto_risco == ["Amoxicilina"]
    finally:
        avaliacao.MEDICAMENTOS_ALTO_RISCO = original


def test_lista_de_alto_risco_vazia_nao_marca_nada():
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, receita_completa(), 0.98)
    assert r.medicamentos_alto_risco == []


def test_score_nunca_fica_abaixo_de_zero():
    dados = DadosExtraidos(medicamentos=[Medicamento(nome="X", legivel=False)] * 10)
    r = avaliar_extracao(TipoDocumento.RECEITUARIO, dados, 1.0)
    assert 0.0 <= r.score_extracao <= 1.0
    assert 0.0 <= r.score_final <= 1.0


if __name__ == "__main__":
    testes = [valor for nome, valor in sorted(globals().items()) if nome.startswith("test_")]
    for teste in testes:
        teste()
        print(f"OK  {teste.__name__}")
    print(f"\n{len(testes)} testes passaram.")
