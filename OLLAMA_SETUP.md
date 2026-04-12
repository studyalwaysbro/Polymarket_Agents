# Ollama Setup

How to run this system for free using local LLMs through Ollama.

## Why Bother

- $0 forever. No API costs.
- Data stays on your machine.
- No network latency after the model loads.
- Works offline.
- Modern models (Llama 3.1, Qwen 2.5) are good enough for this.

## Quick Setup

### 1. Install

**Linux:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

**Mac:**
```bash
brew install ollama
```

**Windows:**
```bash
winget install Ollama.Ollama
```

### 2. Start and Pull a Model

```bash
ollama serve              # keep this running

# In another terminal:
ollama pull llama3.1:8b   # recommended, ~4.7GB download
```

### 3. Configure `.env`

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_gaps
```

### 4. Run

```bash
python run.py test
python run.py demo
```

Done.

## Model Options

| Model | Size | RAM | Speed | Quality | Notes |
|-------|------|-----|-------|---------|-------|
| llama3.1:8b | 4.7GB | 8GB | Medium | Excellent | Best all-around pick |
| mistral | 4.1GB | 8GB | Fast | Very good | If speed matters more |
| phi3 | 2.3GB | 4GB | Very fast | Good | For low-RAM machines |
| qwen2.5:7b | 4.4GB | 8GB | Medium | Excellent | Great for reasoning tasks |

## Ollama vs OpenAI

**Speed:** first run is slower (loading model into RAM). After that, similar or faster since there's no network round-trip.

**Quality:** about 90-95% as good as GPT-4 for this use case. Sentiment analysis is nearly identical. Gap explanations are a bit less detailed but still solid.

**Cost:** OpenAI runs $0.10-0.30 per cycle. Ollama is free.

## System Requirements

**Minimum:** 8GB RAM, 10GB disk, 4-core CPU.

**Recommended:** 16GB RAM, 8+ cores. GPU optional but gives 5-10x speedup if you have an NVIDIA card.

**Low-end:** use `phi3` model, only needs 4GB RAM.

## GPU Acceleration

If you have an NVIDIA GPU, Ollama uses it automatically. Check with `nvidia-smi`. If it's not being used, make sure your drivers are installed.

## Troubleshooting

**"Connection refused":** `ollama serve` isn't running. Start it, then test with `curl http://localhost:11434`.

**"Model not found":** pull it first: `ollama pull llama3.1:8b`. Check with `ollama list`.

**Slow:** try a smaller model (`phi3`), reduce `MAX_CONTRACTS_PER_CYCLE`, or close other apps to free RAM.

**Out of memory:** switch to `phi3` or reduce `SENTIMENT_BATCH_SIZE` to 15.

**Bad results:** try `qwen2.5:7b` (better reasoning), or set `LLM_TEMPERATURE=0.2` for more focused output.

## Switching Between Providers

Just change one line in `.env`:

```env
# Free
LLM_PROVIDER=ollama

# Or paid
LLM_PROVIDER=deepseek
# LLM_PROVIDER=openai
```

Restart and you're on the other provider. The system handles the switch.

## Useful Commands

```bash
ollama serve              # Start server
ollama list               # Show downloaded models
ollama pull <model>       # Download a model
ollama rm <model>         # Delete a model
ollama run llama3.1:8b    # Interactive chat (for testing)
ollama show llama3.1:8b   # Model info
ollama --version          # Version check
```

## Recommended Configs

**Dev/testing:**
```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=mistral
SENTIMENT_BATCH_SIZE=25
MAX_CONTRACTS_PER_CYCLE=10
```

**Best free quality:**
```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:7b
SENTIMENT_BATCH_SIZE=50
MAX_CONTRACTS_PER_CYCLE=20
```

**Low-resource machine:**
```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=phi3
SENTIMENT_BATCH_SIZE=15
MAX_CONTRACTS_PER_CYCLE=5
```

## More Info

- Ollama: https://ollama.ai
- Models: https://ollama.ai/library
- GitHub: https://github.com/ollama/ollama
