# Does the liquidation bot make money? Parameter sweep results

**What this is:** a Monte Carlo model of the strategy's economics, run over every parameter
I could identify (23 of them). **What it is not:** a backtest on real chain data. No blockchain
data source was reachable from the machine this was run on. Read every number as
"if the world looks like these assumptions, the bot earns about this much".

All figures are USD per year, for one market, after gas and infrastructure costs.
Full tables: [TABLES.md](TABLES.md). Reproduce with `python research/run_sweep.py && python research/render.py`.

## Findings

1. **Competition decides almost everything, and it is the one thing the model cannot know.**
   With identical market and costs, the result swings from about -100 (professional bots
   present) to about +5,600 (few slow rivals).
2. **Market size is the second lever.** Under about 1-2M USD of borrowed funds there are too
   few liquidations to cover even a 10 USD/month RPC plan. The result scales almost linearly with size.
3. **Many small loans beat a few large ones.** Small liquidations are the ones professionals
   leave alone.
4. **Gas barely matters below about 0.50 USD per transaction** and kills the strategy at
   Ethereum-mainnet prices. Use cheap chains only.
5. **Protocol and bot settings hardly matter.** Bonus size, protocol fee, flash-loan fee, DEX
   fee, `min_profit` and `gas_safety` each move the result by a few percent. The exception is
   a very shallow DEX pool (20k USD), which cuts profit by two thirds.
6. **Speed matters only relative to rivals.** Going from 6 s to 1 s reaction adds about 20%;
   being slower than the rivals (60 s) removes about 90%.
7. **Capital is not the constraint.** The base case spends about 11 USD of gas per year and the
   gas wallet never dips more than cents. The realistic downside is the infrastructure bill
   plus your time, roughly 100-600 USD a year.
8. **Varying everything at once (3,000 random combinations): 43% lose money, the median earns
   136 USD a year, a third earn over 1,000.** Only the combination "at least 5M USD market,
   professionals absent, slow rivals" is reliably good (median about 10,600, 1% lose), and
   that was 6% of the combinations tried. Those percentages reflect the ranges I chose to
   sample, not measured odds.

## What this means

The strategy is a bet on finding a market that is big enough to matter and still ignored by
professional bots. Such markets exist only briefly, because the profit itself attracts
competition. The code and the costs are not the problem.

The two decisive unknowns are measurable before risking anything: the bot's `backtest`
command reports the number of distinct liquidators and how many blocks positions sat
liquidatable (the head start). A candidate market is worth running only if head starts are
long and liquidators few.

## How far to trust the model

- Borrower behaviour is calibrated to Aave's published lifetime ratios (about 3.3% of borrow
  transactions end in liquidation, about 0.45% of borrowed volume). A small fork may differ.
- The per-liquidation profit formula is tested against the real contract on the fake chain
  (`tests/test_research.py`) and matches to the cent.
- The competition model is my assumption: the chance of winning falls exponentially with the
  size of the profit and with our reaction time relative to rivals. It is plausible, not measured.
- Large liquidations are sold in one swap, so pool depth caps a single profit at about 190 USD
  in the base case. Splitting the sale would raise that.
- Prices are synthetic (70% yearly volatility, fat tails, volatility clustering), not history.
- Not modelled: bad debt, oracle failures, contract bugs, the L1 data fee on rollups, taxes.
