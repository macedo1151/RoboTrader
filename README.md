# RoboTrader — WIN e WDO (B3)

Robô de day trade para **mini índice (WIN)** e **mini dólar (WDO)**, em Python,
com execução via **MetaTrader 5**.

> ⚠️ Projeto em fase 1: **backtest e simulação**. As estratégias incluídas são
> exemplos para validar a estrutura, não sistemas lucrativos. Opere dinheiro
> real só depois de backtest com dados reais, custos reais e semanas em conta demo.

## Operação em simulado (estado atual)

Quatro entradas no WIN, em dois **grupos de carteira** (o WDO está desligado). Nenhuma
ordem é enviada: `mode = "paper"` simula as execuções com o mesmo modelo do backtest.

| Grupo | Entradas | Saída |
|---|---|---|
| `principal` | `WIN` (S&P noturno, `external_gap`) + `WIN_REV` (reversão do dia anterior, candidata) | horário + stop de emergência 10 ATR |
| `trailing` | `WIN_TR` + `WIN_REV_TR` (as mesmas estratégias) | idem + trailing de 4 ATR |

Cada grupo opera como **posição líquida** (`[[portfolio]]` em `config/settings.toml`,
`robotrader/portfolio.py`): se uma estratégia compra e a outra vende no mesmo dia, nenhuma
entra; se concordam, cada uma usa `agree_multiplier` (2x) contratos. O grupo `trailing`
existe para comparar, no simulado, se o trailing melhora o resultado daqui para frente.
O modo `mt5` recusa duas estratégias no mesmo contrato e não suporta trailing.

Backtest 2023–2026 dos grupos: `research/portfolio_check.py`. Regras de risco testadas
(inclusive alvo fixo de 2.000 pts e stop fixo de 500 pts): `research/risk_lab.py`, que
também gera os dados do dashboard.

1. Abra o MetaTrader 5 da Genial e faça login (antes das 9:00).
2. Dê dois cliques em **`iniciar_simulado.bat`** e deixe a janela aberta. Ele mostra a
   decisão do dia e roda o robô. O computador não pode hibernar durante o pregão.
3. Às 9:05 o robô decide: compra, vende ou não opera. A saída é após as 13:00
   (ou no stop de emergência de 10 ATR, ou na zeragem das 17:50).
4. **`relatorio_simulado.bat`** mostra as operações simuladas e o resultado acumulado.

Arquivos: operações em `logs/paper/paper_trades.csv`, posição aberta em
`logs/paper/paper_state.json` (o robô retoma após reinício), log diário em `logs/`.
Se o MT5 cair, o robô tenta reconectar sozinho.

Comandos úteis:

```bash
.venv-mt5\Scripts\python -m robotrader signal         # decisão do dia (gap do S&P x filtro)
.venv-mt5\Scripts\python -m robotrader live           # robô (simulado)
.venv\Scripts\python -m robotrader paper-report       # resultado do simulado
```

O que esperar (backtest 2023–2026): ~120 operações/ano (cerca de 1 a cada 2 pregões),
~55% de acerto, ~R$ 24 líquidos por operação com 1 contrato, meses negativos são normais.
Compare o simulado com isso por **pelo menos 2–3 meses** antes de pensar em dinheiro real.

## Estrutura

```
config/settings.toml      parâmetros (horários, risco, stops, estratégia por ativo)
robotrader/
  contracts.py            tick, valor do ponto, vencimentos e contrato vigente
  data.py                 leitura de CSV (padrão ou exportado do MT5), dados sintéticos
  strategies/             estratégias (sinal +1/-1/0 por barra fechada)
    sma_cross.py          cruzamento de médias
    opening_range.py      rompimento da faixa de abertura
  risk.py                 horário, perda diária, nº de operações, perdas seguidas
  execution.py            stop/alvo, slippage e custos (mesmo modelo no backtest e no simulado)
  backtest.py             motor barra a barra
  metrics.py              resultado, acerto, fator de lucro, drawdown, Sharpe...
  broker/paper.py         corretora simulada + replay de histórico
  broker/mt5.py           conector MetaTrader 5 (dados e ordens)
  live.py                 loop do robô ao vivo
tests/                    testes automatizados (pytest)
```

## Instalação

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

O pacote `MetaTrader5` **ainda não suporta Python 3.15**. Por isso há dois ambientes:

| Ambiente | Python | Para quê |
|---|---|---|
| `.venv` | 3.15 | backtest, replay, testes |
| `.venv-mt5` | 3.13 | tudo acima **+** conexão com o MetaTrader 5 |

```bash
py -3.13 -m venv .venv-mt5
.venv-mt5\Scripts\python -m pip install -r requirements-mt5.txt
```

### Conectando ao MetaTrader 5

1. Instale o MT5 **da sua corretora** (precisa ter acesso a B3) e abra uma **conta demo**.
2. Abra o terminal e faça login.
3. Em *Ferramentas > Opções > Gráficos*, aumente **Máx. barras no gráfico** (ex.: 500000).
4. Em *Ferramentas > Opções > Expert Advisors*, marque **Permitir negociação algorítmica**
   (necessário só para enviar ordens).
5. Teste e baixe o histórico:

```bash
.venv-mt5\Scripts\python -m robotrader check
.venv-mt5\Scripts\python -m robotrader download --days 365
.venv-mt5\Scripts\python -m robotrader backtest --instrument WIN --csv data/WIN_M5.csv
```

`check` mostra a conta, se é demo/real, e se os símbolos `WIN$N`/`WDO$N` e o contrato
vigente existem na corretora (com sugestões, se os nomes forem diferentes).
`download` salva `data/WIN_M5.csv` e `data/WDO_M5.csv`. Use `--timeframe M1` para
barras de 1 minuto. Se vier menos histórico que o pedido, abra o gráfico do símbolo
no MT5 e role para trás, para forçar a corretora a enviar mais dados.

## Uso

```bash
# backtest com dados sintéticos (só testa o código)
.venv\Scripts\python -m robotrader backtest --synthetic

# backtest com dados reais exportados do MT5 (Ctrl+U > Barras > Exportar)
.venv\Scripts\python -m robotrader backtest --instrument WIN --csv data/WIN_M5.csv

# roda o loop ao vivo sobre o histórico, com corretora simulada
.venv\Scripts\python -m robotrader replay --instrument WDO --csv data/WDO_M5.csv

# contrato vigente
.venv\Scripts\python -m robotrader contracts

# ao vivo (requer MT5 aberto e logado). mode="paper" em config = só simula.
.venv\Scripts\python -m robotrader live
.venv\Scripts\python -m robotrader live --confirm   # mode="mt5": envia ordens

# testes
.venv\Scripts\python -m pytest
```

## Pesquisa de estratégias (walk-forward)

```bash
.venv\Scripts\python research\explore.py        # comportamento intradiário de WIN/WDO
.venv\Scripts\python research\run_research.py   # walk-forward de todas as famílias
```

O walk-forward otimiza os parâmetros em 120 pregões e mede o resultado nos 21
pregões **seguintes**, que o otimizador não viu. A janela anda de mês em mês e só os
resultados fora da amostra (OOS) contam. Detalhes por estratégia ficam em
`reports/research/` (janelas, parâmetros escolhidos, operações OOS, top 30 do ano).

Como ler o resumo:
- **OOS líquido / fator de lucro**: desempenho realista, com custos e slippage.
- **t**: estatística t do resultado diário OOS. Abaixo de ~2, não dá para distinguir de sorte.
- **% combinações lucrativas**: se só poucas combinações ganham, a "estratégia" é ruído.
- **Melhor no ano todo**: o que um otimizador ingênuo mostraria — quase sempre ilusório.

Estratégias disponíveis: `sma_cross`, `donchian_breakout`, `opening_range_breakout`
(modos `breakout` e `fade`) e `mean_reversion` (âncoras `sma` e `vwap`). Todas aceitam
`entry_from`/`entry_until` ("HH:MM"). Stops podem ser fixos (`stop_points`) ou
proporcionais à volatilidade (`stop_atr`, `target_atr`, múltiplos do ATR).

### Fluxo de ordens (agressão)

```bash
.venv-mt5\Scripts\python -m robotrader download-flow --instrument WDO --days 400
.venv-mt5\Scripts\python -m robotrader download-flow --instrument WIN --by-contract --days 400
.venv\Scripts\python research\flow_analysis.py      # o fluxo antecipa o preço?
.venv\Scripts\python research\flow_economics.py     # o efeito paga os custos?
```

Baixa os negócios do MT5 dia a dia (retomável) e salva em `data/flow/` barras de 1 minuto
com volume agredido de compra (`buy_vol`), venda (`sell_vol`), neutro e `delta`.
O WIN não tem ticks na série contínua: use `--by-contract` (contrato vigente de cada dia).
A Genial guarda ~1 ano de ticks do `WDO$N`, mas só os contratos de WIN ainda listados
(~2,5 meses).

### Mercados externos: S&P noturno x abertura do WIN (pista mais promissora)

```bash
.venv-mt5\Scripts\python research\download_external.py   # WSP, DI1, DOL... -> data/external/
.venv\Scripts\python research\external_analysis.py        # lead-lag e efeito na abertura
.venv\Scripts\python research\gap_research.py             # validação completa da estratégia
```

Estratégia `external_gap`: o micro S&P da B3 (WSP) abre às 9:00 junto com o WIN, e o gap
dele mede o movimento noturno do S&P. Quando esse gap é maior que a mediana dos 120
pregões anteriores, o robô opera **contra** ele no WIN (S&P subiu -> vende), entra às 9:05
e sai num horário fixo (ex.: 13:00).

Resultado 2023–2026 (1 contrato, custos e 1 tick de slippage): +R$ 10,6 mil, fator de
lucro 1,33, positivo nos 4 anos e nos dois lados; 63/63 variações lucrativas; walk-forward
+R$ 6,1 mil; permutação p = 0,003; aguenta até ~5 ticks de slippage por lado.
**Ressalva:** o efeito foi descoberto nesses mesmos dados. A validação definitiva é
operar em simulado daqui para frente.

## Regras de execução e segurança

- O sinal de uma barra só é executado **depois que ela fecha** (sem olhar o futuro).
- Stop e alvo em pontos; se ambos cabem na mesma barra, o backtest assume o **stop**.
- Slippage e custos por contrato aplicados em toda operação.
- Zeragem obrigatória no horário `flatten`; sem entradas fora de `start`–`last_entry`.
- Para no dia ao atingir perda máxima, nº máximo de operações ou perdas seguidas.
  Os limites são recalculados a partir das operações na corretora, então valem mesmo
  após reiniciar o robô.
- **Botão de pânico:** crie um arquivo chamado `STOP` na raiz do projeto — o robô
  zera as posições e não abre novas.
- No MT5, o robô **recusa conta real** enquanto `allow_real_account = false`.
- Senhas nunca ficam no código: use o terminal MT5 já logado, ou as variáveis de
  ambiente `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`.

## Pontos a conferir antes de operar

- Horário de negociação vigente da B3 para WIN/WDO (muda em alguns períodos).
- Custos reais da sua corretora (`cost_per_contract`).
- Nomes dos símbolos na sua corretora (`WIN$N`, `WDO$N`, contrato atual).
- Datas de rolagem: o cálculo do contrato vigente ignora feriados.

## Próximos passos

1. Obter histórico real (M1/M5) de WIN e WDO pelo MT5 da corretora.
2. Backtest das estratégias com dados reais; otimização com validação fora da amostra
   (walk-forward) para evitar sobreajuste.
3. Desenvolver estratégias melhores (VWAP, volume, filtros de tendência/volatilidade).
4. Rodar em conta demo pelo MT5 por algumas semanas e comparar com o backtest.
