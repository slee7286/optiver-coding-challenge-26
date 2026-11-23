# Setup Instructions

Three steps to get connected and start trading.

**1**

## Your Development Environment

You'll use a web-based Visual Studio Code environment pre-configured with everything needed to connect to Optibook - terminal, auto-completion, debugging, and the Python client library.

**2**

## Connect to Your Dev Environment

1. Sign up and join a team.
2. Click `Dev Environment` at the top-right of the teams page.
3. Your environment opens in a new tab.

From here you can interact with Optibook using Python or the preloaded Jupyter notebook.

**3**

## Connect to the Exchange

1. Open the Optibook visualizer (`Visualizer` button, top-right) and pick an instrument to trade.
2. Open `your_optiver_workspace/optibook_guides/code_snippets.ipynb`, replace `instrument_id`, and run the cells top-to-bottom.

**You're all set - happy trading!**

Need help? Contact [optibook@optiver.com](mailto:optibook@optiver.com)

# FAQ

Common questions about Optibook.

**TechnicalTrading**

**When I connect, I get a message saying someone was already connected. What now?svg**

This is fine - just keep going. Only one connection is allowed at a time. Most likely your previous connection was killed and you re-connected, or a team member was connected and got kicked. Either way, you are connected.

**Can I disconnect from the exchange?svg**

No - when your application stops running you automatically disconnect and all your orders are pulled. We call this Cancel On Disconnect.

**Trade ticks are always empty after connecting. Why?svg**

The client only starts listening to trades once connected. It does not receive a snapshot of previous trades from the exchange.

**How do I know if an order executed successfully?svg**

There is no notification for orders that become trades. You can poll your private trades using poll_new_trades, or monitor your position for changes.

# FAQ

Common questions about Optibook.

**TechnicalTrading**

**How is my PnL calculated?svg**

Your PnL is the money made or lost in the past, plus the worth of your current positions valued at the last traded price. The get_pnl function calculates this for you - you can also supply a different valuation method (mid-point, vwap, etc.).

**Why can't I trade after buying more than X lots?svg**

There are position limits per instrument. You cannot make a trade that would put you over the limit. For example, with a +/-300 limit and a position of 290 lots, you can only buy 10 more. Limits vary by challenge - check the challenge description.

**In a dual listing, can I transfer a position from one instrument to another?svg**

No. Whatever position you build in one instrument stays there - you need to trade out of it directly.

##  Understand Market-Making

Optibook is the virtual exchange we built in-house for educational purposes. Through Optibook, we hope to show you why market makers are essential for the functioning of the economy.\
\
Optibook simulates real-life financial markets. We will teach you the basics of financial markets, and how market makers improve a market by using algorithms. Then, armed with that knowledge, it will be your task to apply all that theory on real-world practical trading problems. To do so, you will write your own algorithms to improve these markets.\
\
We hope that you walk away with a thorough understanding of what it means to be a market maker, and that you are able to solve some of the problems we face every day on your own.

[Instrument overview interface showing market data](https://2026-docsoc.optibook.net/assets/instrument_overview.png)

[Team leaderboard showing rankings and performance](https://2026-docsoc.optibook.net/assets/leaderboard.png)

## Write Your Own Algorithm

You will write your own Python trading algorithms that interact with Optibook. To do so, we provide you with an Amazon EC2 instance, which you can think of as your own virtual machine in the Amazon cloud.\
\
Your EC2 instance is pre-configured to work with Optibook and gives you everything you need to get started. It contains a VSCode IDE with auto-completion. **Log in credentials will be provided to each participant / team.**\
\
Should you be enrolled in a university course using Optibook, extra course materials will also be shared with you on your development environment as the course progresses.

## The Exchange Setup

Optibook is comprised of three components: the exchange itself, a visualizer that displays information on the market and on your current positions, and "bots" that we use to simulate specific market environments.\
\
When you've made a trade against a weird-looking username such as "debit-trader" or "coointegration-quoter", that's one of our bots.\
\
But keep in mind, Optibook acts like a real exchange; it matches any buy and sell order as long as they share the same price, regardless who they are from. That means you can trade with anyone, not just our bots — also amongst yourselves.

[Trade ticker showing real-time trading activity](https://2026-docsoc.optibook.net/assets/trade_ticker.png)
