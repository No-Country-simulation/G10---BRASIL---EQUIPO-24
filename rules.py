"""
rules.py: ETAPA 4 do MediFlow (regras de decisão)

AQUI NÃO TEM LLM. São regras de Python comuns: previsíveis, testáveis e auditáveis.
Se alguém perguntar "por que este documento foi para a revisão humana?", a resposta
está escrita na justificativa, e é sempre a mesma para a mesma entrada.

O QUE ESTE MÓDULO FAZ, EM 3 PASSOS:
  1) avaliar()        -> olha classificação + dados e produz uma `Avaliacao`
                         (urgente? score final? campos faltando? inconsistências?)
  2) escolher_rota()  -> transforma a avaliação em UMA de 3 rotas:
                         "urgente" | "revisao" | "padrao"
  3) decisao_*()      -> monta a `DecisaoRoteamento` (destino, justificativa, alerta)

ORDEM DE PRIORIDADE (do mais importante ao menos):
  1º URGENTE  -> sempre vai para a Fila de Emergência. Urgência nunca espera revisão.
                 (Se, além de urgente, os dados são duvidosos, o alerta sai igual E
                 marcamos `requer_auditoria_humana=True` para um humano conferir depois.)
  2º REVISÃO  -> confiança baixa, campo obrigatório ausente, inconsistência ou erro.
  3º PADRÃO   -> confiança alta e sem urgência: vai ao departamento do tipo de documento.
"""

import re
import unicodedata
from typing import Optional

from pydantic import BaseModel

from config import carregar_config
from schemas import (
    Classificacao,
    DadosExtraidos,
    DecisaoRoteamento,
    DestinoRoteamento,
    NivelPrioridade,
    Notificacao,
    TipoDocumento,
)

# Palavras que, escritas logo antes de um termo de urgência, indicam NEGAÇÃO
# ("sem evidência de TEP", "afasta infarto"): nesses casos NÃO disparamos urgência.
_NEGACOES = ("sem ", "ausencia de", "afasta", "descartad", "nega ", "negativo para", "nao ha", "excluido")


# ---------------------------------------------------------------------------
# 0) UTILIDADES
# ---------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """minúsculas e sem acentos: 'Embolia Pulmonar' e 'embólia pulmonar' ficam iguais."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower()


def _obter(dados: DadosExtraidos, caminho: str):
    """Lê 'paciente.nome' de dentro do objeto DadosExtraidos."""
    valor = dados
    for parte in caminho.split("."):
        valor = getattr(valor, parte, None)
        if valor is None:
            return None
    return valor


def _vazio(valor) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


# ---------------------------------------------------------------------------
# 1) AVALIAÇÃO
# ---------------------------------------------------------------------------
class Avaliacao(BaseModel):
    """Resultado da análise: tudo que as regras descobriram sobre o documento."""

    urgente: bool
    termos_urgencia: list[str] = []
    campos_faltantes: list[str] = []
    inconsistencias: list[str] = []
    erros: list[str] = []
    score_classificacao: float
    score_final: float
    confianca_baixa: bool
    motivos_revisao: list[str] = []


def detectar_termos_urgencia(texto: Optional[str], palavras: list[str]) -> list[str]:
    """Procura palavras de urgência no texto, ignorando as que estão negadas."""
    if not texto:
        return []
    alvo = normalizar(texto)
    achados = []
    for palavra in palavras:
        termo = normalizar(palavra)
        # \b não funciona bem com termos curtos; usamos "não vizinho de letra/número".
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])", alvo):
            antes = alvo[max(0, m.start() - 25) : m.start()]
            if not any(neg in antes for neg in _NEGACOES):
                achados.append(termo)
                break
    return achados


def verificar_inconsistencias(classificacao: Classificacao, dados: DadosExtraidos) -> list[str]:
    """Contradições simples entre o que foi classificado e o que foi extraído."""
    problemas = []
    tipo = classificacao.tipo_documento
    if tipo == TipoDocumento.RECEITUARIO and not dados.medicamentos:
        problemas.append("Documento classificado como receituário, mas nenhum medicamento foi extraído.")
    if tipo in (TipoDocumento.LAUDO_IMAGEM, TipoDocumento.LAUDO_LABORATORIAL):
        if _vazio(dados.exame_realizado) and _vazio(dados.diagnostico_principal):
            problemas.append("Laudo sem exame realizado e sem diagnóstico identificáveis.")
    return problemas


def avaliar(
    classificacao: Classificacao,
    dados: DadosExtraidos,
    config: Optional[dict] = None,
    erros: Optional[list[str]] = None,
) -> Avaliacao:
    """Passo 1: analisa o documento e calcula o score final de confiança."""
    config = config or carregar_config()
    erros = erros or []

    # --- urgência: a sugestão do LLM + as palavras-chave (rede de segurança) ---
    termos = detectar_termos_urgencia(dados.diagnostico_principal, config["palavras_urgencia"])
    urgente = classificacao.nivel_prioridade == NivelPrioridade.URGENTE or bool(termos)

    # --- campos obrigatórios ausentes ---
    faltantes = [c for c in config["campos_obrigatorios"] if _vazio(_obter(dados, c))]

    # --- inconsistências ---
    inconsistencias = verificar_inconsistencias(classificacao, dados)

    # --- score final: parte do score do LLM e desconta cada problema ---
    ilegiveis = [m for m in dados.medicamentos if not m.legivel]
    score = classificacao.score_confianca_classificacao
    score -= config["penalidade_campo_faltante"] * len(faltantes)
    score -= config["penalidade_medicamento_ilegivel"] * len(ilegiveis)
    score = round(max(0.0, min(1.0, score)), 2)
    confianca_baixa = score < config["limiar_confianca"]

    # --- motivos pelos quais um humano precisa olhar ---
    motivos = []
    if erros:
        motivos += [f"Erro de processamento: {e}" for e in erros]
    if confianca_baixa:
        motivos.append(f"Score de confiança {score:.2f} abaixo do limiar {config['limiar_confianca']:.2f}.")
    if faltantes:
        motivos.append("Campos obrigatórios ausentes: " + ", ".join(faltantes) + ".")
    if ilegiveis:
        motivos.append(f"{len(ilegiveis)} medicamento(s) com leitura incerta/ilegível.")
    if classificacao.tipo_documento == TipoDocumento.DESCONHECIDO:
        motivos.append("Tipo de documento não identificado.")
    motivos += inconsistencias

    return Avaliacao(
        urgente=urgente,
        termos_urgencia=termos,
        campos_faltantes=faltantes,
        inconsistencias=inconsistencias,
        erros=erros,
        score_classificacao=classificacao.score_confianca_classificacao,
        score_final=score,
        confianca_baixa=confianca_baixa,
        motivos_revisao=motivos,
    )


# ---------------------------------------------------------------------------
# 2) ESCOLHA DA ROTA (é isto que a aresta condicional do grafo chama)
# ---------------------------------------------------------------------------
def escolher_rota(av: Avaliacao) -> str:
    if av.urgente:
        return "urgente"
    if av.motivos_revisao:
        return "revisao"
    return "padrao"


# ---------------------------------------------------------------------------
# 3) DECISÕES
# ---------------------------------------------------------------------------
_NOMES_CANAL = {"Pronto_Socorro": "Pronto-Socorro"}


def _canal_legivel(canal: Optional[str]) -> str:
    if not canal:
        return "canal nao informado"
    return _NOMES_CANAL.get(canal, canal.replace("_", " "))


def decisao_urgente(
    av: Avaliacao,
    classificacao: Classificacao,
    dados: DadosExtraidos,
    canal_origem: Optional[str] = None,
) -> DecisaoRoteamento:
    diagnostico = dados.diagnostico_principal or "achado critico nao especificado"
    paciente = dados.paciente.nome or "paciente nao identificado"
    tipo = "Laudo" if "Laudo" in classificacao.tipo_documento.value else "Documento"

    origem = []
    if av.termos_urgencia:
        origem.append("termos de urgencia: " + ", ".join(av.termos_urgencia))
    if classificacao.nivel_prioridade == NivelPrioridade.URGENTE:
        origem.append("prioridade classificada como Urgente")
    justificativa = f"Achado critico de alta gravidade detectado: {diagnostico} ({'; '.join(origem)})."

    # Urgente com dados duvidosos: o alerta sai MESMO ASSIM, e pedimos conferência humana.
    requer_auditoria = bool(av.motivos_revisao)
    if requer_auditoria:
        justificativa += " ATENCAO: dados com problemas, conferencia humana tambem solicitada: " + " ".join(
            av.motivos_revisao
        )

    return DecisaoRoteamento(
        destino_principal=DestinoRoteamento.FILA_EMERGENCIA,
        requer_auditoria_humana=requer_auditoria,
        justificativa_roteamento=justificativa,
        notificacao_gerada=Notificacao(
            canal="Alerta_Plantao_Medico",
            mensagem=(
                f"ALERTA URGENTE: {tipo} critico de {diagnostico} para paciente "
                f"{paciente} no {_canal_legivel(canal_origem)}."
            ),
        ),
    )


def decisao_revisao(av: Avaliacao) -> DecisaoRoteamento:
    return DecisaoRoteamento(
        destino_principal=DestinoRoteamento.REVISAO_HUMANA,
        requer_auditoria_humana=True,
        justificativa_roteamento="Encaminhado para revisao humana. " + " ".join(av.motivos_revisao),
    )


def decisao_padrao(
    av: Avaliacao, classificacao: Classificacao, config: Optional[dict] = None
) -> DecisaoRoteamento:
    config = config or carregar_config()
    destino_txt = config["destino_por_tipo"].get(classificacao.tipo_documento.value)
    # Tipo sem destino configurado: não adivinhamos, mandamos para um humano.
    if destino_txt is None:
        return decisao_revisao(
            av.model_copy(
                update={"motivos_revisao": [f"Sem destino configurado para {classificacao.tipo_documento.value!r}."]}
            )
        )
    destino = DestinoRoteamento(destino_txt)
    return DecisaoRoteamento(
        destino_principal=destino,
        requer_auditoria_humana=False,
        justificativa_roteamento=(
            f"{classificacao.tipo_documento.value} com confianca {av.score_final:.2f}, "
            f"campos obrigatorios presentes e sem achados criticos. Encaminhado para {destino.value}."
        ),
    )


def pasta_de_armazenamento(decisao: DecisaoRoteamento, av: Avaliacao) -> str:
    """Em qual 'pasta' do bucket o resultado será salvo (ex.: processados/urgentes)."""
    if decisao.requer_auditoria_humana:
        return "auditoria_humana"
    if av.urgente:
        return "processados/urgentes"
    return f"processados/{decisao.destino_principal.value.lower()}"
