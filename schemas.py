"""
schemas.py: ETAPA 1 do MediFlow

O QUE É ISSO?
Pense num formulário em papel com campos obrigatórios, opcionais e regras
("idade não pode ser negativa", "confiança vai de 0 a 1"). O Pydantic faz isso
em Python: se o dado não obedecer ao formulário, ele REJEITA com um erro claro.

POR QUE IMPORTA AQUI?
Os LLMs às vezes "inventam" formatos. Com o schema, a resposta do Gemini só
segue adiante se estiver no formato certo. Em saúde, isso é segurança.
"""

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


# ---------------------------------------------------------------------------
# 1) ENUMS: listas fechadas de opções
# Em vez de deixar o LLM escrever "urgente", "URGENTE", "urgência"..., limitamos
# a valores permitidos. Isso evita erros de digitação nas regras de decisão.
# ---------------------------------------------------------------------------
class TipoArquivo(str, Enum):
    PDF = "PDF"
    IMAGEM = "IMAGEM"
    TEXTO = "TEXTO"
    JSON = "JSON"


class TipoDocumento(str, Enum):
    RECEITUARIO = "Receituário Médico"
    LAUDO_LABORATORIAL = "Laudo de Exame Laboratorial"
    LAUDO_IMAGEM = "Laudo de Exame de Imagem"
    GUIA_PROCEDIMENTO = "Guia de Solicitação de Procedimento"
    RELATORIO_ALTA = "Relatório de Alta/Internação"
    ATESTADO = "Atestado Médico"
    DESCONHECIDO = "Desconhecido"  # o agente pode não reconhecer o documento


class NivelPrioridade(str, Enum):
    ROTINA = "Rotina"
    PRIORITARIO = "Prioritário"
    URGENTE = "Urgente"


class DestinoRoteamento(str, Enum):
    FILA_EMERGENCIA = "Fila_Emergencia_Medica"
    AUDITORIA_AUTORIZACOES = "Auditoria_Autorizacoes"
    FARMACIA = "Farmacia_Hospitalar"
    PRONTUARIO = "Prontuario_Eletronico"
    REVISAO_HUMANA = "Fila_Revisao_Humana"


# ---------------------------------------------------------------------------
# 2) ENTRADA: o que a API recebe (igual ao exemplo do enunciado)
# ---------------------------------------------------------------------------
class RequisicaoTriagem(BaseModel):
    # O id vira parte do nome do arquivo/objeto na nuvem. Por segurança, só aceitamos
    # letras, números, ponto, hífen e underline (impede "../" e outros truques de caminho).
    documento_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9._-]{1,100}$",
        description="Ex.: DOC-CLIN-2026-8942",
    )
    tipo_arquivo: TipoArquivo
    # Opcional porque, para PDF/imagem, o conteúdo vem como arquivo, não texto.
    documento_texto: Optional[str] = None
    canal_origem: Optional[str] = Field(None, description="Ex.: Pronto_Socorro")


# ---------------------------------------------------------------------------
# 3) DADOS EXTRAÍDOS: o que o Gemini deve devolver
# Campos "Optional" = podem faltar. Um campo faltando NÃO é erro de schema;
# é um SINAL que as regras (Etapa 4) usarão para mandar para revisão humana.
# ---------------------------------------------------------------------------
class Paciente(BaseModel):
    nome: Optional[str] = None
    idade: Optional[int] = Field(None, ge=0, le=130)  # ge/le = mínimo/máximo


class MedicoSolicitante(BaseModel):
    nome: Optional[str] = None
    crm: Optional[str] = None  # formato final padronizado: "145892-SP"

    @field_validator("crm")
    @classmethod
    def padronizar_crm(cls, valor: Optional[str]) -> Optional[str]:
        """Aceita 'CRM-SP 145892', 'CRM/SP 145892' ou '145892-SP' e padroniza.

        Se não conseguir entender, mantém o valor original: a regra de
        decisão decidirá depois. Nunca descartamos informação em silêncio.
        """
        if valor is None:
            return None
        texto = valor.upper().strip()
        # Formato "CRM-SP 145892" / "CRM/SP 145892"
        m = re.search(r"CRM[\s/-]*([A-Z]{2})[\s-]*(\d{3,7})", texto)
        if m:
            return f"{m.group(2)}-{m.group(1)}"
        # Formato "145892-SP"
        m = re.fullmatch(r"(\d{3,7})[\s-]*([A-Z]{2})", texto)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        return valor


class Medicamento(BaseModel):
    nome: str
    dosagem: Optional[str] = None
    posologia: Optional[str] = None  # ex.: "1 comprimido de 8/8h"
    legivel: bool = True  # False quando o LLM não conseguiu ler com certeza

    @model_validator(mode="before")
    @classmethod
    def nome_ausente_vira_ilegivel(cls, dados):
        """O Gemini às vezes devolve `nome: null` para um remédio que não conseguiu ler.

        Em vez de rejeitar a extração INTEIRA por causa de um item, guardamos o item como
        "ILEGIVEL" e marcamos legivel=False (isso gera penalidade e vai para revisão humana).
        """
        if isinstance(dados, dict) and (dados.get("nome") is None or not str(dados.get("nome")).strip()):
            dados = {**dados, "nome": "ILEGIVEL", "legivel": False}
        return dados


class DadosExtraidos(BaseModel):
    paciente: Paciente = Paciente()
    medico_solicitante: MedicoSolicitante = MedicoSolicitante()
    exame_realizado: Optional[str] = None
    diagnostico_principal: Optional[str] = None
    cid_sugerido: Optional[str] = None  # ex.: "I26.9"
    medicamentos: list[Medicamento] = []

    # --- Sinais de QUALIDADE da leitura, informados pelo Gemini ---
    # `exclude=True`: usados só pelas regras (rules.py) e guardados nos metadados;
    # NÃO aparecem no JSON de saída, que continua igual ao exemplo do enunciado.
    score_confianca_extracao: Optional[float] = Field(None, ge=0, le=1, exclude=True)
    campos_incertos: list[str] = Field(default_factory=list, exclude=True)

    @field_validator("campos_incertos", mode="before")
    @classmethod
    def lista_nula_vira_vazia(cls, valor):
        return [] if valor is None else valor

    @model_serializer(mode="wrap")
    def omitir_medicamentos_vazio(self, handler):
        """Ao gerar o JSON de saída, omite 'medicamentos' quando a lista é vazia.

        Num laudo (ex.: TEP) não há medicamentos, e o JSON esperado do enunciado
        não traz esse campo. Em receitas, a lista tem itens e aparece normalmente.
        """
        dados = handler(self)
        if not dados.get("medicamentos"):
            dados.pop("medicamentos", None)
        return dados

    @field_validator("cid_sugerido")
    @classmethod
    def validar_cid(cls, valor: Optional[str]) -> Optional[str]:
        """CID-10 tem o formato: 1 letra + 2 dígitos + (ponto + 1 dígito opcional)."""
        if valor is None:
            return None
        valor = valor.upper().strip()
        if not re.fullmatch(r"[A-Z]\d{2}(\.\d{1,2})?", valor):
            raise ValueError(f"CID-10 inválido: {valor!r}")
        return valor


# ---------------------------------------------------------------------------
# 4) CLASSIFICAÇÃO E DECISÃO
# ---------------------------------------------------------------------------
class Classificacao(BaseModel):
    tipo_documento: TipoDocumento
    especialidade: Optional[str] = None
    nivel_prioridade: NivelPrioridade
    # Field(ge=0, le=1): confiança é sempre um número entre 0 e 1.
    score_confianca_classificacao: float = Field(..., ge=0, le=1)


class Notificacao(BaseModel):
    canal: str  # ex.: "Alerta_Plantao_Medico"
    mensagem: str


class DecisaoRoteamento(BaseModel):
    destino_principal: DestinoRoteamento
    requer_auditoria_humana: bool
    justificativa_roteamento: str  # SEMPRE explicar a decisão (auditável)
    notificacao_gerada: Optional[Notificacao] = None


class ArmazenamentoOCI(BaseModel):
    bucket: str
    caminho_objeto: str  # ex.: "processados/urgentes/DOC-CLIN-2026-8942.json"
    status_backup: str  # "sucesso" | "falha"


# ---------------------------------------------------------------------------
# 5) SAÍDA FINAL: o JSON que a API devolve
# ---------------------------------------------------------------------------
class RespostaTriagem(BaseModel):
    status: str  # "processado" | "erro"
    documento_id: str
    classificacao: Classificacao
    dados_extraidos: DadosExtraidos
    decisao_roteamento: DecisaoRoteamento
    armazenamento_oci: Optional[ArmazenamentoOCI] = None
