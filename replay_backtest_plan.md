# Plano: Replay/Backtest Acelerado com Dados Coletados

## Ideia central

O paper trading ao vivo e util para testar infraestrutura: workers, clock,
SQLite, preflight, LLM, Risk Manager e auditoria em tempo real.

Para testar decisao, prompt, Risk Manager, taxa/slippage, PnL e relatorios, o
melhor fluxo e outro: coletar um bloco de dados reais no SQLite e depois rodar
um replay rapido, sem esperar 30 ou 60 segundos entre ciclos.

## Por que isso melhora o projeto

No modo atual, 30 ciclos com intervalo de 30 segundos levam cerca de 15 minutos.
Isso e bom para validar a pipeline viva, mas lento para iterar estrategia.

Com replay acelerado:

- usamos candles e noticias reais ja coletados;
- cada ciclo simula um momento historico do banco;
- a IA so pode ver dados disponiveis ate aquele timestamp;
- o teste roda rapido, sem `sleep`;
- os relatorios conseguem avaliar 5m, 15m, 30m e 60m sem esperar o tempo passar;
- fica mais facil comparar modelos, prompts e regras de risco no mesmo conjunto de dados.

## Regra mais importante

O replay nao pode deixar a IA olhar o futuro.

Isso significa que o payload precisa aceitar um tempo de referencia:

```text
build_agent_payload(as_of_timestamp=T)
```

Quando `as_of_timestamp` existir:

- candles devem ser buscados apenas com `timestamp <= T`;
- noticias devem ser buscadas apenas com `timestamp <= T`;
- data health deve ser calculado em relacao a `T`, nao ao relogio atual;
- portfolio pode ser o estado simulado do replay naquele ciclo;
- avaliacao futura fica fora do payload e so entra nos relatorios depois.

## Separacao de modos

### Live paper trading

Objetivo: testar operacao viva.

Usa:

- workers reais;
- relogio atual;
- preflight estrito;
- sleep real;
- dados chegando agora.

Comando atual:

```powershell
python .\backend\tests\run_paper_trading.py --cycles 30 --sleep 30
```

### Replay paper trading

Objetivo: testar decisao e regras rapidamente.

Usa:

- candles/noticias ja salvos no SQLite;
- uma janela historica;
- ciclos sem sleep;
- payload `as_of_timestamp`;
- mesmos contratos LLM;
- mesmo Risk Manager;
- mesmo executor paper com taxa/slippage/PnL.

Comando futuro sugerido:

```powershell
python .\backend\tests\run_replay_trading.py --from "2026-06-08 00:00" --to "2026-06-08 09:30" --step 60
```

## Plano para a madrugada

Na meia-noite, iniciar workers e deixar coletando dados reais por 9 a 10 horas.

Janela desejada:

```text
00:00 ate 09:00/10:00 America/Sao_Paulo
```

Dados esperados:

- cerca de 540 a 600 candles de 1 minuto;
- noticias coletadas durante a madrugada;
- heartbeats de workers suficientes para validar saude;
- um bloco realista para testar horizontes de 5m, 15m, 30m e 60m.

Antes de comecar:

```powershell
python .\backend\tests\preflight_data_date.py --require-workers
```

Durante a coleta:

- manter `price_worker` rodando;
- manter `news_worker` rodando;
- nao precisa rodar paper trading durante toda a madrugada;
- o objetivo principal e acumular dados limpos.

Depois da coleta:

```powershell
python .\backend\tests\preflight_data_date.py --require-workers
python .\backend\tests\analyze_trade_logs.py --limit 20
```

Depois implementaremos o replay.

## Arquivos que devem existir no futuro

### `backend/features/payload_builder.py`

Adicionar suporte opcional:

```python
build_agent_payload(asset="BTC/BRL", timeframe="1m", as_of_timestamp=None)
```

### `backend/tests/run_replay_trading.py`

Novo runner acelerado.

Responsabilidades:

- selecionar timestamps de candles na janela;
- montar payload como se estivesse naquele minuto;
- consultar LLM;
- passar pelo Risk Manager;
- simular execucao paper;
- gravar `trade_logs`;
- nao usar `sleep`.

### Relatorios

Os relatorios atuais continuam validos:

```powershell
python .\backend\tests\analyze_trade_logs.py --since-id X --limit 50
python .\backend\tests\evaluate_decisions.py --since-id X --horizons 5,15,30,60
python .\backend\tests\analyze_entry_decisions.py --since-id X --horizons 5,15,30,60
```

Melhoria futura: adicionar tolerancia maxima de gap no `evaluate_decisions.py`
para nao comparar uma decisao com candle muito distante quando houver buraco no
historico.

## Cuidados

- Replay e paper trading real-time devem ser separados nos logs.
- O replay nao deve depender de workers vivos.
- O replay nao deve consultar noticias futuras.
- O replay nao deve usar candle futuro dentro do payload.
- A avaliacao futura so pode acontecer depois que a decisao ja foi registrada.
- A LLM continua sendo interpretadora; Risk Manager continua sendo a autoridade.

## Resultado esperado

Depois dessa fase, teremos dois tipos de teste:

```text
Live paper trading -> valida infraestrutura viva.
Replay/backtest    -> valida decisao e regra rapidamente.
```

Isso deve acelerar muito o desenvolvimento, porque poderemos rodar varios
experimentos em cima da mesma madrugada de dados sem esperar horas a cada teste.
