# Session Handoff - 2026-09-17

Documento para o proximo agente continuar o trabalho sem reconstruir contexto.
Leia `AGENTS.md` na raiz antes de commitar: o protocolo de revisao e obrigatorio.
Leia tambem `.opencode/skills/agent-delegate/SKILL.md` antes de delegar a
qualquer agente, e `.opencode/skills/codex-delegate/SKILL.md` antes de usar o
Codex.

Nota de encoding: escreva arquivos em ASCII puro. O PowerShell 5.1 e o
`Out-File`/`Set-Content -Encoding utf8` gravam BOM e corrompem acentos e o
caractere de bullet; isso ja quebrou uma anexacao de arquivo e uma revisao.

## Estado atual do repositorio

- **Fotografia no inicio desta sessao de handoff:** branch `main`, working tree
  limpo, sem commits pendentes de push em relacao a `origin/main`. HEAD `1677a68`.
  Nenhum worktree adicional. Nenhum stash.
- **Nao confie nisso como estado atual.** Este documento sera commitado depois, e
  qualquer commit muda a contagem. Confirme voce mesmo:
  `git status --short` e `git log --oneline origin/main..HEAD`.
- `git push origin main` **funcionou** neste ambiente nesta sessao (verificado com
  `git push --dry-run` e depois com um push real). O handoff anterior dizia que
  estava bloqueado por autenticacao; isso deixou de ser verdade. Isso e um fato
  observado, **nao** uma autorizacao permanente: se o push falhar, trate como
  bloqueio e reporte ao operador em vez de tentar contornar.

Estado do ambiente verificado nesta sessao:

- PostgreSQL (`tgr01-postgres`) saudavel.
- **Backend: 438 testes passando** (execucao completa, nao apenas coleta).
  `& ".\.venv\Scripts\python.exe" -m pytest backend\tests -q`
- **Desktop: 35 testes passando, build Vite limpo e Electron smoke exit 0.**
  `cd desktop; npm test; npm run build; $env:ELECTRON_DISABLE_SANDBOX="1"; npm run test:electron`
  O exit code importa: verifique-o, e nao apenas a saida impressa.
- **Atencao ao interpretador.** Use SEMPRE `& ".\.venv\Scripts\python.exe"`. O
  `py -3.11` global **nao** tem as dependencias do projeto (`import pytest`
  falha). O README foi corrigido nesta sessao por causa disso; nao reintroduza
  `py -3.11`.
- O Codex CLI em sandbox `read-only` costuma reportar que nao conseguiu rodar
  testes, que o Ollama nao existe ou que um diretorio nao pode ser criado. Isso
  e limitacao do sandbox dele, nao do ambiente. Rode voce mesmo e cite a
  contagem real.

## O que foi concluido nesta sessao

1. **Skill de delegacao** (`.opencode/skills/agent-delegate/SKILL.md`). Documenta
   selecao de modelo, sessoes, worktrees, quota e o portao pre-commit. Passou por
   dupla revisao e por um Codex pos-commit que gerou correcoes reais.

2. **Bug real de console corrigido** (`e46dce6`). A UI Electron **ficava em
   branco com dados reais**: `snapshot.technical.volatility_atr` e um objeto
   `{value, status}` no payload vivo, mas era renderizado como filho React, o que
   dispara React error #31. O smoke test e o fixture usavam `technical: {}` e por
   isso nunca pegaram. Corrigido com `desktop/src/indicators.mjs`
   (`atrValue`/`atrStatus`), que aceitam as duas formas e devolvem `null` (nunca
   `0`) para dado ausente. `atrStatus` suprime status benigno quando o valor e
   inutilizavel.

3. **README corrigido e com capturas reais** (`f6d88ad`). Contagens de teste
   (340 -> 438, 6 -> 35), modelos (glm-5.2 -> kimi-k2.7-code/glm-5.3), todos os
   `py -3.11` -> venv, secao nova de diagnostico edge/custo, e duas imagens
   capturadas do app real contra PostgreSQL vivo.

4. **Notas de campo gravadas na skill** (`1677a68`): comportamento observado por
   modelo, o fato de que um revisor "read-only" nao e read-only de verdade, e a
   licao de que fixture sintetico esconde bug que dado vivo revela.

## Regras de operacao (as que o operador passou explicitamente)

Estas sao cumulativas com `AGENTS.md` e tem prioridade operacional.

### Agentes: 5 e TETO, nao meta

Pode usar 0, 1, 2, 3, 4 ou no maximo 5 agentes, conforme risco e beneficio real
de paralelismo. **Zero e uma resposta valida.** Nao monte 5 agentes por padrao.

Escada: trivial = 0. Bug pequeno/medio = 0 ou 1. Bug complexo = 1 investiga, outro
implementa ou revisa. Mudanca importante = 1 implementador + 1 revisor
independente. Critico = varios investigam **hipoteses diferentes**, mas **um
unico dono da implementacao** de cada area; o resto revisa, projeta teste ou faz
red-team.

### Modelos: allowlist e vetos

**Provider padrao e Ollama Cloud, nao OpenCode Go.** Ha 2 assinaturas OpenCode
Go e elas precisam durar o mes. Go fica reservado para o que so existe la e para
o caso genuinamente dificil.

| Papel | Modelo |
|---|---|
| Workhorse implementacao | `ollama-cloud/glm-5.3-flash` |
| Coding agentic | `ollama-cloud/kimi-k2.7-code` |
| Especialista hard (raro) | `ollama-cloud/glm-5.3` (~100-300 chamadas OK) |
| Reasoning (nao rotineiro) | `opencode-go/gpt-5.6-luna --variant high` |
| Auditoria de risco/seguranca | `opencode/muse-spark-1.3-contributor-free` |
| Red-team A (obrigatorio) | `ollama-cloud/glm-5.3-flash` |
| Red-team B (obrigatorio) | `opencode/union-alpha` |
| Pesquisa / leitura | voce mesmo + subagente `explore` |

**VETADOS - nunca delegue a estes:**

- **`kimi-k3`** (qualquer provider). Ganho de inteligencia quase nulo pelo preco;
  uma tarefa consome ~30% da janela de 5 horas. Decisao explicita do operador.
- **`deepseek-v4-pro`** (qualquer provider). Inferior ao `deepseek-v4.1-flash`.
- **Muse Spark nao pode ser orquestrador nem tocar dado sensivel.** Ele treina
  nos prompts (nao e ZDR). Use apenas em codigo que ja e publico. Nunca passe
  `.env`, tokens, chaves, `DATABASE_URL` ou `backend/data_exports/` (que esta
  fora do Git e nao e publico). Ele foi validado como auditor e achou bugs reais,
  mas a fronteira de dados e obrigatoria.

**Nao faca:** usar OpenCode Go para grep, leitura de arquivo, exploracao ou
execucao mecanica de teste. Isso queima quota a toa.

### Effort / reasoning

- OpenCode: `--variant <valor>`. Os valores sao **especificos do modelo**; nao
  existe enum universal. Consulte `opencode models --verbose` antes.
- `union-alpha` e `kimi-k2.7-code` **nao expoem variantes**: nao passe `--variant`.
- Valor invalido pode ser aceito **em silencio**. Nunca assuma que pegou porque
  nao deu erro.
- Nao aplique override de effort cegamente a modelo novo: pode devolver corpo
  vazio. Meca antes.

### Sessoes

OpenCode cria e retoma assim (o id sai no proprio JSON):

```powershell
$out = opencode run "<prompt>" -m ollama-cloud/glm-5.3-flash --variant low --format json 2>&1
$sid = [regex]::Match(($out -join "`n"), 'ses_[A-Za-z0-9]+').Value
opencode run "<follow-up>" -s $sid -m ollama-cloud/glm-5.3-flash --variant low --format json
opencode export $sid | Out-String   # le a sessao; traz "modelID"/"providerID"
```

**Armadilha verificada:** com um prompt longo multi-linha passado como argumento,
`-m <provider>/<model>` pode ser **descartado em silencio** e o OpenCode roda no
modelo padrao. Ponha as instrucoes longas em arquivo e passe com `-f`, e confirme
o modelo que de fato rodou via `opencode export` antes de confiar na resposta.

### Duplo red-team pre-commit (portao absoluto)

Antes de QUALQUER commit, sem excecao:

1. `ollama-cloud/glm-5.3-flash` revisa o diff exato que sera commitado.
2. `opencode/union-alpha` revisa o MESMO diff, **independentemente**. Nao mostre
   a analise do primeiro ao segundo antes de ele concluir.

Vale mesmo que outro agente ja tenha revisado, que voce ja tenha revisado, que os
testes estejam verdes e que a mudanca seja trivial. Se discordarem, **nao vote**:
reproduza, leia o codigo, rode teste, decida com evidencia. Nunca commite com
achado plausivel nao resolvido.

Isso e separado e cumulativo ao protocolo do `AGENTS.md` (1 subagente validador
antes, gpt-5.6-sol via Codex depois).

Um revisor **nao e read-only de verdade**: um revisor editou o proprio README que
revisava, e outro rodou `git checkout --` e apagou um arquivo nao commitado. Passe
worktree ou sandbox, cheque `git diff` **e** `git diff --cached` depois de cada
revisao, e trate edicao de revisor como mudanca nao revisada.

### Git e isolamento

Antes de delegar tarefa que possa alterar codigo: crie branch exclusiva
(`agent/<slot>/<task>`), crie worktree exclusivo apontando para ela, e de ao
agente somente aquele worktree. Nunca dois agentes no mesmo working tree. Sem
merge direto em `main`; o orquestrador integra. Sem force-push.

Mantenha worktrees **fora** do repositorio, em `D:\tgr01-worktrees\<slot>`, para
nao poluir `git status`:

```powershell
git worktree add -b agent/opencode-1/fix-rsi "D:\tgr01-worktrees\opencode-1" HEAD
git worktree remove "D:\tgr01-worktrees\opencode-1"
git worktree prune
git branch --delete agent/opencode-1/fix-rsi
```

### Commit

Mensagem tecnica, sem nome de IA e sem termo generico. Nunca commite `.env` nem
credenciais. Nunca commite sem a dupla revisao acima.

## Pendencias e proximo passo

### Bloqueado pelo operador

1. **Confirmar a taxa real da conta.** `PAPER_FEE_RATE=0.003` e premissa de
   configuracao, nao medicao. Faltam `MB_CLIENT_ID` e `MB_CLIENT_SECRET` no
   `.env` para rodar `backend/tests/validate_mb_order_dry_run.py`. **Isto vem
   antes de qualquer conclusao sobre edge**, porque determina se o problema de
   custo e real e porque a leitura nao escala linearmente (o slippage minimo
   permanece como piso).
2. **Decisao de alavanca** apos (1): horizonte maior (horas/dias) ou operar
   raramente com o custo no payload. O operador ja sinalizou preferencia por
   horizonte maior depois de confirmar a taxa.

### Nao feito

- **Precisao sobre as particoes** (corrigido apos revisao; o handoff anterior
  dizia "nunca foram tocados", o que era impreciso):
  - `holdout` **selado e nunca avaliado**. Exige `--holdout-approval` com o
    `dataset_id` exato e so deve ser aberto com prompts e regras congelados.
  - `validation` **ja foi usada**: a campanha de 2026-08-10 avaliou 50 snapshots
    estratificados dela (150 chamadas). Veja
    `docs/reports/MULTI_AGENT_HISTORICAL_VALIDATION_2026-08-10.md`.
  - A campanha desta sessao (`EDGE_AND_COST_DIAGNOSTIC_2026-09-16.md`) usou
    **somente `development`**. Nao trate isso como "validation intocada".
- O dataset historico vive fora do Git em `backend/data_exports/`. Nao e publico.

### Achados abertos, NAO corrigidos (nao invente que estao resolvidos)

Um auditor de risco reportou 3 falhas pre-existentes no `risk_manager.py`
(`backend/risk/risk_manager.py`) e eu reproduzi 3 delas:

- `hybrid_confidence == 0.50` **passa** no teste `< 0.50` (com ATR a 20% do preco,
  aprova BUY).
- `technical_context` sem `rsi`/`macd` faz `None` passar por todos os blocklists
  e **aprova BUY/90**.
- `news_context=None` levanta `TypeError` em vez de retornar HOLD (viola
  fail-closed).

**Por que estao abertas.** Elas tocam o Risk Manager. O `AGENTS.md` exige
sign-off explicito do operador para alterar **thresholds e o gate de conviccao**
(`AGENTS.md:53-55`). O `AGENTS.md` tambem manda preferir falhar fechado, entao um
defeito fail-closed e defensivamente relevante **mesmo que nao seja alcancavel
hoje**. Nao descarte por "e so teorico".

**O operador decidiu deixar quieto por ora.** Isso e uma decisao de prioridade,
nao uma declaracao de que nao existem.

**Antes de agir, avalie alcance com evidencia** (nao presuma): o
`payload_builder` constroi `rsi`/`macd` via `calculate_technical_status`
(`backend/features/payload_builder.py:164`), o que sugere que o caso (b) pode nao
ocorrer no caminho vivo. Mas o caminho historico, os scripts de campanha e os
testes passam payloads diferentes. Verifique cada um. Se algum for alcancavel,
trate como bug e leve ao operador com a reproducao; se nenhum for, registre a
evidencia de que e latente, sem apagar o achado.

## Regras nao negociaveis (de `AGENTS.md`)

- Trading real desabilitado. `REAL_TRADING_ENABLED` deve permanecer `false`.
  Nunca adicione endpoint de ordem, cancelamento, transferencia ou saque.
- O Risk Manager deterministico e o unico componente que aprova ordem. Nunca
  enfraqueca, e nunca deixe LLM, agente ou o RAG aprovar/bloquear/dimensionar.
  Nao altere thresholds nem o gate de conviccao sem sign-off explicito.
- Nunca commite `.env` nem credenciais. Nunca cole segredo em prompt.
- Prefira falhar fechado. Bloqueio ou HOLD e resultado valido e seguro.

## Padrao de defeito que se repete - leia antes de mexer na UI

Em muitas iteracoes o red-team encontrou o mesmo padrao: **dado de seguranca
ausente renderizado como valor benigno**. Ja apareceu como exposicao ausente
mostrando `0.00%`, freshness ausente em verde, flag de stale ausente mostrando
`NO`, conviccao nula mostrando `0%`, drawdown desconhecido em verde, limite de
drawdown inventado como 10%, e preco de candle ausente indistinguivel de zero
real.

Regra pratica: em qualquer campo de risco/seguranca, ausente deve render `--` e
tom neutro. Nunca `0`, `NO`, verde ou `NORMAL`. Ao tornar campos `null` para
distinguir ausente de zero, cuidado: `.get(key, 0)` **nao** protege contra chave
presente com valor `None`. Isso ja quebrou a TUI Textual antes.

## Sobre a limpeza de disco

`D:\game-spark` e o workspace de um benchmark separado do operador. **Nao e
residuo e nao deve ser apagado.** Pode haver um agente ativo escrevendo nele.
Nunca delete diretorio fora do repositorio sem confirmar com o operador.

## Como delegar ao Codex (gpt-5.6-sol)

A skill `.opencode/skills/codex-delegate/SKILL.md` tem os detalhes. Pontos que
custam tempo se esquecidos:

- `codex exec` aceita `-s read-only`; `codex exec resume` **nao** aceita, use
  `-c 'sandbox_mode="read-only"'`.
- Sempre passe o effort explicito: `-c 'model_reasoning_effort="medium"'`. Nao
  confie no `~/.codex/config.toml`, que o operador pode mudar.
- Uma sessao aberta no app Codex Desktop trava o arquivo em
  `~/.codex/thread-writer-locks/`, e o `resume` falha com "already has an active
  writer". Nao apague o lock com o app aberto.
- A quota do gpt-5.6-sol tem limite; se estourar, siga so com o subagente e
  retome depois.
