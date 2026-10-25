"""
config.py: regras de triagem CONFIGURÁVEIS (diferencial "fluxo customizável")

IDEIA: em vez de deixar números e palavras "chumbados" no código, guardamos
tudo num arquivo `config_triagem.json`. O gestor hospitalar pode mudar o limiar de
confiança, as palavras de urgência e o destino de cada tipo de documento
(pela tela do Streamlit) SEM mexer no código.

Se o arquivo não existir (ou estiver quebrado), usamos os valores padrão abaixo.
"""

import copy
import json
import os
from pathlib import Path

from schemas import DestinoRoteamento, TipoDocumento

ARQUIVO_CONFIG = Path(os.getenv("CONFIG_TRIAGEM", "config_triagem.json"))

CONFIG_PADRAO: dict = {
    # Abaixo deste score final, o documento vai para revisão humana.
    "limiar_confianca": 0.80,
    # Quanto o score cai por problema encontrado na extração.
    "penalidade_campo_faltante": 0.20,
    "penalidade_medicamento_ilegivel": 0.15,
    # Campos que, se faltarem, obrigam revisão humana (caminho "objeto.campo").
    "campos_obrigatorios": ["paciente.nome", "medico_solicitante.crm"],
    # Termos que indicam achado crítico (comparados sem acento e sem maiúsculas).
    "palavras_urgencia": [
        "tep",
        "tromboembolismo pulmonar",
        "embolia pulmonar",
        "infarto",
        "iam",
        "avc",
        "acidente vascular",
        "hemorragia",
        "sepse",
        "choque septico",
        "parada cardiaca",
        "parada cardiorrespiratoria",
        "dissecção de aorta",
        "aneurisma roto",
        "abdome agudo",
        "pneumotorax hipertensivo",
    ],
    # Para onde vai cada tipo de documento no caso padrão (confiança alta, sem urgência).
    "destino_por_tipo": {
        TipoDocumento.RECEITUARIO.value: DestinoRoteamento.FARMACIA.value,
        TipoDocumento.GUIA_PROCEDIMENTO.value: DestinoRoteamento.AUDITORIA_AUTORIZACOES.value,
        TipoDocumento.LAUDO_LABORATORIAL.value: DestinoRoteamento.PRONTUARIO.value,
        TipoDocumento.LAUDO_IMAGEM.value: DestinoRoteamento.PRONTUARIO.value,
        TipoDocumento.RELATORIO_ALTA.value: DestinoRoteamento.PRONTUARIO.value,
        TipoDocumento.ATESTADO.value: DestinoRoteamento.PRONTUARIO.value,
    },
}


def carregar_config(caminho: Path = None) -> dict:
    """Lê o JSON de configuração e completa o que faltar com os valores padrão."""
    caminho = Path(caminho) if caminho else ARQUIVO_CONFIG
    config = copy.deepcopy(CONFIG_PADRAO)
    try:
        lido = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config  # sem arquivo (ou quebrado): padrão

    for chave, valor in lido.items():
        if chave not in config:
            continue  # ignora chaves desconhecidas
        if isinstance(config[chave], dict) and isinstance(valor, dict):
            config[chave].update(valor)
        else:
            config[chave] = valor
    return config


def validar_config(config: dict) -> list[str]:
    """Devolve uma lista de problemas (vazia = configuração válida)."""
    erros = []
    if not 0 <= config.get("limiar_confianca", -1) <= 1:
        erros.append("limiar_confianca deve estar entre 0 e 1.")
    destinos_validos = {d.value for d in DestinoRoteamento}
    tipos_validos = {t.value for t in TipoDocumento}
    for tipo, destino in config.get("destino_por_tipo", {}).items():
        if tipo not in tipos_validos:
            erros.append(f"Tipo de documento desconhecido: {tipo!r}")
        if destino not in destinos_validos:
            erros.append(f"Destino desconhecido para {tipo!r}: {destino!r}")
    if not isinstance(config.get("palavras_urgencia"), list):
        erros.append("palavras_urgencia deve ser uma lista.")
    return erros


def salvar_config(config: dict, caminho: Path = None) -> None:
    """Valida e grava a configuração. Levanta ValueError se for inválida."""
    erros = validar_config(config)
    if erros:
        raise ValueError("; ".join(erros))
    caminho = Path(caminho) if caminho else ARQUIVO_CONFIG
    caminho.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
