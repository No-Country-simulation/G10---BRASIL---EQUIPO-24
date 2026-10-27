"""
app.py: ETAPA 8 do MediFlow (painel Streamlit: triagem + auditoria humana + regras)

COMO RODAR:
    streamlit run app.py

O Streamlit transforma um script Python em página web. A cada clique, o script
roda de novo de cima para baixo; por isso guardamos coisas em `st.session_state`.

ABAS:
  1. Nova triagem      : envia um documento e vê o resultado do agente.
  2. Auditoria humana  : o auditor aprova/rejeita (e corrige) casos duvidosos.
  3. Regras de triagem : o gestor muda limiar, palavras de urgência e destinos
                         SEM mexer no código (diferencial "fluxo customizável").
  4. Histórico         : o que já foi gravado no bucket.
"""

import json
from datetime import datetime

import streamlit as st

import auditoria
from config import CONFIG_PADRAO, carregar_config, salvar_config
from extractor import ErroExtracao, criar_cliente
from graph import triar_documento
from schemas import DestinoRoteamento, TipoDocumento
from storage import criar_armazenamento
from diagnostico import diagnosticar

st.set_page_config(page_title="MediFlow", page_icon="🏥", layout="wide")

MIME_POR_EXT = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}
ICONES = {
    "Fila_Emergencia_Medica": "🚨",
    "Fila_Revisao_Humana": "🧑‍⚕️",
    "Farmacia_Hospitalar": "💊",
    "Auditoria_Autorizacoes": "📋",
    "Prontuario_Eletronico": "🗂️",
}


@st.cache_resource
def obter_armazenamento():
    return criar_armazenamento()


armazenamento = obter_armazenamento()

st.title("🏥 MediFlow")
st.caption(
    f"Triagem inteligente de documentos clínicos · armazenamento: **{type(armazenamento).__name__}** "
    f"(bucket `{armazenamento.bucket}`) · ⚠️ use apenas dados FICTÍCIOS"
)

aba_triagem, aba_auditoria, aba_regras, aba_historico = st.tabs(
    ["📥 Nova triagem", "🧑‍⚕️ Auditoria humana", "⚙️ Regras de triagem", "🗄️ Histórico"]
)

# ===========================================================================
# ABA 1: NOVA TRIAGEM
# ===========================================================================
with aba_triagem:
    col_a, col_b = st.columns([1, 1])
    with col_a:
        doc_id = st.text_input("ID do documento", value=f"DOC-{datetime.now():%Y%m%d-%H%M%S}")
        canal = st.text_input("Canal de origem", value="Pronto_Socorro")
        arquivo = st.file_uploader("Arquivo (PDF, imagem ou .txt)", type=["pdf", "png", "jpg", "jpeg", "txt"])
        texto = st.text_area("…ou cole o texto do documento", height=180)
        enviar = st.button("Processar documento", type="primary")

    with col_b:
        if enviar:
            try:
                if arquivo is not None and arquivo.name.lower().endswith(".txt"):
                    conteudo, mime = arquivo.getvalue().decode("utf-8"), None
                elif arquivo is not None:
                    conteudo, mime = arquivo.getvalue(), MIME_POR_EXT[arquivo.name.rsplit(".", 1)[-1].lower()]
                elif texto.strip():
                    conteudo, mime = texto, None
                else:
                    st.warning("Envie um arquivo ou cole um texto.")
                    st.stop()

                with st.spinner("O agente está lendo e decidindo…"):
                    resposta = triar_documento(
                        doc_id,
                        conteudo,
                        mime_type=mime,
                        canal_origem=canal or None,
                        client=criar_cliente(),
                        armazenamento=armazenamento,
                        config=carregar_config(),
                    )
                st.session_state["ultima_resposta"] = resposta.model_dump(mode="json")
            except ErroExtracao as e:
                st.error(f"Não foi possível processar: {e}")
            except Exception as e:  # ex.: ID inválido
                st.error(f"Erro: {e}")

        r = st.session_state.get("ultima_resposta")
        if r:
            d = r["decisao_roteamento"]
            icone = ICONES.get(d["destino_principal"], "📄")
            if d["destino_principal"] == "Fila_Emergencia_Medica":
                st.error(f"{icone} URGENTE → {d['destino_principal']}")
            elif d["requer_auditoria_humana"]:
                st.warning(f"{icone} Requer auditoria humana → {d['destino_principal']}")
            else:
                st.success(f"{icone} {d['destino_principal']}")
            if d.get("notificacao_gerada"):
                st.error(d["notificacao_gerada"]["mensagem"])
            st.write("**Justificativa:**", d["justificativa_roteamento"])
            if r["status"] == "erro":
                st.warning("O processamento teve erros: o documento foi enviado à revisão humana.")
            st.json(r, expanded=False)

# ===========================================================================
# ABA 2: AUDITORIA HUMANA
# ===========================================================================
with aba_auditoria:
    pendentes = auditoria.listar_pendentes(armazenamento)
    st.subheader(f"Aguardando revisão: {len(pendentes)}")
    if not pendentes:
        st.info("Nenhum documento pendente. 🎉")
    else:
        escolhido = st.selectbox("Documento", pendentes)
        registro = auditoria.carregar_pendente(armazenamento, escolhido)
        resposta = registro["resposta"]
        meta = registro["metadados"]

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### Por que foi para revisão?")
            for motivo in meta["avaliacao"]["motivos_revisao"] or ["(sem motivos registrados)"]:
                st.warning(motivo)
            st.metric("Score final de confiança", meta["avaliacao"]["score_final"])
            st.write("**Classificação:**", resposta["classificacao"]["tipo_documento"])

            original = auditoria.carregar_original(armazenamento, escolhido)
            if original:
                nome, bytes_ = original
                st.markdown("#### Documento original")
                if nome.endswith(".txt"):
                    st.text(bytes_.decode("utf-8", errors="replace"))
                elif nome.endswith((".png", ".jpg", ".webp")):
                    st.image(bytes_)
                else:
                    st.download_button("Baixar PDF original", bytes_, file_name=nome.split("/")[-1])

        with col2:
            st.markdown("#### Dados extraídos (editáveis)")
            editado = st.text_area(
                "JSON", value=json.dumps(resposta["dados_extraidos"], ensure_ascii=False, indent=2),
                height=330, key=f"json_{escolhido}",
            )
            auditor = st.text_input("Seu nome (auditor)", key=f"aud_{escolhido}")
            obs = st.text_input("Observação", key=f"obs_{escolhido}")
            b1, b2 = st.columns(2)

            def _decidir(decisao: str):
                try:
                    corrigido = json.loads(editado)
                    caminho = auditoria.registrar_decisao(
                        armazenamento, escolhido, decisao, auditor, obs,
                        dados_corrigidos=corrigido if corrigido != resposta["dados_extraidos"] else None,
                    )
                    st.success(f"Registrado em `{caminho}`")
                    st.rerun()
                except json.JSONDecodeError as e:
                    st.error(f"JSON inválido: {e}")
                except auditoria.ErroAuditoria as e:
                    st.error(str(e))

            if b1.button("✅ Aprovar", key=f"ap_{escolhido}"):
                _decidir("aprovado")
            if b2.button("❌ Rejeitar", key=f"rj_{escolhido}"):
                _decidir("rejeitado")

# ===========================================================================
# ABA 3: REGRAS DE TRIAGEM (diferencial)
# ===========================================================================
with aba_regras:
    cfg = carregar_config()
    st.subheader("Regras de triagem (sem mexer no código)")
    limiar = st.slider("Limiar de confiança (abaixo disso → revisão humana)", 0.0, 1.0, float(cfg["limiar_confianca"]), 0.01)
    palavras = st.text_area(
        "Palavras de urgência (uma por linha)", value="\n".join(cfg["palavras_urgencia"]), height=220
    )
    st.markdown("**Destino por tipo de documento** (caso padrão)")
    destinos = [d.value for d in DestinoRoteamento if d != DestinoRoteamento.REVISAO_HUMANA]
    novo_mapa = {}
    for tipo in TipoDocumento:
        if tipo == TipoDocumento.DESCONHECIDO:
            continue
        atual = cfg["destino_por_tipo"].get(tipo.value, destinos[0])
        novo_mapa[tipo.value] = st.selectbox(tipo.value, destinos, index=destinos.index(atual) if atual in destinos else 0, key=f"dest_{tipo.name}")

    c1, c2 = st.columns(2)
    if c1.button("💾 Salvar regras", type="primary"):
        cfg.update(
            limiar_confianca=limiar,
            palavras_urgencia=[p.strip() for p in palavras.splitlines() if p.strip()],
        )
        cfg["destino_por_tipo"] = novo_mapa
        try:
            salvar_config(cfg)
            st.success("Regras salvas! Valem a partir da próxima triagem.")
        except ValueError as e:
            st.error(str(e))
    if c2.button("↩️ Restaurar padrão"):
        salvar_config(CONFIG_PADRAO)
        st.rerun()

# ===========================================================================
# ABA 4: HISTÓRICO
# ===========================================================================
with aba_historico:
    st.subheader("Objetos no bucket")
    prefixo = st.selectbox("Pasta", ["", "recebidos/", "processados/", "auditoria_humana/"], format_func=lambda x: x or "(tudo)")
    objetos = armazenamento.listar(prefixo)
    st.write(f"{len(objetos)} objeto(s)")
    st.dataframe({"objeto": objetos}, width="stretch")
    abrir = st.selectbox("Ver conteúdo de um .json", [""] + [o for o in objetos if o.endswith(".json")])
    if abrir:
        st.json(armazenamento.ler_json(abrir))
