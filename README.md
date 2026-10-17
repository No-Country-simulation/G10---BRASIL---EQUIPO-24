MediFlow
Agente autônomo para triagem, extração e roteamento de documentos clínicos
Projeto desenvolvido no Hackathon ONE G10 (Oracle Next Education & Alura), pela plataforma NoCountry.

Resumo
O MediFlow é um agente de inteligência artificial que recebe documentos clínicos e administrativos (laudos, receitas, guias de autorização, atestados e relatórios), identifica o tipo de cada um, extrai os dados essenciais em formato estruturado (JSON) e decide para qual área encaminhá-lo.

Casos urgentes seguem direto para a equipe médica. Quando há dados ilegíveis, faltando ou inconsistentes, o documento vai para a revisão de um auditor humano, garantindo mais segurança. Já os casos padrão são encaminhados sem intervenção manual.

O problema
Hospitais, laboratórios e operadoras de saúde recebem todos os dias milhares de documentos diferentes: laudos, receitas, guias de autorização (TISS/TUSS), pedidos de exames e relatórios cirúrgicos. Hoje, equipes administrativas leem esses documentos manualmente para digitar os dados em sistemas legados.

Esse processo é lento, caro e sujeito a erros de digitação. Além disso, atrasos no encaminhamento podem prejudicar pacientes em quadros de urgência, e falhas ou demoras nas autorizações de exames e medicamentos geram prejuízos financeiros, como as glosas hospitalares.

Público-alvo
Hospitais e clínicas médicas
Laboratórios
Operadoras de saúde
Dentro dessas organizações, o MediFlow atende equipes como auditoria de autorizações, farmácia hospitalar, prontuário eletrônico e a equipe médica de plantão, que recebem os documentos já classificados e com os dados extraídos.

Como funciona
O documento percorre seis etapas, do recebimento até o armazenamento:

Recebimento: o agente recebe o documento clínico em PDF, imagem ou texto/JSON.
Classificação: identifica a categoria, como receituário médico, laudo de exame laboratorial ou de imagem, guia de solicitação de procedimento, relatório de alta/internação ou atestado médico.
Extração de dados: um modelo de linguagem extrai as informações essenciais, como identificação do paciente, dados do profissional e CRM, hipótese diagnóstica (CID-10), medicamentos, dosagens, urgência e exames solicitados.
Avaliação: o agente verifica a consistência dos dados, procura possíveis conflitos e calcula um score de confiabilidade da extração.
Roteamento: com base nisso, encaminha o documento ao destino correto: fila de urgência médica, auditoria de autorizações, farmácia hospitalar, prontuário eletrônico do paciente ou fila de revisão humana, quando há ambiguidade.
Armazenamento: o resultado e o histórico de triagem ficam disponíveis em JSON estruturado, e os documentos são guardados no OCI Object Storage, organizados por status.
Funcionalidades do MVP
Recebimento de documentos clínicos em texto, PDF ou imagem.
Classificação automática do tipo de documento com modelo de linguagem.
Extração de dados clínicos essenciais em JSON estruturado e validado.
Grafo de decisão condicional que roteia cada documento para o destino correto.
Score de confiança da extração e detecção de urgências médicas.
Tratamento de casos ambíguos: documentos com dados ilegíveis, faltantes ou inconsistentes, ou com baixa confiança, são sinalizados para revisão humana (Human-in-the-Loop).
Armazenamento no OCI Object Storage (camada Always Free), com os documentos organizados por status, por exemplo recebidos, processados e auditoria humana.
Interface de triagem para enviar documentos e visualizar o resultado do fluxo.
Três cenários de demonstração: fluxo padrão aprovado, caso de urgência médica e caso ambíguo direcionado para auditoria humana.
Fluxo de decisão e regras de roteamento
🔴 ATENÇÃO: esta seção é uma proposta inicial e ainda será revisada com a equipe.

O fluxo é modelado como um grafo de decisão com etapas condicionais (LangGraph). Depois de classificar o documento, extrair os dados e avaliar a confiança, o agente escolhe o destino de acordo com as regras abaixo.

🔴 Destino por tipo de documento (a revisar com a equipe)
| Tipo de documento | Destino | |---|---| | Receituário médico | Farmácia Hospitalar | | Guia de solicitação de procedimento | Auditoria de Autorizações | | Laudo de exame, relatório de alta/internação e atestado médico | Prontuário Eletrônico do Paciente |

🔴 Casos especiais (a revisar com a equipe)
Urgência: documentos com sinais de urgência, como solicitações urgentes ou prescrições de alto risco, vão para a Fila de Urgência Médica, com alerta para a equipe de plantão.
Ambiguidade: documentos com dados ilegíveis, faltantes, inconsistentes ou com baixa confiança vão para a Fila de Revisão Humana.
Limite de confiança: em definição, a ser ajustado durante os testes.
Documento urgente e com baixa confiança ao mesmo tempo: em definição.
Arquitetura e tecnologias
🔴 ATENÇÃO: as tecnologias abaixo ainda serão confirmadas com a equipe.

| Camada | Tecnologia | |---|---| | Fluxo do agente (grafo de decisão) | LangGraph (Python) | | Modelo de linguagem (classificação e extração) | Cohere | | Interface de triagem | Streamlit | | Armazenamento | OCI Object Storage (camada Always Free) | | Validação dos dados extraídos | 🔴 Pydantic (sugerido pelo desafio, a confirmar) |

Como as peças se conectam
O usuário envia o documento pela interface em Streamlit.
O grafo em LangGraph conduz o documento pelas etapas, chamando o modelo da Cohere para classificar e extrair os dados.
O agente decide o destino do documento, de acordo com as regras de roteamento.
O resultado em JSON e o documento são salvos no OCI Object Storage, organizados por status.
🔴 A verificar: se o modelo da Cohere escolhido lê imagens e PDFs digitalizados, ou se será preciso extrair o texto antes de enviá-lo ao modelo.

Armazenamento no OCI
Os documentos e os resultados da triagem são guardados no OCI Object Storage, usando exclusivamente recursos da camada Always Free da Oracle.

Os arquivos ficam em buckets organizados por status, o que separa o que acabou de chegar, o que já foi processado e o que depende de revisão humana. Por exemplo:

recebidos/: documentos recém-enviados
processados/: documentos já triados e o resultado em JSON, com os casos urgentes separados (por exemplo, processados/urgentes/)
auditoria_humana/: documentos que aguardam revisão de um auditor
Para cada documento, o histórico de triagem fica registrado em JSON estruturado, com a classificação, os dados extraídos e a decisão de roteamento.

Cenários de demonstração
A demonstração do projeto cobrirá três casos de teste:

Fluxo padrão aprovado: um documento legível e completo, com alta confiança na extração. O agente classifica, extrai os dados e o encaminha automaticamente ao destino correto, sem intervenção humana.
Urgência médica: um documento com achado crítico, como um laudo de tomografia de tórax compatível com tromboembolismo pulmonar agudo (exemplo do desafio). O agente identifica a urgência, encaminha para a Fila de Urgência Médica e gera um alerta para o plantão.
Caso ambíguo: um documento com dados ilegíveis, faltantes ou inconsistentes. O agente sinaliza a baixa confiança e o direciona para a Fila de Revisão Humana (auditoria humana).
🔴 Documentos de teste: em definição.

Equipe
🔴 ATENÇÃO: funções e integrantes ainda serão confirmados com a equipe.

| Integrante | Função | |---|---| | Samuel Piovani | A definir | | Leonardo Lacerda | A definir | | Marcel Fagundes Souza | A definir |

🔴 A equipe tem 5 integrantes, e os demais nomes ainda serão incluídos.

Links
Repositório no GitHub: https://github.com/No-Country-simulation/G10---BRASIL---EQUIPO-24
