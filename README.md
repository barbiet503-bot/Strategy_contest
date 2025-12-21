# ContestMikhailBot

ContestMikhailBot is a contest-focused Python trading bot for
Manifold Markets. It trades exclusively in markets created by
**MikhailTal** and is designed with an emphasis on stability,
discipline, and clearly explainable trading decisions.

The bot was created specifically to meet the requirements and
evaluation style of the Manifold Featured Contest.


## Contest Compliance

- Trades exclusively in markets created by `MikhailTal`
- Uses a dedicated contest username
- Operates only on Manifold’s play-money system
- Fully open-source and easy to inspect


## Key Features

- Strict creator-only market filtering
- Edge-based comparison between internal estimates and market prices
- Light momentum confirmation to avoid fighting strong trends
- Basic liquidity checks before placing trades
- Conservative, capped position sizing
- Per-market cooldowns to prevent overtrading
- Protection against duplicate and pending trades
- Clean CSV trade logs for easy review and analysis


## Strategy Overview

Each market is evaluated using simple, transparent heuristics to form an
internal probability estimate, which is then compared to the current
market probability.

A trade is placed only when:
- The estimated edge crosses a conservative threshold
- Market activity is sufficient for reliable execution
- Recent price movement does not strongly contradict the trade
- The market is not currently in a cooldown period

This approach intentionally favors fewer, higher-confidence trades that
are easier to reason about and evaluate.


## Risk Philosophy

The bot prioritizes:
- Capital preservation over trade volume
- Disciplined entries over aggressive exposure
- Clear reasoning over black-box optimization

All position sizes are capped and edge-dependent, ensuring that no single
market can dominate overall performance.


## How This Differs from `manifoldbot`

- Hard restriction to a single creator (contest enforcement)
- Additional safeguards such as cooldowns and duplicate-trade protection
- More conservative defaults aimed at consistency and clarity
- Execution logic designed to safely handle pending and delayed orders


## Running the Bot

### 1. Set API key

```bash
export MANIFOLD_API_KEY=your_key_here


Verify the key is set:
	
Copy code
Bash
echo $MANIFOLD_API_KEY

2. Run the bot
Copy code

Bash
python bot.py


Trades are recorded in trades.csv, including timestamps, market IDs, order status, and trade rationale.

The bot maintains local state to avoid re-entering the same market after a restart.

Design Intent

The strategy intentionally avoids forcing trades. Periods with no valid trades are expected behavior when market prices do not offer a meaningful edge. This is treated as a feature, not a failure.

This design reflects the observation that MikhailTal markets often resolve quickly and reward disciplined entry more than frequent trading.

