"""Configuration management using Pydantic settings."""

import os
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore'
    )

    # Database Configuration
    database_url: str = Field(
        default='postgresql://postgres:password@localhost:5432/polymarket_gaps',
        description='PostgreSQL connection URL'
    )
    db_pool_size: int = Field(default=20, description='Database connection pool size')
    db_max_overflow: int = Field(default=0, description='Database max overflow connections')

    # LLM Configuration
    llm_provider: str = Field(
        default='ollama',
        description='LLM provider: "openai" or "ollama" (free local models)'
    )

    # OpenAI API Configuration (only if llm_provider='openai')
    openai_api_key: Optional[str] = Field(default=None, description='OpenAI API key')
    openai_model: str = Field(
        default='gpt-4-turbo-preview',
        description='OpenAI model to use'
    )

    # Ollama Configuration (only if llm_provider='ollama')
    ollama_base_url: str = Field(
        default='http://localhost:11434',
        description='Ollama API base URL'
    )
    ollama_model: str = Field(
        default='llama3.1:8b',
        description='Ollama model to use (e.g., llama3.1:8b, mistral, phi3)'
    )

    # General LLM settings
    llm_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description='LLM temperature for generation'
    )

    # DeepSeek API Configuration (Optional - falls back to Ollama if missing)
    deepseek_api_key: Optional[str] = Field(default=None, description='DeepSeek API key')
    deepseek_model: str = Field(default='deepseek-reasoner', description='DeepSeek model name')

    # Tavily Web Search (Optional - skipped if missing)
    tavily_api_key: Optional[str] = Field(default=None, description='Tavily API key for web search')

    # Grok/xAI API for X/Twitter Sentiment (Optional - falls back to X mirror scraper)
    grok_api_key: Optional[str] = Field(default=None, description='Grok API key from console.x.ai')

    # Financial Modeling Prep (Optional - skipped for non-financial contracts)
    fmp_api_key: Optional[str] = Field(default=None, description='FMP API key for financial data')

    # Supabase (Optional - falls back to local PostgreSQL)
    supabase_url: Optional[str] = Field(default=None, description='Supabase project URL')
    supabase_key: Optional[str] = Field(default=None, description='Supabase anon key')

    # LangSmith Observability (Optional - tracing disabled if missing)
    langsmith_api_key: Optional[str] = Field(default=None, description='LangSmith API key')
    langsmith_project: str = Field(default='polymarket-gaps', description='LangSmith project name')

    # Twitter/X API Configuration (Optional)
    twitter_api_key: Optional[str] = Field(default=None, description='Twitter API key')
    twitter_api_secret: Optional[str] = Field(default=None, description='Twitter API secret')
    twitter_bearer_token: Optional[str] = Field(default=None, description='Twitter bearer token')
    twitter_access_token: Optional[str] = Field(default=None, description='Twitter access token')
    twitter_access_secret: Optional[str] = Field(default=None, description='Twitter access secret')

    # Bluesky API Configuration (Free - just needs a Bluesky account)
    bluesky_handle: Optional[str] = Field(default=None, description='Bluesky handle (e.g. user.bsky.social)')
    bluesky_app_password: Optional[str] = Field(default=None, description='Bluesky app password (Settings > App Passwords)')

    # Reddit API Configuration (Optional)
    reddit_client_id: Optional[str] = Field(default=None, description='Reddit client ID')
    reddit_client_secret: Optional[str] = Field(default=None, description='Reddit client secret')
    reddit_user_agent: str = Field(
        default='PolymarketGapDetector/1.0',
        description='Reddit user agent'
    )

    # Polymarket API Configuration
    polymarket_api_url: str = Field(
        default='https://clob.polymarket.com',
        description='Polymarket CLOB API URL'
    )
    polymarket_gamma_api_url: str = Field(
        default='https://gamma-api.polymarket.com',
        description='Polymarket Gamma API URL'
    )
    polymarket_strapi_url: str = Field(
        default='https://strapi-matic.poly.market',
        description='Polymarket Strapi URL'
    )

    # System Configuration
    polling_interval: int = Field(
        default=300,
        ge=60,
        description='Seconds between data collection cycles'
    )
    max_contracts_per_cycle: int = Field(
        default=20,
        ge=1,
        description='Max contracts to analyze per cycle'
    )
    max_contracts_for_social: int = Field(
        default=50,
        ge=1,
        description='Max contracts to fetch social/news data for (more = more coverage, more API usage)'
    )
    min_confidence_score: int = Field(
        default=25,
        ge=0,
        le=100,
        description='Minimum confidence to report a gap'
    )
    log_level: str = Field(
        default='INFO',
        description='Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)'
    )

    # Rate Limiting
    polymarket_rate_limit: int = Field(
        default=10,
        description='Polymarket API requests per minute'
    )
    twitter_rate_limit: int = Field(
        default=15,
        description='Twitter API requests per 15 minutes'
    )
    reddit_rate_limit: int = Field(
        default=60,
        description='Reddit API requests per minute'
    )
    bluesky_rate_limit: int = Field(
        default=30,
        description='Bluesky API requests per minute'
    )
    kalshi_rate_limit: int = Field(
        default=10,
        description='Kalshi API requests per second'
    )
    manifold_rate_limit: int = Field(
        default=30,
        description='Manifold Markets API requests per minute'
    )

    # Agent Configuration
    data_collection_lookback_hours: int = Field(
        default=6,
        ge=1,
        description='How far back to fetch social posts'
    )
    sentiment_batch_size: int = Field(
        default=50,
        ge=1,
        description='Posts per sentiment analysis batch'
    )
    gap_detection_threshold: float = Field(
        default=0.04,
        ge=0.0,
        le=1.0,
        description='Minimum odds difference to flag'
    )
    gap_dedupe_hours: int = Field(
        default=24,
        ge=0,
        description='Skip storing a gap if same contract+type was already detected within this many hours'
    )
    # Sentiment score (-1..1) is mapped to implied probability: 0.5 + (sentiment * scale). Default 0.4 → 0.1..0.9
    gap_sentiment_prob_scale: float = Field(
        default=0.4,
        ge=0.0,
        le=0.5,
        description='Scale from sentiment to implied probability (0.5 + sentiment * scale)'
    )
    arbitrage_min_edge: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description='Minimum cross-market price difference to flag as arbitrage'
    )

    # Scraper Configuration
    scraper_respect_robots: bool = Field(default=True, description='Always respect robots.txt')
    scraper_request_delay: float = Field(default=2.0, description='Delay between scraper requests in seconds')
    scraper_user_agent: str = Field(
        default='PolymarketResearchBot/1.0 (research purposes)',
        description='User agent for scraping requests'
    )

    # RSS Feed Configuration
    rss_feeds: Optional[str] = Field(
        default=None,
        description='Comma-separated RSS feed URLs (uses defaults if not set)'
    )

    # Feature Flags
    enable_twitter: bool = Field(default=True, description='Enable Twitter data collection')
    enable_reddit: bool = Field(default=True, description='Enable Reddit data collection')
    enable_bluesky: bool = Field(default=True, description='Enable Bluesky data collection (free, no API key)')
    enable_kalshi: bool = Field(default=True, description='Enable Kalshi cross-market comparison')
    enable_manifold: bool = Field(default=True, description='Enable Manifold Markets cross-market comparison')
    enable_tavily: bool = Field(default=True, description='Enable Tavily web search (requires API key)')
    enable_grok: bool = Field(default=True, description='Enable Grok X sentiment (requires API key)')
    enable_x_mirror: bool = Field(default=True, description='Enable X mirror scraper (free fallback for Grok)')
    enable_gdelt: bool = Field(default=True, description='Enable GDELT geopolitical news (free, no key)')
    enable_fmp: bool = Field(default=True, description='Enable FMP financial data (requires API key)')
    enable_ensemble_sentiment: bool = Field(default=True, description='Enable VADER+TextBlob ensemble sentiment')
    enable_backtesting: bool = Field(default=False, description='Enable backtesting at end of each cycle')
    enable_historical_analysis: bool = Field(
        default=True,
        description='Enable historical pattern analysis'
    )
    enable_arbitrage_detection: bool = Field(
        default=True,
        description='Enable arbitrage detection'
    )

    # Output Configuration
    console_output_width: int = Field(
        default=120,
        ge=80,
        description='Console output width in characters'
    )
    max_gaps_to_display: int = Field(
        default=10,
        ge=1,
        description='Maximum gaps to display in output'
    )

    @property
    def has_deepseek_credentials(self) -> bool:
        """Check if DeepSeek API key is configured."""
        return bool(self.deepseek_api_key)

    @property
    def has_tavily_credentials(self) -> bool:
        """Check if Tavily API key is configured."""
        return bool(self.tavily_api_key)

    @property
    def has_grok_credentials(self) -> bool:
        """Check if Grok API key is configured."""
        return bool(self.grok_api_key)

    @property
    def has_fmp_credentials(self) -> bool:
        """Check if FMP API key is configured."""
        return bool(self.fmp_api_key)

    @property
    def has_supabase_credentials(self) -> bool:
        """Check if Supabase credentials are configured."""
        return bool(self.supabase_url and self.supabase_key)

    @property
    def has_langsmith_credentials(self) -> bool:
        """Check if LangSmith API key is configured."""
        return bool(self.langsmith_api_key)

    @property
    def has_twitter_credentials(self) -> bool:
        """Check if Twitter credentials are configured."""
        return bool(self.twitter_bearer_token or (
            self.twitter_api_key and
            self.twitter_api_secret and
            self.twitter_access_token and
            self.twitter_access_secret
        ))

    @property
    def has_reddit_credentials(self) -> bool:
        """Check if Reddit credentials are configured."""
        return bool(
            self.reddit_client_id and
            self.reddit_client_secret
        )

    @property
    def has_bluesky_credentials(self) -> bool:
        """Check if Bluesky credentials are configured."""
        return bool(
            self.bluesky_handle and
            self.bluesky_app_password
        )

    def validate_required_services(self):
        """Validate that at least basic services are configured."""
        # Validate LLM configuration
        if self.llm_provider == 'deepseek':
            if not self.has_deepseek_credentials:
                print("WARNING: DeepSeek selected but no API key. Falling back to Ollama.")
                self.llm_provider = 'ollama'
            else:
                print(f"INFO: Using DeepSeek model '{self.deepseek_model}'")
        elif self.llm_provider == 'openai':
            if not self.openai_api_key:
                raise ValueError("OpenAI API key is required when llm_provider='openai'")
        elif self.llm_provider == 'ollama':
            print(f"INFO: Using Ollama with model '{self.ollama_model}' at {self.ollama_base_url}")
            print("INFO: Make sure Ollama is running: ollama serve")
        else:
            raise ValueError(f"Invalid llm_provider: {self.llm_provider}. Must be 'openai', 'ollama', or 'deepseek'")

        # Override DATABASE_URL with Supabase if credentials are present
        if self.has_supabase_credentials:
            # Supabase provides a direct PostgreSQL connection string
            # Typical format: postgresql://postgres.[ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
            supa_url = self.supabase_url
            supa_key = self.supabase_key
            # If supabase_url looks like a full postgres:// URL, use it directly
            if supa_url.startswith('postgresql://') or supa_url.startswith('postgres://'):
                self.database_url = supa_url
                print(f"INFO: Using Supabase PostgreSQL: {supa_url.split('@')[-1] if '@' in supa_url else 'configured'}")
            else:
                # If it's just the project URL (https://xxx.supabase.co), construct the connection
                # User should set DATABASE_URL directly for Supabase pooler connection
                print("INFO: Supabase URL detected but not a PostgreSQL URL.")
                print("  Set DATABASE_URL to your Supabase pooler connection string instead.")
                print("  Example: postgresql://postgres.xxxx:password@aws-0-region.pooler.supabase.com:6543/postgres")

        # Auto-disable sources without credentials
        if self.enable_twitter and not self.has_twitter_credentials:
            self.enable_twitter = False

        if self.enable_reddit and not self.has_reddit_credentials:
            self.enable_reddit = False

        if self.enable_bluesky and not self.has_bluesky_credentials:
            self.enable_bluesky = False

        if self.enable_tavily and not self.has_tavily_credentials:
            self.enable_tavily = False

        if self.enable_grok and not self.has_grok_credentials:
            self.enable_grok = False

        if self.enable_fmp and not self.has_fmp_credentials:
            self.enable_fmp = False

        # X mirror scraper is only useful when Grok is unavailable
        if self.enable_grok:
            self.enable_x_mirror = False

        # Setup LangSmith tracing if configured
        if self.has_langsmith_credentials:
            os.environ['LANGCHAIN_TRACING_V2'] = 'true'
            os.environ['LANGCHAIN_API_KEY'] = self.langsmith_api_key
            os.environ['LANGCHAIN_PROJECT'] = self.langsmith_project

        # Log all source statuses
        self._log_enabled_sources()

    def _log_enabled_sources(self):
        """Log which sources are active at startup."""
        print("\n-- Active Sources ------------------------------------------")
        sources = {
            "LLM":          self.llm_provider,
            "Tavily":       "ON" if self.enable_tavily else "OFF (no key)",
            "Grok/X":       "ON" if self.enable_grok else "OFF (no key)",
            "X Mirror":     "ON" if self.enable_x_mirror else "OFF",
            "GDELT":        "ON (free)" if self.enable_gdelt else "OFF",
            "FMP":          "ON" if self.enable_fmp else "OFF (no key)",
            "Twitter":      "ON" if self.enable_twitter else "OFF",
            "Reddit":       "ON" if self.enable_reddit else "OFF",
            "Bluesky":      "ON" if self.enable_bluesky else "OFF",
            "RSS":          "ON (free)",
            "Kalshi":       "ON (free)" if self.enable_kalshi else "OFF",
            "Manifold":     "ON (free)" if self.enable_manifold else "OFF",
            "Ensemble":     "ON" if self.enable_ensemble_sentiment else "OFF",
            "Supabase":     "ON" if self.has_supabase_credentials else "OFF (local PostgreSQL)",
            "LangSmith":    "ON" if self.has_langsmith_credentials else "OFF",
            "Backtesting":  "ON" if self.enable_backtesting else "OFF",
        }
        for source, status in sources.items():
            print(f"  {source:15s} {status}")
        print("------------------------------------------------------------\n")


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Get or create global settings instance.

    Returns:
        Settings: Application settings
    """
    global _settings
    if _settings is None:
        # Look for .env file in project root
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        if os.path.exists(env_path):
            _settings = Settings(_env_file=env_path)
        else:
            _settings = Settings()

        # Validate configuration
        _settings.validate_required_services()

    return _settings


def reload_settings():
    """Reload settings from environment."""
    global _settings
    _settings = None
    return get_settings()


def get_llm():
    """
    Get configured LLM instance based on settings.

    Supports: 'deepseek' (primary reasoning), 'openai', 'ollama' (free local fallback).
    DeepSeek uses the OpenAI-compatible API format.

    Returns:
        LLM instance
    """
    settings = get_settings()

    if settings.llm_provider == 'deepseek':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.deepseek_model,
            temperature=settings.llm_temperature,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
            max_tokens=4096
        )
    elif settings.llm_provider == 'openai':
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key
        )
    elif settings.llm_provider == 'ollama':
        try:
            from langchain_community.llms import Ollama
            return Ollama(
                model=settings.ollama_model,
                base_url=settings.ollama_base_url,
                temperature=settings.llm_temperature
            )
        except ImportError:
            raise ImportError(
                "langchain-community is required for Ollama. "
                "Install with: pip install langchain-community"
            )
    else:
        raise ValueError(f"Invalid llm_provider: {settings.llm_provider}")


def get_fast_llm():
    """
    Get a fast/cheap LLM for simple classification tasks.
    Always uses Ollama to avoid burning API credits on trivial work.

    Returns:
        Ollama LLM instance
    """
    settings = get_settings()
    try:
        from langchain_community.llms import Ollama
        return Ollama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=0.1
        )
    except ImportError:
        # If Ollama not available, fall back to primary LLM
        return get_llm()
