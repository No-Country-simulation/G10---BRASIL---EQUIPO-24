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
from fastapi.testclient import TestClient  # <--- Importante para rodar em memória
from pydantic import BaseModel, ValidationError

import auditoria
from extractor import ErroExtracao, criar_cliente
from graph import triar_documento
from schemas import RequisicaoTriagem, RespostaTriagem, TipoArquivo
from storage import ArmazenamentoBase, criar_armazenamento

import streamlit as st
import json

# Configuração da página do Streamlit DEVE ser o primeiro comando executado
st.set_page_config(page_title="MediFlow API & Dashboard", layout="wide")

app = FastAPI(
    title="MediFlow",
    description="Agente autônomo para triagem, extração e roteamento de documentos clínicos.",
    version="1.0.0",
)

IdDocumento = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,100}$")]
TAMANHO_MAXIMO = 10 * 1024 * 1024
TIPOS_ACEITOS = {
    "application/pdf": TipoArquivo.PDF,
    "image/png": TipoArquivo.IMAGEM,
    "image/jpeg": TipoArquivo.IMAGEM,
    "image/webp": TipoArquivo.IMAGEM,
}

_armazenamento: Optional[ArmazenamentoBase] = None

def get_armazenamento() -> ArmazenamentoBase:
    global _armazenamento
    if _armazenamento is None:
        _armazenamento = criar_armazenamento()
    return _armazenamento

def get_client():
    try:
        return criar_cliente()
    except ErroExtracao as e:
        raise HTTPException(status_code=503, detail=str(e))

# ---------------------------------------------------------------------------
# ENDPOINTS DO FASTAPI (Mantidos intactos)
# ---------------------------------------------------------------------------
@app.post("/triagem", response_model=RespostaTriagem, tags=["Triagem"])
def triagem(req: RequisicaoTriagem, client=Depends(get_client), armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    if not req.documento_texto or not req.documento_texto.strip():
        raise HTTPException(status_code=422, detail="Envie 'documento_texto'. Para PDF/imagem use POST /triagem/arquivo.")
    return triar_documento(req.documento_id, req.documento_texto, canal_origem=req.canal_origem, client=client, armazenamento=armazenamento)

@app.post("/triagem/arquivo", response_model=RespostaTriagem, tags=["Triagem"])
def triagem_arquivo(
    file: UploadFile = File(..., description="PDF, PNG, JPG ou WEBP"),
    documento_id: str = Form(..., description="Ex.: DOC-CLIN-2026-8942"),
    canal_origem: Optional[str] = Form(None),
    client=Depends(get_client),
    armazenamento: ArmazenamentoBase = Depends(get_armazenamento),
):
    if file.content_type not in TIPOS_ACEITOS:
        raise HTTPException(415, f"Tipo não suportado: {file.content_type}. Use PDF, PNG, JPG ou WEBP.")
    try:
        RequisicaoTriagem(documento_id=documento_id, tipo_arquivo=TIPOS_ACEITOS[file.content_type])
    except ValidationError as e:
        raise HTTPException(422, detail=e.errors(include_url=False, include_context=False))

    conteudo = file.file.read(TAMANHO_MAXIMO + 1)
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(413, "Arquivo maior que 10 MB.")
    if not conteudo:
        raise HTTPException(422, "Arquivo vazio.")

    return triar_documento(documento_id, conteudo, mime_type=file.content_type, canal_origem=canal_origem, client=client, armazenamento=armazenamento)

class DecisaoAuditor(BaseModel):
    decisao: Literal["aprovado", "rejeitado"]
    auditor: str
    observacao: str = ""
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
def decidir(documento_id: IdDocumento, corpo: DecisaoAuditor, armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    try:
        novo_caminho = auditoria.registrar_decisao(armazenamento, documento_id, corpo.decisao, corpo.auditor, corpo.observacao, corpo.dados_corrigidos)
    except auditoria.ErroAuditoria as e:
        raise HTTPException(404 if "não está pendente" in str(e) else 422, str(e))
    return {"documento_id": documento_id, "decisao": corpo.decisao, "caminho_objeto": novo_caminho}

@app.get("/saude", tags=["Sistema"])
def saude(armazenamento: ArmazenamentoBase = Depends(get_armazenamento)):
    return {"status": "ok", "armazenamento": type(armazenamento).__name__, "bucket": armazenamento.bucket}


# ---------------------------------------------------------------------------
# INTERFACE STREAMLIT (Substitui o Swagger nativamente na Nuvem)
# ---------------------------------------------------------------------------
# Inicializa o cliente de teste que executa a API diretamente na memória
client_local = TestClient(app)

st.title("🚀 MediFlow - Playground da API Interativa")
st.markdown("Como o ambiente do Streamlit Cloud impede a exposição do Uvicorn convencional, utilize este painel para testar os endpoints da sua aplicação em tempo de execução.")

# Sidebar com status global do sistema
with st.sidebar:
    st.header("Status do Sistema")
    if st.button("Executar Check de Saúde (/saude)"):
        res = client_local.get("/saude")
        st.json(res.json())

# Organização por abas simulando o Swagger
aba_triagem, aba_auditoria = st.tabs(["📌 Triagem de Documentos", "⚖️ Auditoria Humana"])

with aba_triagem:
    st.subheader("POST /triagem - Texto Clínico")
    with st.form("form_triagem_texto"):
        doc_id = st.text_input("documento_id", value="DOC-TESTE-001")
        doc_texto = st.text_area("documento_texto (Conteúdo clínico para o Gemini analisar)")
        canal = st.text_input("canal_origem (Opcional)", value="web")
        submit_texto = st.form_submit_button("Try it out / Enviar")

        if submit_texto:
            payload = {"documento_id": doc_id, "documento_texto": doc_texto, "canal_origem": canal}
            resposta = client_local.post("/triagem", json=payload)
            st.metric("Status Code", resposta.status_code)
            st.json(resposta.json())

    st.markdown("---")
    st.subheader("POST /triagem/arquivo - Upload de Arquivo (PDF/Imagem)")
    with st.form("form_triagem_arquivo"):
        doc_id_arq = st.text_input("documento_id", value="DOC-ARQUIVO-002")
        canal_arq = st.text_input("canal_origem (Opcional)", value="upload")
        uploaded_file = st.file_uploader("Selecione o arquivo clínico", type=["pdf", "png", "jpg", "jpeg", "webp"])
        submit_arquivo = st.form_submit_button("Try it out / Enviar")

        if submit_arquivo and uploaded_file is not None:
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            data = {"documento_id": doc_id_arq, "canal_origem": canal_arq}
            resposta = client_local.post("/triagem/arquivo", data=data, files=files)
            st.metric("Status Code", resposta.status_code)
            st.json(resposta.json())

with aba_auditoria:
    st.subheader("GET /auditoria - Listar Documentos Pendentes")
    if st.button("Consultar Fila de Auditoria"):
        resposta = client_local.get("/auditoria")
        st.metric("Status Code", resposta.status_code)
        st.json(resposta.json())

    st.markdown("---")
    st.subheader("GET /auditoria/{documento_id} - Detalhes do Documento")
    id_busca = st.text_input("Insira o ID do documento pendente:")
    if st.button("Buscar Detalhes"):
        if id_busca:
            resposta = client_local.get(f"/auditoria/{id_busca}")
            st.metric("Status Code", resposta.status_code)
            st.json(resposta.json())
        else:
            st.warning("Por favor, digite um ID válido.")

    st.markdown("---")
    st.subheader("POST /auditoria/{documento_id}/decisao - Registrar Decisão")
    with st.form("form_decisao"):
        id_decisao = st.text_input("documento_id para Auditoria:")
        decisao = st.selectbox("Decisão do Auditor:", ["aprovado", "rejeitado"])
        auditor_nome = st.text_input("Nome do Auditor:")
        observacao = st.text_area("Observações:")
        dados_corrigidos_str = st.text_area("Dados Corrigidos (JSON string opcional):", value="{}")
        submit_decisao = st.form_submit_button("Registrar Decisão")

        if submit_decisao:
            try:
                dados_corrigidos = json.loads(dados_corrigidos_str) if dados_corrigidos_str.strip() else None
                payload = {"decisao": decisao, "auditor": auditor_nome, "observacao": observacao, "dados_corrigidos": dados_corrigidos}
                resposta = client_local.post(f"/auditoria/{id_decisao}/decisao", json=payload)
                st.metric("Status Code", resposta.status_code)
                st.json(resposta.json())
            except json.JSONDecodeError:
                st.error("O campo 'Dados Corrigidos' precisa conter um formato JSON válido.")
