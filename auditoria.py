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
