# Session Handoff - 2026-09-16

Documento para o proximo agente continuar o trabalho sem reconstruir contexto.
Leia `AGENTS.md` na raiz antes de commitar: o protocolo de revisao e obrigatorio.

## Estado atual do repositorio

- Branch: `main`, working tree limpo.
- **28 commits locais a frente de `origin/main` (d99055a). Nada foi enviado.**
- O `git push` esta bloqueado por autenticacao: nao ha token em ambiente,
  `~/.git-credentials`, nem chave SSH. O Git Credential Manager pede um dialogo
  grafico que um agente nao consegue responder. **O operador precisa rodar
  `git push origin main` manualmente.**
- Existe a tag local `backup-before-merge` apontando para `d99055a`.

Estado do ambiente verificado no fim da sessao:

- PostgreSQL (`tgr01-postgres`) saudavel, 19h de uptime.
- Workers `price_worker` e `news_worker` rodando; ultimo candle com ~50s de idade.

## O que esta funcionando

| Verificacao | Resultado |
|---|---|
| `pytest backend\tests -q` | 438 passed |
| `npm test` (desktop) | 25 passed |
| `python -m compileall -q backend` | limpo |
| `npm run build` (Vite) | limpo |
| `npm run test:electron` | smoke limpo, zero erros de renderer, sem overflow |

Comandos exatos (o `py -3.11` global nao tem as dependencias):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q
Set-Location .\desktop
npm test
$env:ELECTRON_DISABLE_SANDBOX="1"; npm run test:electron
```

**Atencao a uma confusao recorrente**: o Codex CLI, rodando em sandbox
`read-only`, costuma reportar que nao conseguiu rodar os testes ("`.venv` aponta
para um Python ausente" ou `EPERM` do Vite). Isso e limitacao do sandbox dele,
nao do ambiente. Neste repositorio os comandos acima funcionam. Nao trate um
relatorio de "nao consegui rodar" como evidencia de que a suite falha; rode voce
mesmo e cite a contagem real.

## Trabalho concluido nesta sessao

### 1. Fix do RSI Wilder e do ATR (commit `8de31e5`)

O caminho live usava media movel simples de ganhos/perdas enquanto o dataset de
ML usava outro rolling mean. Agora ambos usam Wilder (1978) com semente SMA.

Dois defeitos reais corrigidos junto:

- O ATR otimizado usava `np.maximum`, que **propaga NaN** onde o pandas ignora.
  Isso deslocava a janela de 14 linhas (regressao do PR #9). Agora usa `np.fmax`.
- O volume profile estourava com `int(NaN)` e derrubava a funcao inteira.

O docstring anterior afirmava "489 falsos OVERBOUGHT e 432 falsos OVERSOLD" sem
fonte registrada. Dois agentes independentes e uma medicao propria no PostgreSQL
convergiram em **515/457**. A medicao virou script reproduzivel:
`backend/tests/audit_rsi_definition.py` (exige PostgreSQL).

### 2. Infraestrutura multi-provider de modelos (`74a35e8`, `7192357`, `9474d82`, `cc5eee3`, `04c5067`)

Cada papel (`news`, `technical`, `decision`) resolve seu proprio provider,
modelo, temperatura, budget de tokens e reasoning effort. Providers registrados:
`ollama` (daemon local), `ollama-cloud` (API direta), `opencode-go`.

Descobertas empiricas que definiram os defaults:

- `reasoning_effort=low` no `glm-5.3`: **11/12 para 12/12** de contratos validos
  e ~7x mais rapido (28s vs 195s). Sem isso o modelo gasta o budget inteiro em
  chain-of-thought e devolve corpo vazio.
- `kimi-k2.7-code` precisa de 8000 tokens (**12/12** vs 10/12 em 5000) e pontua
  melhor **sem** override de effort.
- OpenCode Go exige o header `x-opencode-session`, senao retorna 400.

Defaults atuais (todos no daemon Ollama, para nao gastar quota do Go):

```
news      glm-5.3:cloud          max_tokens 5000  effort low
technical glm-5.3:cloud          max_tokens 5000  effort low
decision  kimi-k2.7-code:cloud   max_tokens 8000  effort (nenhum)
```

**Politica de quota**: OpenCode Go tem teto mensal por modelo. Somente a familia
Qwen 3.7/3.8 e sancionada nesse provider, e uma allowlist com regex ancorado
rejeita `qwen3.7-evil` por exemplo.

### 3. Bug pre-existente de contrato de schema

O campo `evidence_fields` nao tinha `description`, entao os modelos escreviam
**prosa** ("RSI overbought blocking BUY") e o validador rejeitava corretamente.
Isso fazia a pipeline multiagente falhar fechado em analises validas. Corrigido
com descricoes que especificam dot-paths. **O validador nao foi afrouxado.**

Tambem alinhei os contratos: `model_validate(strict=True)` no pos-fallback e
guarda contra `conviction=True` virar `1` silenciosamente.

### 4. Miscalibracao que bloqueava 100% das ordens (`3171cf3`, `1634bef`)

Este foi o achado central. O prompt mandava o modelo usar conviccao **60** para
um setup direcional com contra-evidencia, enquanto o Risk Manager exige **>= 70**
(`risk_manager.py`). O modelo obedecia, entao **toda** proposta direcional era
rejeitada. Medi: 6/6 bloqueadas num smoke.

Corrigi **os prompts**, nao o Risk Manager. O gate e a autoridade dele ficaram
intactos. Os 3 profiles em `backend/agents/prompt_profiles.py` tinham o mesmo
defeito e foram alinhados tambem.

Verificado depois: setup bullish forte aprova BUY/80, bearish aprova SELL/70, e
os 7 casos adversariais (market stale, instrucao hostil, red flag, drawdown, ATR
extremo, news stale) continuam fechando em HOLD.

### 5. Diagnostico de edge versus custo (ver `docs/reports/EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md`)

Campanha de 120 amostras na particao `development`. Depois do fix acima, o Risk
Manager aprova direcionais (antes: 100% bloqueado). Resultado:

- **Margem liquida media negativa em todos os horizontes** (-0.48% a -0.57%).
- **A causa observada e o custo contra o movimento**: o custo configurado e
  0.35%/lado (fee 0.30% + slippage min 0.05%), entao BUY tem hurdle de 0.70%
  round-trip. O movimento mediano (valor absoluto) em 60m e **0.470%**; so 21%
  das janelas atingem o hurdle completo de 0.90%.
- **O MACD que o modelo recebe previu a direcao em 60m com 50.0%** nesta amostra
  — uma moeda justa. Em 15m teve 58.5%, que ainda nao paga o custo.
- 14 das 29 direcionais aprovadas foram **contra** o regime esperado da janela.

**Limitacoes desta medicao — leia antes de generalizar:**

- As 120 linhas representam apenas **24 janelas sobrepostas** (5 ciclos cada),
  nao 120 observacoes independentes.
- Uma **unica particao** (`development`), em fev-jun/2026. Nao e walk-forward.
- Noticias **sinteticas** (`neutral-fresh`); nao valida desempenho com noticia real.
- Portanto o resultado sustenta que *nesta amostra e com estes custos* o edge
  bruto nao cobriu o custo. Nao sustenta uma afirmacao universal de que nenhuma
  mudanca de prompt ou politica pode alterar o resultado.

Este relatorio passou por **4 passadas de revisao adversarial** do gpt-5.6-sol e
foi declarado ACCURATE no final. Todo numero foi recalculado por um validador
independente. Leia-o antes de propor qualquer campanha nova.

### 6. Painel "Cost Reality" no console Electron (`69572df` + 7 correcoes)

O console nao mostrava o custo que domina a decisao. Agora mostra: custo por
lado, os dois hurdles, a razao movimento-tipico/custo (0.67x, em vermelho), o
gate de conviccao, motivos de bloqueio e os papeis de modelo.

Durante a implementacao encontrei uma **divergencia real**: o console publicava
`max_exposure_pct=100` enquanto o runtime usa **80** (`main.py`). Corrigido com
uma constante unica `LIVE_MAX_EXPOSURE_PCT` em `backend/core/market_policy.py`.

**Licao importante**: a primeira versao que escrevi fez essa constante
**configuravel por variavel de ambiente**, o que permitiria enfraquecer um gate
do Risk Manager sem code review. O redteam pegou. Agora e constante de codigo com
um guard de teto revisado. Nao torne thresholds de risco configuraveis por env.

## Padrao de defeito que se repetiu muito

Vale ler antes de mexer na UI. Em **7 iteracoes** o redteam encontrou o mesmo
padrao: **dado de seguranca ausente renderizado como valor benigno**.

Exemplos corrigidos: exposicao ausente mostrava `0.00%`; freshness ausente
mostrava verde; flag de stale ausente mostrava `NO`; conviccao nula mostrava
`0%`; drawdown desconhecido pintado de verde; limite de drawdown **inventado**
como 10%; preco de candle ausente indistinguivel de zero real; credito de
exposicao derivado de preco ausente.

Ao tornar campos `null` para distinguir ausente de zero, **quebrei a TUI
Textual** (`.get(key, 0)` nao protege contra chave presente com valor `None`).
Corrigido em `d9739b9`, `521cdbe`, `d611c00`.

Regra pratica: em qualquer campo de risco/seguranca, ausente deve render `--` e
tom neutro. Nunca `0`, `NO`, verde ou `NORMAL`.

## Pendencias e proximo passo

### Bloqueado pelo operador

1. **`git push origin main`** (28 commits locais).

### Decisao pendente do operador (nao do agente)

O gargalo medido e custo versus horizonte: o custo de 0.70% round-trip e maior
que o movimento tipico do ativo no horizonte de minutos desta amostra. Ajustar
prompt para "melhorar acerto" nao ataca essa distancia — mas isso e conclusao
desta amostra, nao uma prova de que nenhuma politica melhoraria.

A escolha da alavanca exige sign-off. Sao hipoteses a testar, nao fatos
observados:

- Horizonte maior (horas/dias), onde o movimento tipico poderia superar o custo.
- Operar raramente, e dar ao LLM o custo esperado no payload para calibrar.
- **Confirmar a taxa real da conta primeiro** — `PAPER_FEE_RATE=0.003` e premissa
  de configuracao, nao medicao. O cliente autenticado ja le as fees reais da
  exchange (`backend/execution/mb_private_client.py`, usado por
  `backend/tests/validate_mb_order_dry_run.py`).

O experimento pre-registrado (hipotese, escopo, metrica de aceitacao e criterio de
falha) esta na secao "Proximo experimento" do relatorio de diagnostico.

### Nao feito

- A particao `validation` e o `holdout` selado **nunca foram tocados**.
- O dataset historico foi reconstruido (6 meses, manifest `8504c29db1253e2a5d8bdafe`)
  e a particao `development` (117534 candles) foi importada no PostgreSQL. O
  diretorio `backend/data_exports/` esta fora do Git. Cobertura medida das
  particoes: development 74.2%, validation 68.3%, holdout 70.1% — abaixo dos 80%
  que o gate do dataset de ML exige (nao bloqueia a campanha historica, mas esta
  registrado como limitacao).
- TCN permanece arquivado; nada foi feito nele.

## Regras nao negociaveis (de `AGENTS.md`)

- Trading real desabilitado. `REAL_TRADING_ENABLED` deve permanecer `false`.
  Nunca adicionar endpoint de ordem, cancelamento, transferencia ou saque.
- O Risk Manager deterministico e o unico componente que aprova ordem. Nunca
  enfraquecer, e nunca deixar LLM ou RAG aprovar/bloquear/dimensionar.
- Nunca commitar `.env` nem credenciais. Nunca colar segredo em prompt.
- Nao alterar thresholds do Risk Manager sem sign-off explicito do operador.
  Calibracao se corrige no prompt.
- Preferir falhar fechado. Bloqueio ou HOLD e resultado valido e seguro.

## Como delegar ao Codex (gpt-5.6-sol)

O protocolo de commit exige 1 subagente validador antes e o Codex depois. A skill
`.opencode/skills/codex-delegate/SKILL.md` tem os detalhes. Pontos que custam
tempo se esquecidos:

- `codex exec` aceita `-s read-only`; `codex exec resume` **nao** aceita, use
  `-c 'sandbox_mode="read-only"'`.
- Sempre passe o effort explicito: `-c 'model_reasoning_effort="medium"'`.
- Uma sessao aberta no app Codex Desktop trava o arquivo em
  `~/.codex/thread-writer-locks/`, e o `resume` falha com "already has an active
  writer". Nao apague o lock com o app aberto.
- A quota do gpt-5.6-sol tem limite; se estourar, siga so com o subagente e
  retome depois.
