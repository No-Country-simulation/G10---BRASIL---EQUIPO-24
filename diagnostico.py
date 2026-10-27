"""
diagnostico.py: traduz erros técnicos (quase sempre do Gemini) para linguagem de gente.

POR QUE EXISTE?
Um erro como  "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is
currently experiencing high demand...'}}"  não ajuda o auditor. Aqui classificamos o
erro em categorias e devolvemos: o que aconteceu, o que fazer e se vale tentar de novo.

`e_temporario()` também é usado pelo extrator para decidir se repete a chamada
automaticamente (erros passageiros) ou desiste na hora (chave inválida, por exemplo).
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostico:
    categoria: str
    titulo: str
    explicacao: str
    acao: str
    temporario: bool  # True = vale a pena tentar de novo


# A ORDEM IMPORTA: a primeira categoria que casar vence.
_CATEGORIAS = [
    (r"GEMINI_API_KEY|API key|API_KEY|PERMISSION_DENIED|UNAUTHENTICATED|\b401\b|\b403\b",
     Diagnostico("chave", "Chave do Gemini ausente ou inválida",
                 "O Gemini recusou a autenticação.",
                 "Confira a GEMINI_API_KEY no arquivo .env e reinicie a aplicação.", False)),
    (r"Documento vazio",
     Diagnostico("documento_vazio", "Documento vazio",
                 "Não havia conteúdo para analisar.", "Envie o documento novamente.", False)),
    (r"\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit",
     Diagnostico("limite", "Limite de uso do Gemini atingido",
                 "A cota gratuita (por minuto ou por dia) foi atingida.",
                 "Aguarde alguns minutos e use Reprocessar. Se persistir, a cota diária acabou: tente amanhã ou use outra chave.", True)),
    (r"\b503\b|UNAVAILABLE|overloaded|high demand|sobrecarreg",
     Diagnostico("indisponivel", "Gemini temporariamente indisponível",
                 "O serviço do Google está com alta demanda (erro 503). Isso é passageiro e não tem relação com o documento.",
                 "Aguarde alguns minutos e use Reprocessar.", True)),
    (r"\b504\b|DEADLINE_EXCEEDED|timed? ?out|timeout",
     Diagnostico("tempo_esgotado", "O Gemini demorou demais para responder",
                 "A resposta não chegou a tempo.", "Use Reprocessar. Arquivos grandes podem demorar mais.", True)),
    (r"\b500\b|INTERNAL|\b502\b",
     Diagnostico("erro_servidor", "Erro interno no servidor do Gemini",
                 "Falha do lado do Google (erro 5xx).", "Aguarde um pouco e use Reprocessar.", True)),
    (r"\b404\b|NOT_FOUND|is not found for API",
     Diagnostico("modelo", "Modelo do Gemini não encontrado",
                 "O nome do modelo configurado não existe ou foi descontinuado.",
                 "Ajuste GEMINI_MODEL no .env (ex.: gemini-2.5-flash).", False)),
    (r"Connection|connect|Name or service|network|SSL|resolve",
     Diagnostico("rede", "Sem conexão com o Gemini",
                 "Não foi possível alcançar a internet ou o serviço.",
                 "Verifique a conexão (e proxy/firewall) e use Reprocessar.", True)),
    (r"JSON válido|rejeitada",
     Diagnostico("resposta_invalida", "O Gemini respondeu fora do formato esperado",
                 "A resposta não passou na validação, mesmo após uma nova tentativa.",
                 "Use Reprocessar. Se repetir, o documento pode ser incomum e precisa de leitura humana.", True)),
    (r"Falha ao salvar original|bucket|armazen",
     Diagnostico("armazenamento", "Falha ao gravar no armazenamento",
                 "Não foi possível salvar o arquivo original no bucket.",
                 "Verifique se o SeaweedFS/OCI está no ar (python storage.py --testar).", True)),
]

# Categorias em que repetir a chamada ao Gemini automaticamente faz sentido.
_REPETIVEIS = {"limite", "indisponivel", "tempo_esgotado", "erro_servidor", "rede"}


def diagnosticar(erro: str) -> Diagnostico:
    """Classifica o texto de um erro. Sem correspondência, devolve um diagnóstico genérico."""
    for padrao, diagnostico in _CATEGORIAS:
        if re.search(padrao, erro or "", re.IGNORECASE):
            return diagnostico
    return Diagnostico("desconhecido", "Erro inesperado no processamento",
                       "Algo deu errado e não reconhecemos o tipo de erro.",
                       "Veja os detalhes técnicos e use Reprocessar.", False)


def e_temporario(erro: str) -> bool:
    """True se vale repetir a chamada ao Gemini (erros passageiros de serviço/rede/cota)."""
    return diagnosticar(erro).categoria in _REPETIVEIS


def resumir(erro: str) -> str:
    """Uma frase curta para a justificativa da decisão. Sem categoria conhecida, devolve o texto original."""
    d = diagnosticar(erro)
    if d.categoria == "desconhecido":
        return erro
    return f"{d.titulo}. {d.acao}"
