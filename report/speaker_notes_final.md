# Speaker Notes — Polymarket Pricing Gap Detection
### Nicholas Tavares & Matthew White
### Stevens Institute of Technology — Big Data Technologies — Spring 2026

---

## Slide 1: Title — "Can AI Find Pricing Mistakes in Prediction Markets?"

**NICK opens:**

"Hey everyone — so Matt and I built something kind of ambitious this semester. We tried to answer a pretty simple question: can AI find pricing mistakes in prediction markets before the market corrects them? And the honest answer — which we'll get into — is that the market is really, really good at its job. But we learned a ton about big data architecture, NLP, and market efficiency in the process, and we actually stumbled into something potentially more interesting along the way."

**MATT adds:**

"Yeah — and what we're going to show you today is a full production system. This isn't a Jupyter notebook. This is 8,000 lines of Python, 9 database tables, 8 data sources, 4 AI agents running autonomously. We'll walk you through how we built it, what it found, and — honestly — why it didn't work the way we hoped."

---

## Slide 2: What Are Prediction Markets?

**NICK:**

"So for anyone not familiar — prediction markets are basically stock markets for beliefs. Instead of trading shares of Apple, you trade contracts on events. 'Will the Fed cut rates in June?' If the market price is 65 cents, the crowd is saying there's a 65% chance it happens. If you think it's actually 80%, you buy at 65 and profit when it resolves."

"Polymarket is the biggest one. It's decentralized — runs on the Polygon blockchain — and it blew up during the 2024 election. Over a billion dollars in volume. They cover everything from presidential elections to 'Will Jesus Christ return before GTA VI comes out.' Not joking, that's a real market."

**MATT:**

"Our central question was: if we throw 8 different data sources at this — social media, news feeds, cross-platform data — and run NLP sentiment analysis on all of it, can we find moments where the crowd is wrong? Where social media knows something the market hasn't priced yet? Spoiler alert — it's harder than it sounds."

---

## Slide 3: The Journey

**NICK:**

"So let me walk you through how this actually came together, because it wasn't a straight line."

"Matt kicked things off back in early February with the initial codebase — Polymarket API integration, Bluesky scraper, RSS feeds, basic single-model sentiment. That was the foundation."

"I forked it the next day and immediately hit a wall. The Polymarket API parser had two bugs that were there from day one — odds were always coming back as zero because the API sends outcome prices in a separate array, not nested where the code expected them. And categories were always 'Unknown' because they're buried inside an events array. So for the first couple days, we were literally running a gap detector on garbage data and wondering why nothing made sense."

"On top of that — 429 rate limits were causing infinite recursion, one failed social post would rollback the entire batch, and deduplication was using Python's built-in hash() which changes between runs. So the same articles kept getting re-inserted every cycle."

**MATT:**

"Once Nick got those fixed, he went pretty hard on a v2.0 rewrite in March. I was working on the upstream side — performance tuning, keeping the core clean."

**NICK:**

"Yeah, the March 11th commit was the big one for me — 19 new files, about 4,500 lines of code. Added 5 new data sources, built the ensemble sentiment system, the FastAPI dashboard, DeepSeek LLM integration, smart contract selection instead of just grabbing the first N contracts, and the backtesting framework."

"Then in April, Matt pushed some really important stuff upstream — the volume spike detector, which is about 390 lines of new gap detection logic, and semantic inversion detection for cross-market arbitrage. That one's actually really clever — it catches cases where two markets look related but have opposite semantics, like 'Will X pass?' versus 'Will X fail?' Those aren't arbitrage, they're consistent, and before Matt's fix we would have flagged them."

---

## Slide 4: System Architecture

**MATT:**

"So here's the architecture. We used a framework called CrewAI — it lets you build multi-agent systems where each AI agent has a specific role. Think of it like a team of analysts, each doing one job."

"Agent 1 goes out and collects data — pulls every contract from Polymarket's API, then scrapes social media for each one. Agent 2 takes all those social posts and runs sentiment analysis — and this is a three-model ensemble, not just one model. Agent 3 is the gap detector — it compares what social media thinks versus what the market is pricing. Agent 4 formats the results."

"Each agent hands off to the next. If Agent 1 crashes, Agent 2 doesn't run — the error is isolated. And everything persists to a PostgreSQL database at every stage, so we never lose data even if the pipeline breaks mid-cycle."

**NICK:**

"On the big data side — we've got PostgreSQL with a 20-connection pool, SQLAlchemy ORM, JSONB columns for flexible metadata storage, Pydantic for type-safe configuration, and a FastAPI dashboard with 8 REST endpoints. This thing is built like production infrastructure, not a homework project."

"The smart contract selection was a big deal too. Instead of just analyzing whatever contracts come back first, we fetch the full universe — over 625 contracts — store everything for historical tracking, then filter out dead markets and rank what's left by volume, volatility, uncertainty, whether it's near expiry, liquidity, and spread. The most interesting contracts get processed first."

---

## Slide 5: 8 Heterogeneous Data Sources

**NICK:**

"This is where it gets interesting from a big data perspective. We're pulling from 8 completely different data sources, each with its own API format, rate limits, and quirks."

"Polymarket's Gamma API gives us the market data — 625 contracts, paginated, rate-limited to 10 requests per minute. We built exponential backoff for 429 errors. Bluesky is our primary social source — we use the AT Protocol SDK, get 47 to 75 posts per contract. GDELT gives us global news in 65 languages. RSS feeds from Reuters, BBC, AP. And Polymarket's own comment threads for people actually discussing the bets."

"Two sources are broken right now — Tavily's been throwing HTTP 432 errors consistently, and we never got Grok API credentials set up. But the system handles this gracefully. Missing sources just get skipped. The pipeline still runs with whatever's available."

**MATT:**

"One thing worth mentioning — we do a dual search strategy for every contract. We search by extracted keywords AND by the full contract title. So for 'Will Trump win Iowa?', we search for 'Trump win Iowa' AND the full question. This catches both broad topic discussion and bet-specific conversations. Results get deduplicated by post ID within each source."

---

## Slide 6: Ensemble Sentiment Analysis

**MATT:**

"So sentiment analysis is where the NLP lives. We didn't want to rely on a single model because every approach has blind spots. So we built a three-model ensemble."

"DeepSeek — which is an LLM — gets 50% of the weight. It's the smart one. It understands context, sarcasm, conditional statements. When someone says 'there's no way the Fed DOESN'T cut rates' — a lexicon model sees the word 'no' and thinks it's negative. DeepSeek understands the double negative."

"VADER gets 25% — it's a rule-based lexicon specifically built for social media. It knows that 'this is AMAZING!!!' with all caps and exclamation marks is very positive. TextBlob gets the other 25% — it's trained on different data, so it provides a complementary signal."

"The idea is that when the LLM hallucinates — and LLMs do hallucinate — the lexicon models anchor the score back toward reality. It's like having a creative analyst checked by two accountants."

**NICK:**

"From a throughput perspective — we batch 5 posts per LLM call. That's an 80% reduction in API costs compared to one-at-a-time. We built JSON repair logic because LLMs don't always return clean JSON — sometimes you get markdown fences, trailing commas, missing brackets. We handle all of that automatically. And the cheap stuff — keyword extraction, basic classification — always runs through Ollama locally for free, no matter what the primary LLM provider is."

---

## Slide 7: Five Gap Detection Strategies

**NICK:**

"Once we have sentiment scores for every contract, we need to actually detect pricing gaps. We built five different detectors."

"Type 1 — sentiment-probability mismatch — is the main one. We convert the average sentiment score into an implied probability using a scaling factor alpha of 0.4. If social media is really bullish but the market is pricing lower, that's a potential gap. We flag anything where the difference is at least 4 percentage points."

"Type 2 is information asymmetry — has sentiment shifted in the last 3 hours but market odds haven't moved? That could mean the market hasn't caught up."

"Type 3 is z-score pattern deviation — is the current price a statistical outlier compared to its own history?"

"Type 4 is cross-market arbitrage — we compare prices on Kalshi and Manifold for the same event. And Matt's semantic inversion fix makes sure we're actually comparing the same question, not similar-sounding but opposite ones."

**MATT:**

"And Type 5 — volume spike — is the newest addition. I built that in the v2.1 push. It flags contracts with unusual volume patterns that might be telling us something about incoming price movement. That's about 390 lines of new detection code."

"Every gap also gets a dynamic confidence score from 0 to 100. It factors in the size of the gap, how much data we have, whether multiple sources agree, and the contract's own features like volatility and liquidity. If we don't have much data, the confidence gets pulled down automatically."

---

## Slide 8: What We Found

**NICK:**

"Alright, here's where we get honest. Four cycles completed over three days. The first couple had errors — schema issues, empty runs. The third cycle ran for over two hours, analyzed 126 contracts, collected 8,000 social posts — found zero gaps. The fourth cycle found all 30."

"Now, look at the breakdown. Half of these — 15 out of 30 — are NHL Stanley Cup contracts. Teams with 0.1% market odds where fan sentiment says 5%. The average confidence is 53, the average edge is under 5%."

"The highest-confidence gap — 98% — is 'Will Jesus Christ return before GTA VI?' It shows as a cross-platform arbitrage because one platform prices it at 48% and another at 2%. That's not an arbitrage opportunity. That's two different groups of people pricing a joke differently."

**MATT:**

"And the critical number: zero validated predictions. We have a backtest table. It has zero rows. Not a single gap has been checked against what actually happened. So every confidence score you see here is an unverified hypothesis. We're being upfront about that."

---

## Slide 9: The Social Media Optimism Bias

**MATT:**

"This is the key finding, and honestly, it's more interesting than finding actual gaps would have been."

"If you imagine a scatter plot — the x-axis is what the market says, the y-axis is what social sentiment implies — the dashed line would be perfect efficiency. If every point sat on that line, sentiment and market prices would agree perfectly."

"What we actually see is a cluster of points all above that line, especially in the bottom left. That's 15 NHL teams. The market prices them at essentially zero — 0.1%, 0.2% — because most teams aren't going to win the Stanley Cup. That's just math. But fan sentiment? Fans are always optimistic about their team. Our system sees 'Go Kraken!' on Reddit and converts that into a 5% implied probability. The market is right. The fans are biased."

**NICK:**

"And this isn't just sports. 29 out of 30 gaps are in the same direction — social media more bullish than the market. That's not random. That's social media optimism bias. People post about things they're excited about. Nobody's writing passionate Reddit threads about how their team is definitely NOT going to win. The data-generating process itself is biased, and our system was picking up that bias and calling it alpha. That was the moment we realized what we were actually looking at."

---

## Slide 10: Five Reasons This Doesn't Work

**NICK:**

"So let's be systematic about why this approach fails."

"Number one — speed. This is the big one. Polymarket prices move in minutes when new information hits. Our system runs once a day. By the time we detect a gap at 8 PM, any real gap was closed at 2 PM by traders who saw the same news six hours ago. We're not even in the same time zone as the edge."

"Number two — no information advantage. We're reading the same Reddit threads, same news articles, same tweets as the people actually trading these markets. We have no proprietary data. We have no edge."

**MATT:**

"Number three — sentiment doesn't equal information. A thousand fans saying 'Go Sharks!' is not the same as one insider knowing something material about the team. We treat them equally, and we shouldn't."

"Number four — insufficient historical data. We have exactly 8 records in our historical odds table. You can't do meaningful z-score analysis with 8 data points. Our pattern deviation detector was basically blind."

"Number five — liquidity. Even the gaps that might be real are on contracts where one trade would move the price more than the edge. You can't profitably trade a market with 0.1% odds and no liquidity."

---

## Slide 11: Evidence for Weak-Form Market Efficiency

**MATT:**

"So what does this all add up to? Evidence for the Efficient Market Hypothesis — specifically weak-form efficiency — in liquid prediction markets."

"Kyle's 1985 model says that in markets with transparent order books, market makers learn from trading activity almost instantly. Polymarket is on a blockchain — every single trade is public. The information incorporation rate is basically instantaneous for popular contracts."

"What our data shows is a spectrum. Liquid contracts — presidential elections, crypto prices — are highly efficient. No amount of sentiment scraping will beat them. Mid-tier contracts are moderately efficient. And the long tail — meme markets, niche events — yeah, those are less efficient. But they're illiquid. You can't trade them at scale. The efficiency frontier protects itself."

**NICK:**

"That's the paradox we keep coming back to. The contracts where we could beat the market, we can't trade because there's no liquidity. The contracts where there's enough liquidity to trade, we can't beat because they're too efficient. It's a clean trap."

---

## Slide 12: The Pivot — Prediction Markets as Equity Features

**NICK:**

"So here's where we got excited about a pivot. If you can't beat prediction markets — use them."

"Think about this: Polymarket has real-time probability estimates for things like 'Will the Fed cut rates in June?' or 'Will the US raise tariffs on China?' These are macro events that directly move stock prices. And they're being priced 24/7 by financially incentivized participants."

"Most equity models use lagging indicators — GDP reports that are months old, employment data that's weeks old. Prediction market odds are forward-looking and continuously updating. That's a completely different kind of input feature."

**MATT:**

"The key insight is that stock market participants don't systematically incorporate prediction market probabilities. A portfolio manager might check Polymarket casually, but nobody is feeding those odds into their quantitative models as features. That's the potential information asymmetry — not between us and Polymarket, but between Polymarket and the stock market."

"We'd target something like a Temporal Fusion Transformer — a TFT model — because it handles mixed-frequency inputs natively. You can feed in daily stock prices alongside real-time prediction market odds, and the model's attention mechanism will tell you which prediction market features are actually driving the equity forecast. It's interpretable, which matters."

"And the best part? Our data collection pipeline already does this. We're already pulling Polymarket odds into a database every cycle. We just need to pipe it into an equity model instead of trying to trade prediction markets directly."

---

## Slide 13: Who Built What

**NICK:**

"So people always ask about the split. We tracked this carefully — I actually built a provenance system called TRUTH.md that documents every single commit, who wrote it, and whether it came from Matt's upstream repo or my fork."

"Matt built the original codebase — the Polymarket API integration, Bluesky scraper, RSS feeds, the basic sentiment pipeline, gap detection, and the PostgreSQL schema. That was the foundation everything else was built on."

"I took that and built the v2.0 overhaul on top — that's the 5 new data sources, the ensemble sentiment system, the FastAPI dashboard, DeepSeek integration, smart contract selection, and the backtesting framework. About 4,500 lines across 19 new files. Plus all the bug fixes we talked about earlier and the operations side — actually running the cycles, analyzing results, writing documentation."

**MATT:**

"And for v2.1 I pushed the volume spike detector — that's about 390 lines of new gap detection — and the semantic inversion detection for cross-market arbitrage. Plus some dashboard fixes and the .env parsing bug that was driving us crazy."

"We used an upstream/fork collaboration model. Three syncs total over the project. Every commit is documented. There's no ambiguity about who did what."

---

## Slide 14: Technology Stack

**MATT:**

"Quick run through the tech stack for the engineering-minded folks in the room."

"On the AI side — CrewAI for agent orchestration, LangChain for LLM abstraction, DeepSeek as our primary reasoning model, Ollama running LLaMA 3.1 locally for lightweight tasks like keyword extraction. The cost optimization here matters — we use the expensive API model for actual analysis and the free local model for trivial work."

"Data layer — PostgreSQL with SQLAlchemy ORM. FastAPI serving the dashboard. Pydantic ensuring our configuration is type-safe, which matters when you have 40+ settings. Pandas and NumPy for numerical work."

"Infrastructure — Beautiful Soup for web scraping, feedparser for RSS, ratelimit library for API throttling, and Rich for beautiful console output because even pipelines that run at 2 AM deserve nice formatting."

**NICK:**

"In total — about 8,000 lines of code across 14 service modules, 4 AI agents, 8 API endpoints, and a 9-table database. Total API costs over the entire project were probably under $20. This is genuinely production-grade big data infrastructure."

---

## Slide 15: What We Learned

**NICK:**

"So what did we actually learn?"

"First — the architecture works. This is a legitimate production system. 32,000 social posts, 625 contracts, ensemble NLP, multi-agent orchestration — everything runs. The pipeline itself is solid."

"Second — the signal doesn't work yet. Social media sentiment is not market-beating information, at least not at daily frequency against these markets. The optimism bias is real and it's structural."

"But third — and this is what I want everyone to take away — negative results are results. We now have empirical evidence that Polymarket is efficient against this specific type of analysis. That's actually a contribution to the literature. Most papers only publish wins. We're publishing what we actually found."

**MATT:**

"And the infrastructure survives the pivot. Everything we built — the data collection, the database, the sentiment analysis — can be repurposed for the equity signal feature idea. We didn't waste work. We learned something real, built something real, and found a potentially better direction to take it."

"There's a meta-lesson too, and this applies way beyond prediction markets: we built the pipeline before proving the signal exists. In quant finance, the right order is — first, get historical data and prove there's a pattern. Second, estimate if the edge survives transaction costs. Third — and only then — build the real-time system. We did step three first. That's the most expensive way to discover you don't have an edge. But now we know, and the infrastructure isn't wasted."

---

## Slide 16: Questions

**NICK:**

"We're happy to take questions. And if anyone wants to dig into the code, it's on GitHub — the architecture is all there."

**MATT:**

"Yeah, ask us anything."

---

## Q&A Prep (Not on slides — for reference only)

**Q: "Why not just run it more frequently?"**

NICK: "We could, but speed alone doesn't solve the information disadvantage problem. Even at 15-minute cycles, we're still reading the same public sources as traders. The real fix requires either proprietary data or a completely different signal."

**Q: "How much did this cost to run?"**

MATT: "Surprisingly little. DeepSeek is about 50 cents per full cycle. Ollama is free — runs locally. The main cost was our PostgreSQL database and development time. Total API costs over the project were probably under $20."

**Q: "Could you use this for actual trading?"**

NICK: "In its current form, no — and that's actually the honest answer. But the equity feature application doesn't require beating prediction markets. It just requires that prediction market odds contain information that equity models aren't using, which is much more plausible."

**Q: "What about the meme market arbitrage?"**

MATT: "Great question. The Jesus Christ / GTA VI 'arbitrage' perfectly illustrates why you need human judgment alongside algorithmic detection. Different platforms have different user populations pricing the same joke differently. That's sociology, not arbitrage."

**Q: "How does CrewAI compare to just running functions sequentially?"**

MATT: "For this project, the main benefit was error isolation and the ability to give each agent different LLM prompts optimized for its role. The data collector's prompt is focused on extraction; the sentiment analyzer's prompt is focused on nuance. CrewAI makes that separation clean."

**Q: "What would you do differently?"**

NICK: "Prove the signal before building the system. Get 6 months of Polymarket historical data, backtest whether sentiment has ever predicted price movement, and only if that shows something would we build the real-time pipeline. We did it backwards, but we learned from that."
