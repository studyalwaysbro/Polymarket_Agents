# Speaker Notes — Polymarket Pricing Gap Detection
### Nicholas Tavares & Matthew White
### Stevens Institute of Technology — Big Data Technologies — March 2026

---

## Slide 1: Title Slide

**Nick opens:**

"Hey everyone — so Matt and I built something kind of ambitious this semester. We tried to answer a pretty simple question: can AI find pricing mistakes in prediction markets before the market corrects them? And the honest answer — which we'll get into — is that the market is really, really good at its job. But we learned a ton about big data architecture, NLP, and market efficiency in the process, and we actually stumbled into something potentially more interesting along the way."

**Matt adds:**

"Yeah — and what we're going to show you today is a full production system. This isn't a Jupyter notebook. This is 8,000 lines of Python, 9 database tables, 8 data sources, 4 AI agents running autonomously. We'll walk you through how we built it, what it found, and — honestly — why it didn't work the way we hoped."

---

## Slide 2: What Are Prediction Markets?

**Nick:**

"So for anyone not familiar — prediction markets are basically stock markets for beliefs. Instead of trading shares of Apple, you trade contracts on events. 'Will the Fed cut rates in June?' If the market price is 65 cents, the crowd is saying there's a 65% chance it happens. If you think it's actually 80%, you buy at 65 and profit when it resolves."

"Polymarket is the biggest one. It's decentralized — runs on the Polygon blockchain — and it blew up during the 2024 election. Over a billion dollars in volume. They cover everything from presidential elections to 'Will Jesus Christ return before GTA VI comes out.' Not joking, that's a real market."

**Matt:**

"Our central question was: if we throw 8 different data sources at this — social media, news feeds, cross-platform data — and run NLP sentiment analysis on all of it, can we find moments where the crowd is wrong? Where social media knows something the market hasn't priced yet? Spoiler alert — it's harder than it sounds."

---

## Slide 3: How We Built It

**Matt:**

"So here's the architecture. We used a framework called CrewAI — it lets you build multi-agent systems where each AI agent has a specific role. Think of it like a team of analysts, each doing one job."

"Agent 1 goes out and collects data — pulls every contract from Polymarket's API, then scrapes social media for each one. Agent 2 takes all those social posts and runs sentiment analysis — and this is a three-model ensemble, not just one model. Agent 3 is the gap detector — it compares what social media thinks the probability should be versus what the market is actually pricing. Agent 4 formats the results."

"Each agent hands off to the next. If Agent 1 crashes, Agent 2 doesn't run — the error is isolated. And everything persists to a PostgreSQL database at every stage, so we never lose data even if the pipeline breaks mid-cycle."

**Nick:**

"On the big data side — we've got PostgreSQL with a 20-connection pool, SQLAlchemy ORM, JSONB columns for flexible metadata storage, Pydantic for type-safe configuration, and a FastAPI dashboard with 8 REST endpoints. This thing is built like production infrastructure, not a homework project."

---

## Slide 4: 8 Heterogeneous Data Sources

**Nick:**

"This is where it gets interesting from a big data perspective. We're pulling from 8 completely different data sources, each with its own API format, rate limits, and quirks."

"Polymarket's Gamma API gives us the market data — 625 contracts, paginated, rate-limited to 10 requests per minute. We built exponential backoff for 429 errors. Bluesky is our primary social source — we use the AT Protocol SDK, get 47 to 75 posts per contract. GDELT gives us global news in 65 languages. RSS feeds from Reuters, BBC, AP. Reddit through Redlib mirror scraping. X/Twitter through Nitter mirror instances."

"Two sources are broken right now — Tavily's been throwing HTTP 432 errors consistently, and we never got Grok API credentials set up. But here's the thing — the system handles this gracefully. Missing sources just get skipped. The pipeline still runs with whatever's available."

**Matt:**

"One thing worth mentioning — we do a dual search strategy for every contract. We search by extracted keywords AND by the full contract title. So for 'Will Trump win Iowa?', we search for 'Trump win Iowa' AND the full question. This catches both broad topic discussion and bet-specific conversations."

---

## Slide 5: Ensemble Sentiment Analysis

**Matt:**

"So sentiment analysis is where the NLP lives. We didn't want to rely on a single model because every approach has blind spots. So we built a three-model ensemble."

"DeepSeek — which is an LLM — gets 50% of the weight. It's the smart one. It understands context, sarcasm, conditional statements. When someone says 'there's no way the Fed DOESN'T cut rates' — a lexicon model sees the word 'no' and thinks it's negative. DeepSeek understands the double negative."

"VADER gets 25% — it's a rule-based lexicon specifically built for social media. It knows that 'this is AMAZING!!!' with all caps and exclamation marks is very positive. TextBlob gets the other 25% — it's trained on different data, so it provides a complementary signal."

"The idea is that when the LLM hallucinates — and LLMs do hallucinate — the lexicon models anchor the score back toward reality. It's like having a creative analyst checked by two accountants."

**Nick:**

"From a throughput perspective — we batch 5 posts per LLM call. That's an 80% reduction in API costs compared to one-at-a-time. We built JSON repair logic because LLMs don't always return clean JSON — sometimes you get markdown fences, trailing commas, missing brackets. We handle all of that automatically."

---

## Slide 6: Four Gap Detection Strategies

**Nick:**

"So once we have sentiment scores for every contract, we need to actually detect pricing gaps. We built four different detectors."

"Type 1 — sentiment-probability mismatch — is the main one. We convert the average sentiment score into an implied probability using a scaling factor alpha of 0.4. If social media is really bullish on a contract but the market is pricing it lower, that's a potential gap. We flag anything where the difference is at least 4 percentage points."

"Type 2 is information asymmetry — has sentiment shifted in the last 3 hours but the market odds haven't moved? That could mean the market hasn't caught up yet."

"Type 3 is z-score pattern deviation — is the current price a statistical outlier compared to its own history? More than 1.5 standard deviations and we flag it."

"Type 4 is cross-market arbitrage — we search Kalshi and Manifold Markets for the same question. If Polymarket says 60% and Kalshi says 45% for the same event, that's a potential arb. We use LLM-confirmed semantic matching to make sure we're actually comparing the same markets and not similar-sounding but different ones."

---

## Slide 7: Database Architecture

**Matt:**

"Here's the database. Nine tables, all in PostgreSQL. And these numbers are real — this is from our actual production database."

"625 contracts tracked. 32,791 social media posts collected. 12,185 sentiment analyses performed. And 30 detected gaps. Notice the ratio there — 32,000 data points in, 30 signals out. That's a 0.09% hit rate, which actually tells you something about market efficiency."

"A few big data patterns worth highlighting: JSONB evidence columns let us store different metadata per gap type without schema changes. SHA-256 deduplication means we never process the same post twice. The store-all-analyze-selectively pattern means we have complete market history even for contracts we didn't deeply analyze."

"And notice the zero in backtest results. That's going to come up again."

---

## Slide 8: What We Found

**Nick:**

"Alright, here's where we get honest. Four cycles completed over three days. The first couple had errors — schema issues, empty runs. The third cycle ran for over two hours, analyzed 126 contracts, collected 8,000 social posts — found zero gaps. The fourth cycle found all 30."

"Now, look at the breakdown. Half of these gaps — 15 out of 30 — are NHL Stanley Cup contracts. Teams with 0.1% market odds where fan sentiment says 5%. The average confidence is 53, the average edge is under 5%."

"The highest-confidence gap — 98% confidence — is 'Will Jesus Christ return before GTA VI?' It shows as a cross-platform arbitrage because one platform prices it at 48% and another at 2%. That's not an arbitrage opportunity. That's two different groups of people pricing a joke differently."

**Matt:**

"And the critical number: zero validated predictions. We have a backtest table. It has zero rows. Not a single gap has been checked against what actually happened. So every confidence score you see here is an unverified hypothesis."

---

## Slide 9: The Social Media Optimism Bias

**Matt:**

"This is the key finding, and honestly, it's more interesting than finding actual gaps would have been."

"Look at this scatter plot. The x-axis is what the market says. The y-axis is what social media sentiment implies. The dashed line is perfect efficiency — if every point sat on that line, sentiment and market prices would agree perfectly."

"See that cluster in the bottom left? That's 15 NHL teams. The market prices them at essentially zero — 0.1%, 0.2% — because most teams aren't going to win the Stanley Cup. That's just math. But fan sentiment? Fans are always optimistic about their team. Our system sees 'Go Kraken!' on Reddit and converts that into a 5% implied probability. The market is right. The fans are biased."

**Nick:**

"And this isn't just sports. 29 out of 30 gaps are in the same direction — social media is more bullish than the market. That's not random. That's a known phenomenon called social media optimism bias. People post about things they're excited about. Nobody's writing passionate Reddit threads about how their team is definitely NOT going to win. The data-generating process itself is biased, and our system was picking up that bias and calling it alpha."

---

## Slide 10: Five Reasons This Doesn't Work

**Nick:**

"So let's be systematic about why this approach fails."

"Number one — speed. This is the big one. Polymarket prices move in minutes when new information hits. Our system runs once a day. By the time we detect a gap at 8 PM, any real gap was closed at 2 PM by traders who saw the same news six hours ago. We're not even in the same time zone as the edge."

"Number two — no information advantage. We're reading the same Reddit threads, same news articles, same tweets as the people actually trading these markets. We have no proprietary data. We have no edge."

**Matt:**

"Number three — sentiment doesn't equal information. A thousand fans saying 'Go Sharks!' is not the same as one insider knowing something material about the team. We treat them equally, and we shouldn't."

"Number four — insufficient historical data. We have exactly 8 records in our historical odds table. You can't do meaningful z-score analysis with 8 data points. Our pattern deviation detector was basically blind."

"Number five — liquidity. Even the gaps that might be real are on contracts where one trade would move the price more than the edge. You can't profitably trade a market with 0.1% odds and no liquidity."

---

## Slide 11: Evidence for Weak-Form Market Efficiency

**Matt:**

"So what does this all add up to? Evidence for the Efficient Market Hypothesis — specifically weak-form efficiency — in liquid prediction markets."

"Kyle's 1985 model says that in markets with transparent order books, market makers learn from trading activity almost instantly. Polymarket is on a blockchain — every single trade is public. The information incorporation rate lambda is basically instantaneous for popular contracts."

"What our data shows is a spectrum. The liquid contracts — presidential elections, crypto prices — are highly efficient. No amount of sentiment scraping will beat them. Mid-tier contracts — major sporting events, geopolitics — are moderately efficient. And the long tail — meme markets, niche events — yeah, those are less efficient. But they're illiquid. You can't trade them at scale. The efficiency frontier protects itself."

---

## Slide 12: What We Have vs. What We'd Need

**Nick:**

"This slide is the honest gap analysis. What would it actually take to make this work?"

"Frequency: we'd need to go from once a day to every 5-15 minutes. That's 96 to 288 times faster. Information sources: we'd need proprietary data, not public social media. Sentiment calibration: our alpha parameter of 0.4 was a guess. We'd need source-specific calibration curves built from historical data. Market universe: instead of scanning 625 contracts broadly, we'd need to focus on 10-20 liquid contracts with upcoming catalysts."

"And the biggest one: validation. We have zero resolved predictions. We'd need 30+ days of tracked outcomes to even know if we have a signal."

**Matt:**

"Here's the meta-lesson, and this applies way beyond prediction markets: we built the pipeline before proving the signal exists. In quant finance, the right order is — first, get historical data and prove there's a pattern. Second, estimate if the edge survives transaction costs. Third — and only then — build the real-time system. We did step three first. That's the most expensive way to discover you don't have an edge. But now we know, and the infrastructure isn't wasted."

---

## Slide 13: Prediction Markets as Equity Signal Features

**Nick:**

"So here's where we got excited about a pivot. If you can't beat prediction markets — use them."

"Think about this: Polymarket has real-time probability estimates for things like 'Will the Fed cut rates in June?' or 'Will the US raise tariffs on China?' These are macro events that directly move stock prices. And they're being priced 24/7 by financially incentivized participants."

"Most equity models use lagging indicators — GDP reports that are months old, employment data that's weeks old. Prediction market odds are forward-looking and continuously updating. That's a completely different kind of input feature."

**Matt:**

"The key insight is that stock market participants don't systematically incorporate prediction market probabilities. A portfolio manager might check Polymarket casually, but nobody is feeding those odds into their quantitative models as features. That's the potential information asymmetry — not between us and Polymarket, but between Polymarket and the stock market."

"We'd target something like a Temporal Fusion Transformer — a TFT model — because it handles mixed-frequency inputs natively. You can feed in daily stock prices alongside real-time prediction market odds, and the model's attention mechanism will tell you which prediction market features are actually driving the equity forecast. It's interpretable, which matters."

"The best part? Our data collection pipeline already does this. We're already pulling Polymarket odds into a database every cycle. We just need to pipe it into an equity model instead of trying to trade prediction markets directly."

---

## Slide 14: Full Technology Stack

**Matt:**

"Quick run through the tech stack for the engineering-minded folks in the room."

"On the AI side — CrewAI for agent orchestration, LangChain for LLM abstraction, DeepSeek as our primary reasoning model, Ollama running LLaMA 3.1 locally for lightweight tasks like keyword extraction. The cost optimization here matters — we use the expensive API model for actual analysis and the free local model for trivial work."

"Data layer — PostgreSQL with SQLAlchemy ORM. FastAPI serving the dashboard. Pydantic ensuring our configuration is type-safe, which matters when you have 40+ settings. Pandas and NumPy for numerical work."

"Infrastructure — Beautiful Soup for web scraping, feedparser for RSS, ratelimit library for API throttling, and Rich for beautiful console output because even pipelines that run at 2 AM deserve nice formatting."

**Nick:**

"In total — about 8,000 lines of code across 14 service modules, 4 AI agents, 8 API endpoints, and a 9-table database. This is a genuine big data application."

---

## Slide 15: What We Learned

**Nick:**

"So what did we actually learn?"

"First — the architecture works. This is a legitimate production system. 32,000 social posts, 625 contracts, ensemble NLP, multi-agent orchestration — everything runs. The pipeline itself is solid."

"Second — the signal doesn't work yet. Social media sentiment is not market-beating information, at least not at daily frequency against these markets. The optimism bias is real and it's structural."

"But third — and this is what I want everyone to take away — negative results are results. We now have empirical evidence that Polymarket is efficient against this specific type of analysis. That's actually a contribution to the literature. Most papers only publish wins. We're publishing what we actually found."

**Matt:**

"And the infrastructure survives the pivot. Everything we built — the data collection, the database, the sentiment analysis — can be repurposed for the equity signal feature idea. We didn't waste work. We learned something real, built something real, and found a potentially better direction to take it."

"Thank you."

---

## Slide 16: Questions

**Nick:**

"We're happy to take questions. And if anyone wants to dig into the code, it's on GitHub — the architecture is all there."

**Possible Q&A prep:**

- **Q: "Why not just run it more frequently?"**
  A: "We could, but speed alone doesn't solve the information disadvantage problem. Even at 15-minute cycles, we're still reading the same public sources as traders. The real fix requires either proprietary data or a completely different signal."

- **Q: "How much did this cost to run?"**
  A: "Surprisingly little. DeepSeek is about 50 cents per full cycle. Ollama is free — runs locally. The main cost was our PostgreSQL database and development time. Total API costs over the project were probably under $20."

- **Q: "Could you use this for actual trading?"**
  A: "In its current form, no — and that's actually the honest answer. But the equity feature application doesn't require beating prediction markets. It just requires that prediction market odds contain information that equity models aren't using, which is much more plausible."

- **Q: "What about the meme market arbitrage?"**
  A: "Great question. The Jesus Christ / GTA VI 'arbitrage' perfectly illustrates why you need human judgment alongside algorithmic detection. Different platforms have different user populations pricing the same joke differently. That's sociology, not arbitrage."

- **Q: "How does CrewAI compare to just running functions sequentially?"**
  A: "For this project, the main benefit was error isolation and the ability to give each agent different LLM prompts optimized for its role. The data collector's prompt is focused on extraction; the sentiment analyzer's prompt is focused on nuance. CrewAI makes that separation clean."
