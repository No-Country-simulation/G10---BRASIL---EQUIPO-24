"""
avaliacao.py: ETAPA 4 do MediFlow: avaliar consistência, conflitos e score de confiabilidade

O QUE ESTE ARQUIVO FAZ?
Recebe o que já foi descoberto sobre o documento (o tipo, os dados extraídos e o score
da classificação) e responde: "dá para confiar nesta extração ou um humano precisa olhar?"

Ele devolve um `ResultadoAvaliacao` com:
    - score_extracao / score_final  (0 a 1)
    - campos obrigatórios que faltam
    - conflitos encontrados (coisas que não fazem sentido)
    - se precisa de revisão humana, e os motivos (em português, prontos para virar
      a justificativa do roteamento na Etapa 5)

NÃO CHAMA O GEMINI.
São regras em Python puro: previsíveis, rápidas e testáveis sem chave de API.
Em saúde, é bom que a decisão final siga regras claras, e não a "opinião" do modelo.

COMO AJUSTAR SEM MEXER NA LÓGICA:
Todos os números e listas que o time pode querer mudar estão na seção AJUSTES, no topo.
ATENÇÃO: os valores atuais são PROVISÓRIOS (propostas iniciais) e precisam ser revisados
com a equipe e ajustados durante os testes.

COMO TESTAR (sem chave de API):
    python test_avaliacao.py
"""

import re
from dataclasses import dataclass, field

from schemas import DadosExtraidos, TipoDocumento

# ===========================================================================
# AJUSTES (valores provisórios: revisar com a equipe)
# ===========================================================================

# Se o score final ficar abaixo deste valor, o documento vai para revisão humana.
LIMITE_REVISAO_HUMANA = 0.70

# Quanto o score da extração cai para cada problema encontrado.
PENALIDADE_OBRIGATORIO_AUSENTE = 0.25
PENALIDADE_DESEJAVEL_AUSENTE = 0.05
PENALIDADE_MEDICAMENTO_ILEGIVEL = 0.25
PENALIDADE_CONFLITO = 0.20

# Medicamentos de alto risco. EM DEFINIÇÃO: o PDF do desafio cita "prescrições de alto
# risco" como urgência, mas a lista deve ser definida por alguém da área da saúde.
# Enquanto estiver vazia, nada é marcado. Exemplo de uso: {"heparina", "insulina"}
MEDICAMENTOS_ALTO_RISCO: set = set()

# CRM no formato NUMERO-UF (ex.: 145892-SP), o mesmo padrão usado no schemas.py
PADRAO_CRM = re.compile(r"[0-9]{3,7}-[A-Z]{2}")


# ===========================================================================
# QUAIS CAMPOS CADA TIPO DE DOCUMENTO EXIGE
# Cada campo tem um nome legível (para as mensagens) e um teste "está preenchido?".
# ===========================================================================
def _preenchido(valor) -> bool:
    """True se o valor existe e não é só espaços."""
    return valor is not None and str(valor).strip() != ""


CAMPOS = {
    "paciente.nome": ("nome do paciente", lambda d: _preenchido(d.paciente.nome)),
    "paciente.idade": ("idade do paciente", lambda d: d.paciente.idade is not None),
    "medico.nome": ("nome do médico", lambda d: _preenchido(d.medico_solicitante.nome)),
    "medico.crm": ("CRM do médico", lambda d: _preenchido(d.medico_solicitante.crm)),
    "exame_realizado": ("exame/procedimento", lambda d: _preenchido(d.exame_realizado)),
    "diagnostico_principal": (
        "diagnóstico",
        lambda d: _preenchido(d.diagnostico_principal),
    ),
}

# OBRIGATÓRIOS: se faltar algum, o documento vai para revisão humana.
# (Para receituário, a presença de medicamentos é conferida à parte, nos conflitos.)
OBRIGATORIOS = {
    TipoDocumento.RECEITUARIO: ["paciente.nome", "medico.nome", "medico.crm"],
    TipoDocumento.LAUDO_LABORATORIAL: ["paciente.nome", "exame_realizado"],
    TipoDocumento.LAUDO_IMAGEM: ["paciente.nome", "exame_realizado"],
    TipoDocumento.GUIA_PROCEDIMENTO: [
        "paciente.nome",
        "medico.nome",
        "medico.crm",
        "exame_realizado",
        "diagnostico_principal",
    ],
    TipoDocumento.RELATORIO_ALTA: ["paciente.nome", "diagnostico_principal"],
    TipoDocumento.ATESTADO: ["paciente.nome", "medico.nome", "medico.crm"],
}

# DESEJÁVEIS: se faltarem, só baixam um pouco o score (não obrigam revisão).
DESEJAVEIS = {
    TipoDocumento.RECEITUARIO: ["paciente.idade"],
    TipoDocumento.LAUDO_LABORATORIAL: ["paciente.idade", "medico.nome"],
    TipoDocumento.LAUDO_IMAGEM: ["paciente.idade", "medico.nome"],
    TipoDocumento.GUIA_PROCEDIMENTO: ["paciente.idade"],
    TipoDocumento.RELATORIO_ALTA: ["paciente.idade"],
    TipoDocumento.ATESTADO: ["paciente.idade"],
}

# Tipos em que é estranho não ter NEM exame NEM diagnóstico (conteúdo clínico vazio).
TIPOS_COM_CONTEUDO_CLINICO = {
    TipoDocumento.LAUDO_LABORATORIAL,
    TipoDocumento.LAUDO_IMAGEM,
    TipoDocumento.GUIA_PROCEDIMENTO,
    TipoDocumento.RELATORIO_ALTA,
}


# ===========================================================================
# O "RESULTADO" DESTA ETAPA
# ===========================================================================
@dataclass
class ResultadoAvaliacao:
    score_extracao: float  # confiabilidade só dos dados extraídos (0 a 1)
    score_final: float  # o MENOR entre o score da classificação e o da extração
    requer_revisao_humana: bool
    motivos_revisao: list = field(default_factory=list)  # por que precisa de revisão
    campos_ausentes: list = field(default_factory=list)  # ex.: ["medico.crm"]
    conflitos: list = field(default_factory=list)  # coisas que não fazem sentido
    alertas: list = field(default_factory=list)  # avisos que NÃO obrigam revisão
    medicamentos_alto_risco: list = field(default_factory=list)


# ===========================================================================
# FUNÇÃO PRINCIPAL
# ===========================================================================
def avaliar_extracao(
    tipo: TipoDocumento,
    dados: DadosExtraidos,
    score_classificacao: float,
) -> ResultadoAvaliacao:
    """Avalia a consistência da extração e calcula o score de confiabilidade.

    Parâmetros:
        tipo:                tipo do documento (vem da classificação).
        dados:               dados extraídos pelo Gemini (já validados pelo Pydantic).
        score_classificacao: score de confiança da classificação (0 a 1).
    """
    penalidade = 0.0
    campos_ausentes: list = []
    conflitos: list = []
    alertas: list = []
    motivos: list = []

    # --- 0) tipo desconhecido: não dá para saber o que conferir ---
    if tipo == TipoDocumento.DESCONHECIDO:
        motivos.append("Tipo de documento não identificado.")

    # --- 1) campos obrigatórios ---
    for campo in OBRIGATORIOS.get(tipo, []):
        nome_legivel, esta_preenchido = CAMPOS[campo]
        if not esta_preenchido(dados):
            campos_ausentes.append(campo)
            motivos.append(f"Campo obrigatório ausente: {nome_legivel}.")
            penalidade += PENALIDADE_OBRIGATORIO_AUSENTE

    # --- 2) campos desejáveis (só avisam e baixam um pouco o score) ---
    for campo in DESEJAVEIS.get(tipo, []):
        nome_legivel, esta_preenchido = CAMPOS[campo]
        if not esta_preenchido(dados):
            alertas.append(f"Campo desejável ausente: {nome_legivel}.")
            penalidade += PENALIDADE_DESEJAVEL_AUSENTE

    # --- 3) conflitos: coisas que não fazem sentido ---
    if tipo == TipoDocumento.RECEITUARIO and not dados.medicamentos:
        conflitos.append("Receituário sem nenhum medicamento.")

    crm = dados.medico_solicitante.crm
    if _preenchido(crm) and not PADRAO_CRM.fullmatch(str(crm).strip()):
        conflitos.append(f"CRM fora do padrão NUMERO-UF: {crm!r}.")

    if (
        tipo in TIPOS_COM_CONTEUDO_CLINICO
        and not _preenchido(dados.exame_realizado)
        and not _preenchido(dados.diagnostico_principal)
    ):
        conflitos.append("Documento sem exame e sem diagnóstico (conteúdo clínico não identificado).")

    for conflito in conflitos:
        motivos.append(f"Conflito: {conflito}")
        penalidade += PENALIDADE_CONFLITO

    # --- 4) medicamentos ilegíveis ou incertos ---
    for medicamento in dados.medicamentos:
        if not medicamento.legivel:
            motivos.append(f"Medicamento ilegível ou incerto: {medicamento.nome}.")
            penalidade += PENALIDADE_MEDICAMENTO_ILEGIVEL

    # --- 5) medicamentos de alto risco (lista EM DEFINIÇÃO; vazia = nada marcado) ---
    alto_risco = [
        m.nome
        for m in dados.medicamentos
        if any(risco.lower() in m.nome.lower() for risco in MEDICAMENTOS_ALTO_RISCO)
    ]
    for nome in alto_risco:
        alertas.append(f"Medicamento de alto risco: {nome}.")

    # --- 6) scores ---
    score_extracao = round(max(0.0, min(1.0, 1.0 - penalidade)), 2)
    score_classificacao = max(0.0, min(1.0, score_classificacao))
    score_final = round(min(score_classificacao, score_extracao), 2)

    # --- 7) precisa de revisão humana? ---
    # Qualquer motivo concreto (campo obrigatório faltando, conflito, medicamento
    # ilegível, tipo desconhecido) OU score final abaixo do limite.
    if score_final < LIMITE_REVISAO_HUMANA and not motivos:
        motivos.append(
            f"Confiança baixa: score {score_final} abaixo do limite {LIMITE_REVISAO_HUMANA}."
        )

    return ResultadoAvaliacao(
        score_extracao=score_extracao,
        score_final=score_final,
        requer_revisao_humana=bool(motivos) or score_final < LIMITE_REVISAO_HUMANA,
        motivos_revisao=motivos,
        campos_ausentes=campos_ausentes,
        conflitos=conflitos,
        alertas=alertas,
        medicamentos_alto_risco=alto_risco,
    )
