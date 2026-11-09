"""
main.py: ETAPA 7 do MediFlow (API REST com FastAPI)

COMO RODAR:
    uvicorn main:app --reload
Depois abra  http://127.0.0.1:8000/docs  : o FastAPI gera uma página interativa
onde você testa cada endpoint clicando em "Try it out".

ENDPOINTS:
    POST /triagem                       triagem de um documento em TEXTO (JSON do enunciado)
    POST /triagem/arquivo               triagem de PDF/imagem enviado como upload
    GET  /auditoria                     lista documentos aguardando revisão humana
    GET  /auditoria/{documento_id}      detalhes de um documento pendente
    POST /auditoria/{documento_id}/decisao   aprova/rejeita (com ou sem correções)
    GET  /saude                         verificação de funcionamento

POR QUE AS FUNÇÕES SÃO `def` E NÃO `async def`?
A chamada ao Gemini é "bloqueante" (espera a resposta). Com `def`, o FastAPI roda
cada requisição numa thread separada, e uma triagem lenta não trava as outras.
"""

from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, UploadFile
from pydantic import BaseModel, ValidationError

import auditoria
from extractor import ErroExtracao, criar_cliente
from graph import triar_documento
from schemas import RequisicaoTriagem, RespostaTriagem, TipoArquivo
from storage import ArmazenamentoBase, criar_armazenamento

import streamlit as st

app = FastAPI(
    title="MediFlow",
    description="Agente autônomo para triagem, extração e roteamento de documentos clínicos.",
    version="1.0.0",
)

# Mesma regra do schemas.py: id seguro para usar em caminhos de arquivo/objeto.
IdDocumento = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,100}$")]

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB por upload
TIPOS_ACEITOS = {
    "application/pdf": TipoArquivo.PDF,
    "image/png": TipoArquivo.IMAGEM,
    "image/jpeg": TipoArquivo.IMAGEM,
    "image/webp": TipoArquivo.IMAGEM,
}


# ---------------------------------------------------------------------------
# DEPENDÊNCIAS: o FastAPI "injeta" estes objetos nos endpoints.
# Nos testes, trocamos por versões falsas com `app.dependency_overrides`.
# ---------------------------------------------------------------------------
_armazenamento: Optional[ArmazenamentoBase] = None


def get_armazenamento() -> ArmazenamentoBase:
    global _armazenamento
    if _armazenamento is None:  # criado uma vez e reaproveitado
        _armazenamento = criar_armazenamento()
    return _armazenamento


def get_client():
    try:
        return criar_cliente()
    except ErroExtracao as e:  # ex.: GEMINI_API_KEY ausente
        raise HTTPException(status_code=503, detail=str(e))


# ---------------------------------------------------------------------------
# TRIAGEM
# ---------------------------------------------------------------------------
@app.post("/triagem", response_model=RespostaTriagem, tags=["Triagem"])
def triagem(
    req: RequisicaoTriagem,
    client=Depends(get_client),
    armazenamento: ArmazenamentoBase = Depends(get_armazenamento),
):
    """Recebe o JSON do enunciado (com `documento_texto`) e devolve a triagem."""
    if not req.documento_texto or not req.documento_texto.strip():
        raise HTTPException(
            status_code=422,
            detail="Envie 'documento_texto'. Para PDF/imagem use POST /triagem/arquivo.",
        )
    return triar_documento(
        req.documento_id,
        req.documento_texto,
        canal_origem=req.canal_origem,
        client=client,
        armazenamento=armazenamento,
    )


@app.post("/triagem/arquivo", response_model=RespostaTriagem, tags=["Triagem"])
def triagem_arquivo(
    file: UploadFile = File(..., description="PDF, PNG, JPG ou WEBP"),
    documento_id: str = Form(..., description="Ex.: DOC-CLIN-2026-8942"),
    canal_origem: Optional[str] = Form(None),
    client=Depends(get_client),
    armazenamento: ArmazenamentoBase = Depends(get_armazenamento),
):
    """Recebe um arquivo (PDF/imagem). O Gemini lê o arquivo direto, sem OCR separado."""
    if file.content_type not in TIPOS_ACEITOS:
        raise HTTPException(415, f"Tipo não suportado: {file.content_type}. Use PDF, PNG, JPG ou WEBP.")

    # Reaproveita as validações do schema (ex.: formato seguro do documento_id).
    try:
        RequisicaoTriagem(documento_id=documento_id, tipo_arquivo=TIPOS_ACEITOS[file.content_type])
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))

    conteudo = file.file.read(TAMANHO_MAXIMO + 1)  # lê 1 byte a mais só para detectar excesso
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(413, "Arquivo maior que 10 MB.")
    if not conteudo:
        raise HTTPException(422, "Arquivo vazio.")

    return triar_documento(
        documento_id,
        conteudo,
        mime_type=file.content_type,
        canal_origem=canal_origem,
        client=client,
        armazenamento=armazenamento,
    )


# ---------------------------------------------------------------------------
# AUDITORIA HUMANA (Human-in-the-Loop)
# ---------------------------------------------------------------------------
class DecisaoAuditor(BaseModel):
    decisao: Literal["aprovado", "rejeitado"]
    auditor: str
    observacao: str = ""
    # Opcional: o auditor pode corrigir os dados extraídos antes de aprovar.
    dados_corrigidos: Optional[dict] = None


@app.get("/auditoria", tags=["Auditoria humana"])
def listar_pendentes(armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    ids = auditoria.listar_pendentes(armazenamento)
    return {"total": len(ids), "documentos": ids}


@app.get("/auditoria/{documento_id}", tags=["Auditoria humana"])
def detalhe_pendente(documento_id: IdDocumento, armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    try:
        return auditoria.carregar_pendente(armazenamento, documento_id)
    except (auditoria.ErroAuditoria, ValueError) as e:
        raise HTTPException(404, str(e))


@app.post("/auditoria/{documento_id}/decisao", tags=["Auditoria humana"])
def decidir(
    documento_id: IdDocumento,
    corpo: DecisaoAuditor,
    armazenamento: ArmazenamentoBase = Depends(get_armazenamento),
):
    try:
        novo_caminho = auditoria.registrar_decisao(
            armazenamento,
            documento_id,
            corpo.decisao,
            corpo.auditor,
            corpo.observacao,
            corpo.dados_corrigidos,
        )
    except auditoria.ErroAuditoria as e:
        raise HTTPException(404 if "não está pendente" in str(e) else 422, str(e))
    return {"documento_id": documento_id, "decisao": corpo.decisao, "caminho_objeto": novo_caminho}


@app.get("/saude", tags=["Sistema"])
def saude(armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    return {"status": "ok", "armazenamento": type(armazenamento).__name__, "bucket": armazenamento.bucket}
