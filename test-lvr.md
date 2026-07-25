Good question. Understanding this precisely will make your call much stronger.

**Why no rebalancing during the vault term:**

```
Saffron's vault is a fixed-term contract
between two parties:

Party A (LP): "I will receive X% fixed APY
               guaranteed for 90 days"

Party B (speculator): "I will pay that fixed
                       rate and keep whatever
                       the position actually earns"

Both parties signed a contract at the start.
The terms are fixed. Immutable.

If Saffron rebalanced the underlying
Uniswap position mid-term:
- The position changes
- The expected yield changes
- Party B's bet changes without their consent
- The fixed rate Party A was promised
  may no longer be sustainable
- The contract terms are violated

So the position stays locked exactly as
set at vault creation until expiry.
No touching it.
```

**Why this makes LVR a pricing problem, not an execution problem:**

```
At vault creation Saffron has to decide:
"This ETH/USDC position will earn approximately
X% over the next 90 days"

They use that estimate to set the fixed rate
they offer Party A.

BUT they are probably underestimating because:
  Gross yield from fees: 15%
  Minus LVR extraction:  -8%
  Actual net yield:       7%

If Saffron prices the fixed rate at 12%
assuming 15% gross yield, but LVR takes 8%,
the speculator only earns 7% and loses money.

Next time they will not use Saffron.

OR Saffron's treasury absorbs the difference.
Either way: bad outcome caused by not
accounting for LVR at pricing time.
```

**The specific value you bring to the call:**

```
You can tell him:
"For a given pair, price range, and term length,
my backtest infrastructure estimates how much
LVR will reduce the gross fee yield. That number
should be subtracted from expected yield before
you set your fixed rate offer."

Concrete example to bring to the call:
ETH/USDC 0.05% fee tier, ±10% range, 90-day term:
  Gross fee APY: ~15% (from historical data)
  Expected LVR:  ~6-8% (from your model)
  Net yield for pricing: ~7-9%

Saffron should price the fixed rate below 7%,
not below 15%.

That is the gap your research fills.
```

**One thing to do before the call:**

Run your backtest on ETH/USDC specifically. You said your model is validated on stablecoin pairs, not volatile pairs yet. Before the call, be honest about whether you can extend the model to ETH/USDC or whether you need to say "I can do this for stablecoin pairs now and extend to volatile pairs with more data."

Do not promise what you cannot deliver on the call. He already respected you for being honest about scope in the first message. Keep that standard.

Now go submit the hackathon entry. How much time is left?