"""
analise.py: ETAPA 3 (PASSO 1 de 3) do MediFlow: classificação + extração em UMA chamada

O QUE ESTE ARQUIVO FAZ?
Reaproveita o extractor.py (SEM alterá-lo) e acrescenta ao mesmo pedido ao Gemini
dois campos de classificação:
    - tipo_documento : uma das opções fechadas do enum TipoDocumento
    - especialidade  : texto curto (ex.: "Radiologia / Pneumologia") ou null

Devolve um `AnaliseDocumento`: classificação parcial + os `DadosExtraidos` de sempre.

POR QUE UM ARQUIVO NOVO?
Assim o `extrair_dados()` do extractor.py continua funcionando exatamente como antes.
Quem já usa essa função não precisa mudar nada.

PASSOS DA CLASSIFICAÇÃO:
    Passo 1 (este arquivo): tipo_documento + especialidade
    Passo 2 (próximo):      nivel_prioridade
    Passo 3 (depois):       score_confianca_classificacao
Quando os 3 passos estiverem prontos, o `AnaliseDocumento` poderá virar o
`Classificacao` completo do schemas.py.

COMO TESTAR SEM CHAVE DE API (usa um "Gemini de mentira"):
    python test_analise.py

COMO TESTAR COM O GEMINI DE VERDADE (precisa da GEMINI_API_KEY no .env):
    python analise.py exemplos/02_urgencia_laudo_tep.pdf
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, ValidationError

from extractor import (
    MAX_TENTATIVAS,
    MODELO_PADRAO,
    PROMPT_EXTRACAO,
    ErroExtracao,
    _montar_conteudo,
    criar_cliente,
    limpar_json,
    mime_por_extensao,
)
from schemas import DadosExtraidos, TipoDocumento

# ---------------------------------------------------------------------------
# 1) O PROMPT: o prompt do extractor + as instruções extras de classificação
# A lista de tipos vem direto do enum, então nunca fica fora de sincronia com ele.
# ---------------------------------------------------------------------------
_TIPOS_VALIDOS = ", ".join(f'"{tipo.value}"' for tipo in TipoDocumento)

PROMPT_CLASSIFICACAO = f"""

CLASSIFICAÇÃO (campos EXTRAS, no MESMO JSON, junto com os campos acima):
10. "tipo_documento": a categoria do documento. Use EXATAMENTE um destes valores:
    {_TIPOS_VALIDOS}.
    Se não for possível saber com segurança, use "Desconhecido". Não crie outros valores.
11. "especialidade": a especialidade médica envolvida, em texto curto
    (ex.: "Radiologia / Pneumologia"), ou null se não estiver clara.
12. Exceção à regra 4: além do "cid_sugerido", o "tipo_documento" e a "especialidade"
    também são classificações suas, feitas a partir do conteúdo do documento.
13. Coloque "tipo_documento" e "especialidade" no nível principal do JSON, ao lado de
    "paciente", "medico_solicitante" etc. (não dentro de outro objeto).
"""

PROMPT_ANALISE = PROMPT_EXTRACAO + PROMPT_CLASSIFICACAO


# ---------------------------------------------------------------------------
# 2) O "FORMULÁRIO" DESTE PASSO
# Modelo temporário: o `Classificacao` do schemas.py exige prioridade e score,
# que só entram nos passos 2 e 3. Por isso este aqui é menor.
# ---------------------------------------------------------------------------
class AnaliseDocumento(BaseModel):
    tipo_documento: TipoDocumento
    especialidade: Optional[str] = None
    dados_extraidos: DadosExtraidos


def _interpretar_resposta(texto: str) -> AnaliseDocumento:
    """Transforma o texto devolvido pelo Gemini em um AnaliseDocumento validado.

    Pode levantar json.JSONDecodeError, ValueError ou ValidationError:
    quem chama trata esses erros como "tentar de novo".
    """
    bruto = json.loads(limpar_json(texto))
    if not isinstance(bruto, dict):
        raise ValueError("A resposta do Gemini precisa ser um objeto JSON.")

    # Separa os campos novos; o que sobra é exatamente o formato do extractor.py
    tipo = bruto.pop("tipo_documento", None)
    especialidade = bruto.pop("especialidade", None)

    return AnaliseDocumento(
        tipo_documento=tipo,
        especialidade=especialidade,
        dados_extraidos=DadosExtraidos.model_validate(bruto),
    )


# ---------------------------------------------------------------------------
# 3) FUNÇÃO PRINCIPAL (mesma ideia do extrair_dados, com o prompt ampliado)
# ---------------------------------------------------------------------------
def analisar_documento(
    conteudo: Union[str, bytes],
    mime_type: Optional[str] = None,
    client=None,
    modelo: Optional[str] = None,
) -> AnaliseDocumento:
    """Classifica (tipo + especialidade) e extrai dados em UMA chamada ao Gemini.

    Parâmetros iguais aos de `extrair_dados`:
        conteudo:  texto do documento OU bytes do arquivo (PDF/imagem).
        mime_type: obrigatório para bytes (ex.: "application/pdf").
        client:    cliente do Gemini. Se None, cria um com a chave do .env.
        modelo:    nome do modelo; padrão vem de GEMINI_MODEL ou MODELO_PADRAO.

    Levanta ErroExtracao se não conseguir um resultado válido.
    """
    if isinstance(conteudo, str) and not conteudo.strip():
        raise ErroExtracao("Documento vazio.")

    client = client or criar_cliente()
    modelo = modelo or os.getenv("GEMINI_MODEL", MODELO_PADRAO)

    # Reaproveita a montagem do extractor.py e troca só o prompt (posição 0)
    mensagem = _montar_conteudo(conteudo, mime_type)
    mensagem[0] = PROMPT_ANALISE

    ultimo_erro: Optional[Exception] = None

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        # --- chamada ao Gemini ---
        try:
            resposta = client.models.generate_content(
                model=modelo,
                contents=mensagem,
                config={
                    "response_mime_type": "application/json",
                    "temperature": 0,
                },
            )
            texto = resposta.text or ""
        except Exception as e:
            raise ErroExtracao(f"Falha ao chamar o Gemini: {e}") from e

        # --- conferência com o Pydantic ---
        try:
            return _interpretar_resposta(texto)
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            ultimo_erro = e
            if tentativa < MAX_TENTATIVAS:
                mensagem = mensagem + [
                    f"Sua resposta anterior foi rejeitada: {e}\n"
                    "Responda novamente apenas com o JSON corrigido."
                ]

    raise ErroExtracao(
        f"O Gemini não devolveu um JSON válido após {MAX_TENTATIVAS} tentativas: {ultimo_erro}"
    )


def analisar_arquivo(caminho: Union[str, Path], **kwargs) -> AnaliseDocumento:
    """Atalho: lê um arquivo do disco (.txt, .pdf, .png, .jpg) e analisa."""
    caminho = Path(caminho)
    if caminho.suffix.lower() == ".txt":
        return analisar_documento(caminho.read_text(encoding="utf-8"), **kwargs)
    return analisar_documento(
        caminho.read_bytes(), mime_type=mime_por_extensao(caminho), **kwargs
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python analise.py <arquivo .txt|.pdf|.png|.jpg>")
        sys.exit(1)
    try:
        analise = analisar_arquivo(sys.argv[1])
    except ErroExtracao as erro:
        print(f"ERRO: {erro}")
        sys.exit(2)
    print(analise.model_dump_json(indent=2))
