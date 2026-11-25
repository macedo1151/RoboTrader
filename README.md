# RoboTrader

Robô de day trade para **mini índice (WIN)** e **mini dólar (WDO)** da B3, escrito em Python, com backtest realista, pesquisa walk-forward e execução via **MetaTrader 5**.

![Python](https://img.shields.io/badge/Python-3.13%20%7C%203.15-3776AB?logo=python&logoColor=white)
![Plataforma](https://img.shields.io/badge/MetaTrader%205-Windows-0A66C2)
![Modo](https://img.shields.io/badge/modo-simulado%20(paper)-orange)
![Licença](https://img.shields.io/badge/licença-MIT-green)

> ⚠️ **Aviso:** projeto educacional e de pesquisa, hoje em **fase de backtest e simulação**. Nenhuma ordem real é enviada no modo padrão. Day trade envolve risco de perda total do capital. Nada aqui é recomendação de investimento. Opere dinheiro real somente após validar com dados reais, custos reais e semanas em conta demo.

## Destaques

- **Backtest sem olhar o futuro:** o sinal só é executado depois que a barra fecha, com slippage e custos por contrato em toda operação. Se stop e alvo cabem na mesma barra, assume-se o stop.
- **Mesmo modelo de execução em tudo:** backtest, replay e simulado usam as mesmas regras (`execution.py`), então os resultados são comparáveis.
- **Pesquisa com walk-forward:** otimiza em 120 pregões e mede nos 21 seguintes, que o otimizador não viu. Só o resultado fora da amostra conta.
- **Gestão de risco embutida:** horário de entrada, zeragem obrigatória, perda máxima diária, limite de operações e de perdas seguidas, recalculados a partir da corretora (valem após reiniciar).
- **Carteira de estratégias:** grupos com posição líquida, em que sinais opostos se anulam e sinais concordantes aumentam o tamanho.
- **Simulado ao vivo com painel local:** liga e desliga o robô por uma página em `localhost`.
- **Botão de pânico:** criar um arquivo `STOP` na raiz zera as posições e bloqueia novas entradas.

## Estratégia em operação (simulado)

A principal é a **`external_gap`**: o micro S&P da B3 (WSP) abre junto com o WIN, e o gap dele mede o movimento noturno do S&P. Quando o gap é maior que a mediana dos 120 pregões anteriores, o robô opera **contra** ele no WIN, entra às 9:05 e sai em horário fixo.

| Grupo | Entradas | Saída |
|---|---|---|
| `principal` | `WIN` (S&P noturno) + `WIN_REV` (reversão do dia anterior) | horário + stop de emergência de 10 ATR |
| `trailing` | as mesmas estratégias | idem + trailing de 4 ATR |

Backtest 2023–2026 (1 contrato, com custos e 1 tick de slippage): fator de lucro de 1,33, positivo nos 4 anos e nos dois lados, permutação p = 0,003.

> **Ressalva honesta:** o efeito foi descoberto nesses mesmos dados. A validação definitiva é o simulado daqui para frente, por pelo menos 2–3 meses.

## Instalação

Requer Windows (por causa do MT5) e um MetaTrader 5 de corretora com acesso à B3.

```bash
git clone https://github.com/macedo1151/RoboTrader.git
cd RoboTrader

python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

O pacote `MetaTrader5` ainda não suporta Python 3.15, então há dois ambientes:

| Ambiente | Python | Para quê |
|---|---|---|
| `.venv` | 3.15 | backtest, replay, testes |
| `.venv-mt5` | 3.13 | tudo acima, mais a conexão com o MT5 |

```bash
py -3.13 -m venv .venv-mt5
.venv-mt5\Scripts\python -m pip install -r requirements-mt5.txt
```

## Uso rápido

```bash
# teste do código com dados sintéticos
.venv\Scripts\python -m robotrader backtest --synthetic

# conectar ao MT5 e baixar histórico (terminal aberto e logado)
.venv-mt5\Scripts\python -m robotrader check
.venv-mt5\Scripts\python -m robotrader download --days 365

# backtest com dados reais
.venv\Scripts\python -m robotrader backtest --instrument WIN --csv data/WIN_M5.csv

# decisão do dia e robô ao vivo em modo simulado
.venv-mt5\Scripts\python -m robotrader signal
.venv-mt5\Scripts\python -m robotrader live

# resultado do simulado e testes
.venv\Scripts\python -m robotrader paper-report
.venv\Scripts\python -m pytest
```

No Windows há atalhos de duplo clique: `iniciar_simulado.bat`, `relatorio_simulado.bat` e `painel_robo.bat` (painel em `http://localhost:8765`).

## Estrutura

```
config/settings.toml      parâmetros: horários, risco, stops, estratégia por ativo
robotrader/
  strategies/             sma_cross, donchian, opening_range, mean_reversion,
                          external_gap, prev_day_reversal
  backtest.py             motor barra a barra
  execution.py            stop/alvo, slippage e custos
  risk.py                 limites diários e horários
  portfolio.py            grupos de carteira (posição líquida)
  broker/                 corretora simulada (paper) e conector MT5
  live.py                 loop do robô ao vivo
  painel.py               painel local para ligar/desligar
research/                 scripts de pesquisa e validação walk-forward
tests/                    testes automatizados (pytest)
docs/GUIA.md              guia detalhado de operação e pesquisa
```

## Segurança

- Com `mode = "paper"` (padrão), nenhuma ordem é enviada.
- No modo MT5 o robô **recusa conta real** enquanto `allow_real_account = false`, e exige `--confirm` para enviar ordens.
- Senhas nunca ficam no código: use o terminal já logado ou as variáveis `MT5_LOGIN`, `MT5_PASSWORD` e `MT5_SERVER`.

## Documentação

O guia completo, com pesquisa de estratégias, análise de fluxo de ordens, mercados externos e checklist antes de operar, está em [docs/GUIA.md](docs/GUIA.md).

## Licença

Distribuído sob a licença [MIT](LICENSE).
