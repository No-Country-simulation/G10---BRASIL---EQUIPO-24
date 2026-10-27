# 🏥 MediFlow
### Agente autônomo para triagem, extração e roteamento de documentos clínicos

Projeto da **Equipe 24** no **Hackathon ONE G10** (Oracle Next Education & Alura), pela plataforma NoCountry.

## Resumo

O MediFlow recebe documentos clínicos e administrativos (laudos, receitas, guias, relatórios de alta e atestados) em **texto, PDF ou imagem**, identifica o tipo, extrai os dados essenciais em JSON validado e decide para onde encaminhar cada um.

Casos urgentes vão direto para a fila de emergência, com alerta. Documentos duvidosos, incompletos ou ilegíveis vão para a **revisão de uma pessoa**. Os casos padrão seguem para o setor correto sem intervenção manual.

## O problema

Hospitais, clínicas, laboratórios e operadoras recebem milhares de documentos por dia, e equipes administrativas os leem manualmente para digitar dados em sistemas legados. Isso é lento, caro e sujeito a erros, e atrasos podem prejudicar pacientes em urgência e gerar glosas em autorizações.

**Público-alvo:** hospitais, clínicas, laboratórios e operadoras de saúde (equipes de auditoria, farmácia, prontuário e plantão médico).

## Como funciona

O fluxo é um **grafo de decisão em LangGraph**, com etapas e desvios condicionais:

1. **Ingestão:** guarda o original no bucket (`recebidos/`).
2. **Classificação (Gemini):** tipo do documento, especialidade, prioridade (Rotina, Prioritário ou Urgente) e score de confiança.
3. **Extração (Gemini):** paciente, médico e CRM, exame, diagnóstico, CID sugerido e medicamentos (nome, dosagem, posologia, legibilidade), validados com Pydantic.
4. **Avaliação (regras em Python):** urgência, campos faltando, inconsistências e score final.
5. **Roteamento:** urgente, revisão humana ou padrão.
6. **Armazenamento:** grava o resultado em JSON na pasta correspondente.

Se o Gemini falhar, o erro não derruba o sistema: o documento vai para a revisão humana. O conteúdo dos documentos é tratado como dado, nunca como instrução, para reduzir riscos de manipulação do modelo.

## Regras de decisão

**A IA sugere, a regra confirma.** A decisão final usa regras previsíveis e auditáveis, e não apenas a opinião do modelo.

- **Urgência:** prioridade "Urgente" sugerida pelo modelo **ou** termos críticos no diagnóstico (ex.: TEP, infarto, AVC, sepse), com tratamento de negações como "sem evidência de…".
- **Revisão humana:** campo obrigatório ausente, medicamento ilegível, inconsistência (ex.: receita sem medicamento), tipo desconhecido, erro de processamento ou score abaixo do limiar.
- **Score final:** parte do score da classificação e desconta cada problema encontrado.

| Situação | Destino |
|---|---|
| Urgente | Fila de Urgência Médica + alerta ao plantão |
| Urgente com dados duvidosos | Mesma fila de urgência, marcada para auditoria humana |
| Dados duvidosos | Fila de Revisão Humana |
| Receituário | Farmácia Hospitalar |
| Guia de procedimento | Auditoria de Autorizações |
| Laudos, relatório de alta e atestado | Prontuário Eletrônico |

Limiar de confiança, penalidades, campos obrigatórios, termos de urgência e destinos ficam em um arquivo de configuração e podem ser ajustados **sem mexer no código**.

## Tecnologias

| Camada | Tecnologia |
|---|---|
| Linguagem e validação | Python, Pydantic |
| Fluxo do agente | LangGraph |
| IA multimodal | Google Gemini (lê PDF e imagem direto, sem OCR separado) |
| Interface | Streamlit |
| Armazenamento | OCI Object Storage (Always Free) |
| Teste local do armazenamento | SeaweedFS no Docker (API S3) |

## Armazenamento

Os arquivos ficam em um bucket organizado por status:

- `recebidos/`: documentos originais
- `processados/`: resultados em JSON (com subpasta `urgentes/` e uma por destino)
- `auditoria_humana/`: casos aguardando revisão

Cada registro guarda a resposta da triagem e metadados (data, canal de origem e avaliação com os motivos). O código aceita três destinos: **OCI** (nuvem, só recursos Always Free), **SeaweedFS** em Docker e uma pasta local, usados para desenvolver e testar antes da nuvem.

## Interface

Painel em **Streamlit**. A aba de nova triagem (envio de arquivo ou texto, resultado, justificativa e alerta) está ativa. As abas de auditoria humana, regras de triagem e histórico do bucket estão em desenvolvimento.

## Status

- ✅ **Implementado:** classificação, extração, avaliação, roteamento em LangGraph, armazenamento local/SeaweedFS e tela de triagem.
- 🔄 **Em andamento:** integração com o OCI real, painel de auditoria humana, tela de regras e histórico.
- 📌 **Planejado, se houver tempo:** lista de medicamentos de alto risco, alertas em tempo real (Slack/e-mail), deploy no OCI Compute.

## Cenários de demonstração

1. **Rotina:** receita legível e completa, encaminhada à Farmácia sem intervenção humana.
2. **Urgência:** laudo de tomografia compatível com tromboembolismo pulmonar agudo, enviado à Fila de Urgência com alerta.
3. **Ambíguo:** documento ilegível ou incompleto, direcionado à revisão humana.

## Cuidados e limitações

- Usamos **apenas documentos fictícios**, sem dados reais de pacientes (LGPD).
- O CID-10 é uma **sugestão**, não um diagnóstico.
- A urgência é detectada a partir do diagnóstico extraído; termos fora de contexto podem gerar alertas desnecessários, o que é preferível a deixar de alertar.

## Demonstração

▶️ [Assista à demonstração no YouTube](https://www.youtube.com/watch?v=H7V5tj8-2xs)

🖥️ Tela do MediFlow
![Tela do MediFlow](https://media.discordapp.net/attachments/1559374106967801937/1564028207269814464/image.png?ex=6ae117c6&is=6adfc646&hm=ada92070a5c1d4f4918bfd30a18ccbcc164dc5831986764078f0f20c64bb4372&=&format=webp&quality=lossless)

- O MediFlow apoia a triagem e **não substitui** profissionais de saúde.

## Equipe e links

**Equipe 24:** Samuel Piovani, Leonardo Lacerda e Marcel Fagundes Souza.

**Repositório:** https://github.com/No-Country-simulation/G10---BRASIL---EQUIPO-24
