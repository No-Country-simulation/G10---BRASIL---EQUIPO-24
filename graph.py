"""
graph.py: ETAPA 5 do MediFlow (o grafo de decisão com LangGraph)

CONCEITOS DO LANGGRAPH (em linguagem simples):
  - ESTADO : um dicionário compartilhado que viaja pelo grafo. Cada nó lê o que
             precisa e devolve só o que quer ATUALIZAR.
  - NÓ     : uma função Python que faz uma etapa do trabalho.
  - ARESTA : a seta entre dois nós ("depois do A, vá para o B").
  - ARESTA CONDICIONAL : uma seta com um "if": uma função olha o estado e diz
             para qual nó ir. É aqui que mora a inteligência do roteamento.

O DESENHO:

  START -> ingestao -> classificar --(erro?)--+--> extrair -> avaliar
                                              |                  ^
                                              +------------------+
                                                                 |
                  avaliar --(escolher_rota)--+--> rota_urgente --+
                                             +--> rota_revisao --+--> salvar -> END
                                             +--> rota_padrao  --+

Se o Gemini falhar, NÃO derrubamos a API: o erro vai para o estado e o documento
cai na revisão humana. Em saúde, "não sei" deve virar "chame um humano".
"""

from datetime import datetime, timezone
from typing import Optional, TypedDict, Union

from langgraph.graph import END, START, StateGraph

import rules

from classifier import classificar_documento
from config import carregar_config
from extractor import ErroExtracao, extrair_dados
from schemas import (
    ArmazenamentoOCI,
    Classificacao,
    DadosExtraidos,
    DecisaoRoteamento,
    NivelPrioridade,
    RespostaTriagem,
    TipoDocumento,
)
# from storage import ArmazenamentoBase, criar_armazenamento


class EstadoTriagem(TypedDict, total=False):
    """O estado compartilhado. `total=False` = nem todo campo existe desde o início."""

    # entrada
    documento_id: str
    conteudo: Union[str, bytes]
    mime_type: Optional[str]
    canal_origem: Optional[str]
    # preenchidos ao longo do caminho
    caminho_original: Optional[str]
    classificacao: Classificacao
    dados: DadosExtraidos
    erros: list[str]
    avaliacao: rules.Avaliacao
    decisao: DecisaoRoteamento
    resposta: RespostaTriagem


_EXTENSOES = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def construir_grafo(client=None, armazenamento: Optional[ArmazenamentoBase] = None, config: Optional[dict] = None):
    """Monta e compila o grafo.

    Recebemos as dependências (Gemini, armazenamento, config) como parâmetros em vez
    de criá-las dentro dos nós. Isso se chama INJEÇÃO DE DEPENDÊNCIA e é o que
    permite testar o grafo inteiro com um Gemini falso e uma pasta temporária.
    """
    armazenamento = armazenamento or criar_armazenamento()
    config = config or carregar_config()

   #  ------------------------------ NÓS ------------------------------
    def ingestao(estado: EstadoTriagem) -> dict:
        """Guarda o documento ORIGINAL em recebidos/ (se falhar, seguimos mesmo assim)."""
        conteudo = estado["conteudo"]
        extensao = "txt" if isinstance(conteudo, str) else _EXTENSOES.get(estado.get("mime_type"), "bin")
        caminho = f"recebidos/{estado['documento_id']}.{extensao}"
        dados = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
        try:
            armazenamento.salvar(caminho, dados)
        except Exception as e:  # o backup do original não pode impedir a triagem
            return {"caminho_original": None, "erros": estado.get("erros", []) + [f"Falha ao salvar original: {e}"]}
        return {"caminho_original": caminho}

    def classificar(estado: EstadoTriagem) -> dict:
        try:
            classificacao = classificar_documento(estado["conteudo"], estado.get("mime_type"), client=client)
            return {"classificacao": classificacao}
        except ErroExtracao as e:
            return {"erros": estado.get("erros", []) + [str(e)]}

    def extrair(estado: EstadoTriagem) -> dict:
        try:
            return {"dados": extrair_dados(estado["conteudo"], estado.get("mime_type"), client=client)}
        except ErroExtracao as e:
            return {"erros": estado.get("erros", []) + [str(e)]}

    def avaliar(estado: EstadoTriagem) -> dict:
        # Se algo falhou antes, usamos valores "vazios e de baixa confiança".
        classificacao = estado.get("classificacao") or Classificacao(
            tipo_documento=TipoDocumento.DESCONHECIDO,
            nivel_prioridade=NivelPrioridade.ROTINA,
            score_confianca_classificacao=0.0,
        )
        dados = estado.get("dados") or DadosExtraidos()
        avaliacao = rules.avaliar(classificacao, dados, config, estado.get("erros"))
        return {"classificacao": classificacao, "dados": dados, "avaliacao": avaliacao}

    def rota_urgente(estado: EstadoTriagem) -> dict:
        return {
            "decisao": rules.decisao_urgente(
                estado["avaliacao"], estado["classificacao"], estado["dados"], estado.get("canal_origem")
            )
        }

    def rota_revisao(estado: EstadoTriagem) -> dict:
        return {"decisao": rules.decisao_revisao(estado["avaliacao"])}

    def rota_padrao(estado: EstadoTriagem) -> dict:
        return {"decisao": rules.decisao_padrao(estado["avaliacao"], estado["classificacao"], config)}

    def salvar(estado: EstadoTriagem) -> dict:
        """Monta a resposta final e grava o registro no bucket (pasta conforme a decisão)."""
        avaliacao, decisao = estado["avaliacao"], estado["decisao"]
        pasta = rules.pasta_de_armazenamento(decisao, avaliacao)
        caminho = f"{pasta}/{estado['documento_id']}.json"

        resposta = RespostaTriagem(
            status="erro" if estado.get("erros") else "processado",
            documento_id=estado["documento_id"],
            classificacao=estado["classificacao"],
            dados_extraidos=estado["dados"],
            decisao_roteamento=decisao,
            armazenamento_oci=ArmazenamentoOCI(
                bucket=armazenamento.bucket, caminho_objeto=caminho, status_backup=armazenamento.status_ok
            ),
        )
       # O registro guardado tem a resposta + metadados que ajudam o auditor.
        registro = {
            "resposta": resposta.model_dump(mode="json"),
            "metadados": {
                "recebido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "canal_origem": estado.get("canal_origem"),
                "documento_original": estado.get("caminho_original"),
                "avaliacao": avaliacao.model_dump(mode="json"),
            },
        }
        try:
            armazenamento.salvar_json(caminho, registro)
        except Exception:  # qualquer falha de gravação vira status_backup="falha" na resposta
            resposta.armazenamento_oci.status_backup = "falha"
        return {"resposta": resposta}

    #------------------- ARESTAS CONDICIONAIS (os "ifs") -------------------
    def apos_classificar(estado: EstadoTriagem) -> str:
        # Se a classificação falhou, não adianta extrair: vamos direto para a avaliação.
        return "extrair" if estado.get("classificacao") else "avaliar"

    def apos_avaliar(estado: EstadoTriagem) -> str:
        return rules.escolher_rota(estado["avaliacao"])  # "urgente" | "revisao" | "padrao"

    #------------------------------ MONTAGEM ------------------------------
    g = StateGraph(EstadoTriagem)
    for nome, funcao in [
        ("ingestao", ingestao),
        ("classificar", classificar),
        ("extrair", extrair),
        ("avaliar", avaliar),
        ("rota_urgente", rota_urgente),
        ("rota_revisao", rota_revisao),
        ("rota_padrao", rota_padrao),
        ("salvar", salvar),
    ]:
        g.add_node(nome, funcao)

    g.add_edge(START, "ingestao")
    g.add_edge("ingestao", "classificar")
    g.add_conditional_edges("classificar", apos_classificar, {"extrair": "extrair", "avaliar": "avaliar"})
    g.add_edge("extrair", "avaliar")
    g.add_conditional_edges(
        "avaliar",
        apos_avaliar,
        {"urgente": "rota_urgente", "revisao": "rota_revisao", "padrao": "rota_padrao"},
    )
    for rota in ("rota_urgente", "rota_revisao", "rota_padrao"):
        g.add_edge(rota, "salvar")
    g.add_edge("salvar", END)
    return g.compile()


def triar_documento(
    documento_id: str,
    conteudo: Union[str, bytes],
    mime_type: Optional[str] = None,
    canal_origem: Optional[str] = None,
    client=None,
    armazenamento: Optional[ArmazenamentoBase] = None,
    config: Optional[dict] = None,
) -> RespostaTriagem:
    """Função de alto nível: documento entra, `RespostaTriagem` sai."""
    if client is None:
        from extractor import criar_cliente

        client = criar_cliente()  # levanta ErroExtracao se não houver GEMINI_API_KEY
    grafo = construir_grafo(client, armazenamento, config)
    estado_final = grafo.invoke(
        {
            "documento_id": documento_id,
            "conteudo": conteudo,
            "mime_type": mime_type,
            "canal_origem": canal_origem,
            "erros": [],
        }
    )
    return estado_final["resposta"]


if __name__ == "__main__":
    # Imprime o diagrama do grafo em formato Mermaid (cole em mermaid.live para ver).
    print(construir_grafo(client=object(), armazenamento=criar_armazenamento()).get_graph().draw_mermaid())
