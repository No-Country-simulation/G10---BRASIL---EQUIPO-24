"""
auditoria.py: lógica do Human-in-the-Loop (HITL), usada pela API e pelo painel Streamlit

FLUXO:
  1. O grafo manda um documento duvidoso para  auditoria_humana/<id>.json
  2. O auditor abre o painel, confere (e pode corrigir os dados extraídos)
  3. Ao decidir, registramos a decisão e MOVEMOS o registro:
        aprovado  -> processados/revisados/<id>.json
        rejeitado -> processados/rejeitados/<id>.json
     Sempre com QUEM decidiu, QUANDO e a observação (trilha de auditoria).
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from schemas import DadosExtraidos
from storage import ArmazenamentoBase, ErroArmazenamento

PASTA_PENDENTES = "auditoria_humana"

_MIME_POR_EXTENSAO = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


class ErroAuditoria(Exception):
    pass


def listar_pendentes(armazenamento: ArmazenamentoBase) -> list[str]:
    """IDs dos documentos aguardando revisão."""
    nomes = armazenamento.listar(f"{PASTA_PENDENTES}/")
    return [n.split("/", 1)[1].removesuffix(".json") for n in nomes if n.endswith(".json")]


def carregar_pendente(armazenamento: ArmazenamentoBase, documento_id: str) -> dict:
    try:
        return armazenamento.ler_json(f"{PASTA_PENDENTES}/{documento_id}.json")
    except ErroArmazenamento as e:
        raise ErroAuditoria(f"Documento {documento_id!r} não está pendente de auditoria.") from e


def carregar_original(armazenamento: ArmazenamentoBase, documento_id: str) -> Optional[tuple[str, bytes]]:
    """Devolve (nome_do_objeto, bytes) do documento original salvo em recebidos/, se existir."""
    for nome in armazenamento.listar(f"recebidos/{documento_id}."):
        return nome, armazenamento.ler(nome)
    return None


def reprocessar(armazenamento: ArmazenamentoBase, documento_id: str, client, config: Optional[dict] = None):
    """Roda a triagem de novo a partir do ORIGINAL guardado em recebidos/.

    Útil quando o motivo da revisão foi um erro passageiro (ex.: Gemini sobrecarregado).
    Se o novo resultado for gravado em outra pasta, o registro pendente é removido.
    """
    from graph import triar_documento  # import aqui: evita carregar o grafo à toa

    pendente = carregar_pendente(armazenamento, documento_id)
    original = carregar_original(armazenamento, documento_id)
    if original is None:
        raise ErroAuditoria(f"O arquivo original de {documento_id!r} não foi encontrado em recebidos/.")

    nome, dados = original
    extensao = nome.rsplit(".", 1)[-1].lower()
    conteudo = dados.decode("utf-8") if extensao == "txt" else dados
    resposta = triar_documento(
        documento_id,
        conteudo,
        mime_type=_MIME_POR_EXTENSAO.get(extensao),
        canal_origem=pendente.get("metadados", {}).get("canal_origem"),
        client=client,
        armazenamento=armazenamento,
        config=config,
    )

    novo = resposta.armazenamento_oci.caminho_objeto
    gravou = resposta.armazenamento_oci.status_backup != "falha"
    if gravou and novo != f"{PASTA_PENDENTES}/{documento_id}.json":
        armazenamento.apagar(f"{PASTA_PENDENTES}/{documento_id}.json")  # saiu da fila do auditor
    return resposta


def registrar_decisao(
    armazenamento: ArmazenamentoBase,
    documento_id: str,
    decisao: str,
    auditor: str,
    observacao: str = "",
    dados_corrigidos: Optional[dict] = None,
) -> str:
    """Registra aprovação/rejeição e devolve o novo caminho do registro."""
    if decisao not in ("aprovado", "rejeitado"):
        raise ErroAuditoria("decisao deve ser 'aprovado' ou 'rejeitado'.")
    if not auditor.strip():
        raise ErroAuditoria("Informe o nome do auditor.")

    registro = carregar_pendente(armazenamento, documento_id)

    if dados_corrigidos is not None:
        try:
            # Só aceitamos correções que passem no mesmo schema da extração.
            validados = DadosExtraidos.model_validate(dados_corrigidos)
        except ValidationError as e:
            raise ErroAuditoria(f"Dados corrigidos inválidos: {e}") from e
        registro["dados_extraidos_original"] = registro["resposta"]["dados_extraidos"]
        registro["resposta"]["dados_extraidos"] = validados.model_dump(mode="json")

    registro["revisao_humana"] = {
        "decisao": decisao,
        "auditor": auditor.strip(),
        "observacao": observacao.strip(),
        "data_hora": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    pasta = "revisados" if decisao == "aprovado" else "rejeitados"
    destino = f"processados/{pasta}/{documento_id}.json"
    armazenamento.salvar_json(destino, registro)
    armazenamento.apagar(f"{PASTA_PENDENTES}/{documento_id}.json")
    return destino
