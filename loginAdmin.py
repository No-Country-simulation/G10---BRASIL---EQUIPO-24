import hmac
import os

import streamlit as st


# ===========================================================================
# ACESSO DO ADMINISTRADOR (protótipo: uma senha única, sem cadastro de usuários)
# Médicos usam só a aba "Nova triagem". Auditoria, Regras e Histórico exigem a senha.
# A senha NUNCA fica no código: vem de ADMIN_PASSWORD (segredos do Streamlit Cloud ou .env).
# ===========================================================================
def _senha_configurada() -> str:
    try:
        return str(st.secrets["ADMIN_PASSWORD"])  # Streamlit Cloud: Settings > Secrets
    except Exception:
        return os.getenv("ADMIN_PASSWORD", "")  # local: variável no .env


def _entrar_admin():
    senha = _senha_configurada()
    tentativa = st.session_state.get("senha_admin", "")
    if senha and hmac.compare_digest(tentativa.encode(), senha.encode()):
        st.session_state["admin"] = True
    else:
        st.session_state["admin"] = False
        st.session_state["erro_login"] = True
    st.session_state["senha_admin"] = ""  # limpa o campo


with st.sidebar:
    st.subheader("🔐 Administrador")
    if st.session_state.get("admin"):
        st.success("Acesso de administrador ativo.")
        if st.button("Sair"):
            st.session_state["admin"] = False
            st.rerun()
    else:
        st.text_input("Senha", type="password", key="senha_admin", on_change=_entrar_admin)
        if st.session_state.pop("erro_login", False):
            st.error("Senha incorreta.")
        if not _senha_configurada():
            st.caption("ADMIN_PASSWORD não configurada: as abas de administrador ficam bloqueadas.")
        else:
            st.caption("Médicos usam apenas a aba Nova triagem.")

eh_admin = bool(st.session_state.get("admin"))

nomes_abas = ["📥 Nova triagem"]
if eh_admin:
    nomes_abas += ["🧑‍⚕️ Auditoria humana", "⚙️ Regras de triagem", "🗄️ Histórico"]
abas = st.tabs(nomes_abas)
aba_triagem = abas[0]
if eh_admin:
    aba_auditoria, aba_regras, aba_historico = abas[1:]
    