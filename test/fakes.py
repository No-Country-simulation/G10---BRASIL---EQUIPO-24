"""
Gemini "de mentira" para os testes: nenhum teste precisa de internet nem de chave.

ClienteRoteado olha a mensagem recebida e responde conforme a TAREFA:
  - se o prompt é o do classificador  -> devolve `classificacao`
  - caso contrário (extrator)         -> devolve `extracao`
Assim o teste não depende da ORDEM das chamadas dentro do grafo.
"""
import json


class _Resposta:
    def __init__(self, text):
        self.text = text


class _Models:
    def __init__(self, classificacao, extracao):
        self.classificacao, self.extracao = classificacao, extracao
        self.chamadas = 0

    def generate_content(self, model, contents, config=None):
        self.chamadas += 1
        escolhida = self.classificacao if "CLASSIFICADOR DE DOCUMENTOS" in str(contents[0]) else self.extracao
        if isinstance(escolhida, Exception):
            raise escolhida
        return _Resposta(escolhida if isinstance(escolhida, str) else json.dumps(escolhida))


class ClienteRoteado:
    def __init__(self, classificacao, extracao):
        self.models = _Models(classificacao, extracao)


# ---------------------------------------------------------------------------
# Respostas prontas dos 3 cenários exigidos
# ---------------------------------------------------------------------------
ROTINA = (
    {"tipo_documento": "Receituário Médico", "especialidade": "Clínica Geral",
     "nivel_prioridade": "Rotina", "score_confianca_classificacao": 0.97},
    {"paciente": {"nome": "Maria Aparecida Souza", "idade": 34},
     "medico_solicitante": {"nome": "Dr. Joao Pedro Alves", "crm": "CRM-SP 123456"},
     "diagnostico_principal": "Faringite Aguda", "cid_sugerido": "J02.9",
     "medicamentos": [
         {"nome": "Amoxicilina", "dosagem": "500 mg", "posologia": "1 capsula a cada 8 horas por 7 dias", "legivel": True},
         {"nome": "Dipirona", "dosagem": "500 mg", "posologia": "1 comprimido a cada 6 horas, se dor ou febre", "legivel": True}]},
)

URGENCIA = (
    {"tipo_documento": "Laudo de Exame de Imagem", "especialidade": "Radiologia / Pneumologia",
     "nivel_prioridade": "Urgente", "score_confianca_classificacao": 0.99},
    {"paciente": {"nome": "Carlos Eduardo Mendes", "idade": 52},
     "medico_solicitante": {"nome": "Dra. Renata Silveira", "crm": "145892-SP"},
     "exame_realizado": "Tomografia de Torax com contraste",
     "diagnostico_principal": "Tromboembolismo Pulmonar Agudo (TEP)", "cid_sugerido": "I26.9"},
)

AMBIGUO = (
    {"tipo_documento": "Receituário Médico", "especialidade": None,
     "nivel_prioridade": "Rotina", "score_confianca_classificacao": 0.62},
    {"paciente": {"nome": "J. Silva", "idade": None},
     "medico_solicitante": {"nome": None, "crm": None},
     "medicamentos": [
         {"nome": "Metformina", "dosagem": None, "posologia": None, "legivel": False},
         {"nome": "Losartana", "dosagem": None, "posologia": "1 comprimido pela manha", "legivel": False}]},
)
