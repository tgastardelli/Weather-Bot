---
trigger: always_on
description: Regras inegociáveis de segurança de trading e de credenciais
---

# Segurança de Trading — Regras Inegociáveis

## Modo de execução
- **Paper trading é o modo default.** O bot só simula ordens até decisão explícita do usuário.
- Modo live exige **todas** as condições:
  1. Flag explícita de configuração (ex.: `MODE=live` no `.env`) — nunca default;
  2. Limites de risco configurados e ativos: stake máximo por ordem, exposição máxima por mercado, perda diária máxima;
  3. Kill switch funcional (cancela todas as ordens abertas e interrompe o loop de trading);
  4. Verificação de geoblock (`GET https://polymarket.com/api/geoblock`) antes de habilitar ordens.
- Código de ordem live NUNCA roda em testes automatizados; usar mocks/fixtures.

## Credenciais
- `PRIVATE_KEY` (controla fundos reais), API creds (key/secret/passphrase) e endereços de carteira: **somente em `.env`**, que deve estar no `.gitignore` desde o primeiro commit.
- Nunca hardcodar, logar, imprimir ou incluir credenciais em mensagens de erro, commits ou exemplos de código.
- `.env.example` documenta as variáveis com valores vazios/placeholder.
- A chave privada só é necessária para o modo live (Fase 5) — não pedir nem manusear antes disso.

## Comandos e ações
- NUNCA executar automaticamente comandos que movam fundos, criem/cancelem ordens reais, façam approvals on-chain ou transferências (ex.: `setup_trading_approvals()`, `place_*_order`, `transfer_erc20`, `split/merge/redeem`). Sempre exigir aprovação explícita do usuário.
- Heartbeat: se implementarmos sessões com heartbeat do CLOB, a falha de heartbeat cancela ordens abertas — tratar como mecanismo de segurança, não como bug.

## Risco de mercado (aplicável já no paper trading)
- Toda ordem (simulada ou real) passa pelo módulo de risco: valida stake, exposição e perda diária ANTES de ser registrada/enviada.
- Mercados de clima têm **taxa taker de 5%** (`weather_fees`) — o cálculo de edge/PnL DEVE descontar fees; ignorá-las invalida a estratégia.
- Ler as regras de resolução (`description` do mercado) antes de operar: a fonte oficial e a precisão (inteiro vs 1 casa decimal) variam por cidade.
