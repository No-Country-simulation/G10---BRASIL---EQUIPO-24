"""Testes da Etapa 4 (rules.py): regras puras, sem LLM e sem internet."""
import pytest

from config import CONFIG_PADRAO, carregar_config, salvar_config, validar_config
from rules import (
    avaliar,
    decisao_padrao,
    decisao_revisao,
    decisao_urgente,
    detectar_termos_urgencia,
    escolher_rota,
    normalizar,
    pasta_de_armazenamento,
)
from schemas import Classificacao, DadosExtraidos, DestinoRoteamento


def classif(tipo="Laudo de Exame de Imagem", prio="Rotina", score=0.95):
    return Classificacao(tipo_documento=tipo, nivel_prioridade=prio, score_confianca_classificacao=score)


def dados(**kw):
    base = dict(
        paciente={"nome": "Fulano de Tal", "idade": 40},
        medico_solicitante={"nome": "Dr. X", "crm": "123456-SP"},
        exame_realizado="Exame", diagnostico_principal="Achado qualquer",
    )
    base.update(kw)
    return DadosExtraidos(**base)


# ---------------- urgência ----------------
def test_normalizar_remove_acentos_e_caixa():
    assert normalizar("Embolia PULMONAR Aguda é grave") == "embolia pulmonar aguda e grave"


@pytest.mark.parametrize("texto", ["Tromboembolismo Pulmonar Agudo (TEP)", "Infarto agudo do miocárdio", "AVC isquêmico"])
def test_termos_de_urgencia_sao_detectados(texto):
    assert detectar_termos_urgencia(texto, CONFIG_PADRAO["palavras_urgencia"])


@pytest.mark.parametrize("texto", ["Sem evidência de TEP", "Afasta infarto agudo", "Ausência de hemorragia", "Descartado AVC"])
def test_negacao_nao_dispara_urgencia(texto):
    assert detectar_termos_urgencia(texto, CONFIG_PADRAO["palavras_urgencia"]) == []


def test_termo_curto_nao_casa_dentro_de_outra_palavra():
    # "tep" não pode disparar em "septico"/"intepretacao"; "iam" não pode disparar em "diametro"
    assert detectar_termos_urgencia("Lesao de diametro aumentado, interpretacao normal", CONFIG_PADRAO["palavras_urgencia"]) == []


def test_urgencia_por_prioridade_do_llm_mesmo_sem_palavra_chave():
    av = avaliar(classif(prio="Urgente"), dados(diagnostico_principal="Quadro inespecifico"))
    assert av.urgente and escolher_rota(av) == "urgente"


def test_urgencia_por_palavra_mesmo_se_llm_disse_rotina():
    """Rede de segurança: LLM errou a prioridade, a regra pega."""
    av = avaliar(classif(prio="Rotina"), dados(diagnostico_principal="Tromboembolismo Pulmonar Agudo"))
    assert av.urgente


# ---------------- confiança / campos faltantes ----------------
def test_caso_padrao_alta_confianca():
    av = avaliar(classif(), dados())
    assert escolher_rota(av) == "padrao" and av.score_final == 0.95 and not av.motivos_revisao


def test_crm_ausente_forca_revisao_e_reduz_score():
    av = avaliar(classif(), dados(medico_solicitante={"nome": "Dr. X", "crm": None}))
    assert av.campos_faltantes == ["medico_solicitante.crm"]
    assert av.score_final == 0.75 and escolher_rota(av) == "revisao"


def test_paciente_ausente_forca_revisao():
    av = avaliar(classif(), dados(paciente={"nome": "  ", "idade": None}))
    assert "paciente.nome" in av.campos_faltantes and escolher_rota(av) == "revisao"


def test_score_baixo_do_llm_forca_revisao():
    av = avaliar(classif(score=0.5), dados())
    assert av.confianca_baixa and escolher_rota(av) == "revisao"


def test_medicamento_ilegivel_penaliza():
    d = dados(medicamentos=[{"nome": "Losartana", "legivel": False}])
    av = avaliar(classif("Receituário Médico", score=0.90), d)
    assert av.score_final == 0.75 and escolher_rota(av) == "revisao"


def test_score_nunca_fica_negativo():
    d = dados(paciente={"nome": None}, medico_solicitante={"crm": None},
              medicamentos=[{"nome": "X", "legivel": False}] * 5)
    assert avaliar(classif(score=0.3), d).score_final == 0.0


def test_receituario_sem_medicamentos_e_inconsistente():
    av = avaliar(classif("Receituário Médico"), dados())
    assert av.inconsistencias and escolher_rota(av) == "revisao"


def test_tipo_desconhecido_vai_para_revisao():
    assert escolher_rota(avaliar(classif("Desconhecido"), dados())) == "revisao"


def test_erros_de_processamento_vao_para_revisao():
    av = avaliar(classif(), dados(), erros=["Gemini fora do ar"])
    assert escolher_rota(av) == "revisao" and "Gemini fora do ar" in av.motivos_revisao[0]


# ---------------- decisões ----------------
def test_decisao_urgente_gera_alerta_e_nao_pede_auditoria_se_dados_ok():
    c, d = classif(prio="Urgente", score=0.99), dados(diagnostico_principal="Tromboembolismo Pulmonar Agudo (TEP)")
    dec = decisao_urgente(avaliar(c, d), c, d, "Pronto_Socorro")
    assert dec.destino_principal == DestinoRoteamento.FILA_EMERGENCIA
    assert not dec.requer_auditoria_humana
    assert dec.notificacao_gerada.canal == "Alerta_Plantao_Medico"
    assert "Fulano de Tal" in dec.notificacao_gerada.mensagem and "Pronto-Socorro" in dec.notificacao_gerada.mensagem


def test_urgente_com_dados_duvidosos_alerta_E_pede_auditoria():
    c = classif(prio="Urgente", score=0.99)
    d = dados(diagnostico_principal="Infarto", medico_solicitante={"crm": None})
    av = avaliar(c, d)
    dec = decisao_urgente(av, c, d, "Pronto_Socorro")
    assert dec.destino_principal == DestinoRoteamento.FILA_EMERGENCIA  # urgência nunca espera
    assert dec.requer_auditoria_humana and dec.notificacao_gerada
    assert pasta_de_armazenamento(dec, av) == "auditoria_humana"


def test_decisao_revisao_sempre_pede_auditoria():
    dec = decisao_revisao(avaliar(classif(score=0.4), dados()))
    assert dec.destino_principal == DestinoRoteamento.REVISAO_HUMANA and dec.requer_auditoria_humana


@pytest.mark.parametrize("tipo,destino", [
    ("Receituário Médico", "Farmacia_Hospitalar"),
    ("Guia de Solicitação de Procedimento", "Auditoria_Autorizacoes"),
    ("Atestado Médico", "Prontuario_Eletronico"),
])
def test_destino_padrao_por_tipo(tipo, destino):
    d = dados(medicamentos=[{"nome": "X", "legivel": True}])
    assert decisao_padrao(avaliar(classif(tipo), d), classif(tipo)).destino_principal.value == destino


def test_pastas_de_armazenamento():
    c, d = classif("Receituário Médico"), dados(medicamentos=[{"nome": "X"}])
    av = avaliar(c, d)
    assert pasta_de_armazenamento(decisao_padrao(av, c), av) == "processados/farmacia_hospitalar"


# ---------------- configuração customizável ----------------
def test_config_customizada_muda_o_comportamento(tmp_path):
    cfg = carregar_config(tmp_path / "nao_existe.json")
    cfg["limiar_confianca"] = 0.99
    assert escolher_rota(avaliar(classif(score=0.95), dados(), cfg)) == "revisao"  # antes seria padrão

    cfg = carregar_config(tmp_path / "nao_existe.json")
    cfg["palavras_urgencia"].append("faringite")
    assert avaliar(classif(), dados(diagnostico_principal="Faringite aguda"), cfg).urgente


def test_config_roundtrip_e_validacao(tmp_path):
    arq = tmp_path / "cfg.json"
    cfg = carregar_config(arq)
    cfg["limiar_confianca"] = 0.9
    salvar_config(cfg, arq)
    assert carregar_config(arq)["limiar_confianca"] == 0.9
    cfg["limiar_confianca"] = 1.5
    with pytest.raises(ValueError):
        salvar_config(cfg, arq)
    cfg["limiar_confianca"] = 0.8
    cfg["destino_por_tipo"]["Atestado Médico"] = "Destino_Inexistente"
    assert validar_config(cfg)


def test_config_quebrada_cai_no_padrao(tmp_path):
    arq = tmp_path / "cfg.json"
    arq.write_text("{ isto nao e json")
    assert carregar_config(arq) == CONFIG_PADRAO
