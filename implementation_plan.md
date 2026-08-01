# Plano de Implementação: tgr01-trading-llmv2

Este é o planejamento estrutural para a criação real do repositório da V2, focando em robustez, workers desacoplados, e isolamento do modelo mental do LLM.

## User Review Required
> [!IMPORTANT]
> Aprovação do Cronograma: O desenvolvimento será dividido em 5 Fases incrementais. Não codaremos os agentes de IA até a Fase 3. Isso garante que a fundação matemática e de dados esteja sólida antes de gastar tokens. Valide se concorda com a ordem.

## Open Questions
> [!WARNING]
> 1. **Banco de Dados Local**: Para o armazenamento de Klines (preços históricos) e log de operações do Risk Manager, você prefere usar o **SQLite** (basta um arquivo local `.db`, ótimo para simplicidade inicial) ou quer partir direto para o **PostgreSQL** via Docker?
> 2. **Banco Vetorial Local**: Para armazenar e buscar as notícias, recomendo o **ChromaDB** local (não precisa de docker, roda in-memory/no disco e é leve) ao invés de soluções complexas como Pinecone ou Qdrant. Concorda?

## Proposed Changes

### Estrutura Base (Backend e Frontend)
Criação dos esqueletos dos projetos.

#### [NEW] [backend/main.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/main.py)
#### [NEW] [backend/requirements.txt](file:///d:/Projetos/tgr01-trading-llmv2/backend/requirements.txt)
#### [NEW] [frontend/package.json](file:///d:/Projetos/tgr01-trading-llmv2/frontend/package.json)

### Componente: Data Workers (Ingestão Desacoplada)
Processos isolados que rodam de forma contínua para preencher o banco de dados.

#### [NEW] [backend/data/news_worker.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/data/news_worker.py)
#### [NEW] [backend/data/price_worker.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/data/price_worker.py)

### Componente: Core & Features (Matemática Determinística)
Onde as lógicas de negócio e cálculo de TA-Lib e Pandas vivem.

#### [NEW] [backend/features/indicators.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/features/indicators.py)
#### [NEW] [backend/features/payload_builder.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/features/payload_builder.py)

### Componente: Inteligência (LLM Agents)
Os agentes com seus prompts e esquemas Pydantic rígidos.

#### [NEW] [backend/agents/math_agent.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/agents/math_agent.py)
#### [NEW] [backend/agents/analyst_agent.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/agents/analyst_agent.py)
#### [NEW] [backend/agents/cio_agent.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/agents/cio_agent.py)
#### [NEW] [backend/agents/contracts.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/agents/contracts.py)

### Componente: Gestão de Risco e Execução
A barreira de contenção que decide as ordens reais.

#### [NEW] [backend/risk/risk_manager.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/risk/risk_manager.py)
#### [NEW] [backend/execution/exchange_gateway.py](file:///d:/Projetos/tgr01-trading-llmv2/backend/execution/exchange_gateway.py)

## Cronograma (Fases de Implementação)

> [!IMPORTANT]
> **FONTE DE VERDADE DO MAPA DE FASES.** Este cronograma é o mapa de fases **canônico** do projeto. Qualquer outro arquivo (incluindo `project_super_summary.md`, que usa uma numeração histórica/narrativa diferente datada de 2026-05-22) está subordinado a este. Se houver conflito sobre "o que é a Fase X", vale este documento.
>
> **Status atual (atualizado em 2026-08-01):** Fases 1 a 6 concluídas dentro do escopo de pesquisa e paper trading. A aceitação final cobre PostgreSQL real, reconciliação de posição legada auditável, testes concorrentes, backups restauráveis, RAG híbrido externo e interfaces operacionais. Execução real continua deliberadamente fora do escopo e não está implementada.

* **Fase 1: Infraestrutura e Dados (Sem LLM)**
  * Configurar Banco SQLite local e ChromaDB para os Embeddings de notícias.
  * Implementar `price_worker.py` (Mock ou Exchange Real - apenas leitura).
  * Implementar `news_worker.py` (Mock inicial de notícias fictícias para testar o fluxo de RAG em Push).
* **Fase 2: Motor Matemático (Sem LLM)**
  * Criar as lógicas do TA-Lib (MACD, RSI, etc).
  * Criar a classe do `Risk Manager` (Stop Loss, Fractional Kelly).
* **Fase 3: Os Agentes Isolados (Com LLM)**
  * Implementar as classes de Agente usando `groq` ou `OpenRouter`.
  * Integrar com os Contratos Pydantic.
* **Fase 4: Integração do Pipeline Backend (O CIO)**
  * Unir tudo no `main.py` com FastAPI. O CIO ouve os outros agentes e joga pro Risk Manager.
* **Fase 5: Frontend e Testes Red Team**
  * Dashboard de auditoria e status de logs (Caixa de Vidro).
* **Fase 6: Fundação Operacional e Pesquisa Segura**
  * **Concorrência (PostgreSQL) — concluída**: caminho ativo PostgreSQL-only, SQLAlchemy Core, pool real, banco pytest isolado e migração SQLite somente leitura.
  * **Execução Paper Auditável — concluída**: taxas, slippage, custo médio, PnL e invariantes de capital persistidos transacionalmente.
  * **Reconciliação Legada — concluída**: replay determinístico das ordens históricas, fechamento exato com a carteira, trilha de auditoria e rejeição de estados ambíguos.
  * **RAG Híbrido — concluído**: busca densa/lexical em paridade, reranker CUDA e integração somente diagnóstica com filtros contra prompt injection e segredos.
  * **Execução Real — fora do escopo atual**: nenhum endpoint privado ou envio de ordem foi implementado. Qualquer proposta futura exige nova arquitetura, revisão independente e autorização explícita.
  * **Controle de Custos (Token Economics) — concluído no escopo atual**: preflight e safe mode evitam chamadas LLM quando dados de mercado estão ausentes ou stale; cenários direcionais ainda são avaliados pelo LLM e pelo Risk Manager.
  * **Infra Docker — concluída para operação local**: PostgreSQL do trading e RAG híbrido rodam em stacks isoladas; a separação preserva falha independente e integração somente leitura.
  * **TUI/Electron — concluída para operação local**: ambas usam a mesma allowlist de comandos e não duplicam regras de trading.

## Verification Plan

### Automated Tests
* Rodar testes unitários no `Risk Manager` passando um JSON onde o CIO recomenda comprar 100% da banca com `system_reliability = 0.2`. O teste DEVE esperar uma ação de `HOLD`.
* Simular injeção de prompt no `news_worker` para ver se o validador do Pydantic quebra silenciosamente ou trava a pipeline.

### Manual Verification
* Subir o backend e os workers locais. Injetar uma notícia artificial no DB de teste ("O CEO do Mercado Bitcoin foi preso") e assistir o fluxo completo até o CIO soltar um aviso de Black Swan Alert, travando o Mock Execution.
