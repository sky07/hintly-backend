"""Configuration management using pydantic-settings."""

from typing import Dict, Literal, Optional
from pydantic_settings import BaseSettings


# ── A/B switch for the TPM prompt ───────────────────────────────────────────
# Change this ONE line to flip every TPM role between the legacy long prompt
# and the new optimized V2 prompt. Both variants live in services/prompts.py.
#   "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_TPM"  → legacy (long, expensive)
#   "PERSONALIZED_TPM_PROMPT_V2"                        → optimized (short, structured)
TPM_PROMPT_VARIANT = "PERSONALIZED_TPM_PROMPT_V2"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database settings
    database_url: str  # required: set DATABASE_URL
    database_echo: bool = False  # Set to True for SQL query logging

    # Auth settings
    jwt_secret_key: str  # required: set JWT_SECRET_KEY (openssl rand -hex 32)
    jwt_expire_minutes: int = 10080  # 7 days
    jwt_algorithm: str = "HS256"

    # Google OAuth settings
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    google_redirect_uri: str = "https://hintly.tech/auth/callback"

    # Encryption for API keys (Fernet key)
    encryption_key: Optional[str] = None  # Fernet key

    # LLM Provider configuration
    llm_provider: Literal["openai", "openai-adaptive", "gemini", "adaptive", "anthropic", "mock"] = "mock"

    # OpenAI settings
    openai_api_key: str = ""

    # Gemini settings
    gemini_api_key: str = ""

    # Groq settings (for Adaptive provider)
    groq_api_key: str = ""

    # Anthropic settings
    anthropic_api_key: str = ""

    # Rate limiting settings (for Gemini free tier testing)
    dev_mode: bool = True  # Enable rate-limit-safe testing mode (disable for faster responses)
    rate_limit_rpm: int = 4  # Requests per minute (4 for free tier with buffer)
    audio_buffer_seconds: int = 3  # Seconds of audio to buffer (reduced for faster responses)

    # Deepgram streaming STT (feature flag)
    use_deepgram_stt: bool = True  # Feature flag: use Deepgram Nova-2 instead of Whisper
    deepgram_api_key: str = ""  # Server-side Deepgram API key

    # Semantic turn detection settings
    turn_detection_enabled: bool = True  # Enable semantic turn detection (reduces jittery suggestions)
    turn_silence_threshold_ms: int = 1000  # Silence duration to consider turn complete (ms)
    turn_min_words: int = 3  # Minimum words for a valid turn

    # Transcript grouping settings (for visual separation in UI)
    # Option A (legacy): Use transcript arrival time - less accurate due to audio buffering
    # Option B1 (recommended): Use frontend VAD speech timing - more accurate
    use_speech_timing_for_turns: bool = True  # True = B1 (VAD timing), False = A (transcript timing)
    speech_silence_threshold_ms: int = 1500  # Silence duration to consider new turn (ms) - for B1

    # MongoDB settings (for interview question bank)
    mongodb_uri: Optional[str] = None
    mongodb_db_name: str = "interview_assistant"

    # Resend email settings
    resend_api_key: Optional[str] = None
    email_from: str = "Hintly <contact@mailing.hintly.tech>"
    support_email: str = "interviewassistant1999@gmail.com"
    email_verification_expiry_hours: int = 24
    frontend_url: str = "https://hintly.tech"

    # PhonePe payment gateway settings
    phonepe_merchant_id: str = ""
    phonepe_salt_key: str = ""
    phonepe_salt_index: str = "1"
    phonepe_base_url: str = "https://api-preprod.phonepe.com/apis/pg-sandbox"  # Use production URL in prod
    backend_url: str = "http://localhost:8000"

    # Credit system settings
    free_trial_minutes: int = 10
    free_trial_expiry_days: int = 7
    credit_deduction_interval_seconds: int = 30
    credit_grace_period_seconds: int = 60

    # Sentry error monitoring
    NEXT_PUBLIC_SENTRY_DSN: str = ""
    SENTRY_ORG: str = "hintly-ev"
    SENTRY_PROJECT: str = "hintly-frontend"
    SENTRY_AUTH_TOKEN: str = ""
    
    sentry_dsn: Optional[str] = None

    # Compact verbosity: tighter token limits & stricter length instructions
    # Set USE_COMPACT_VERBOSITY=true in env to activate
    use_compact_verbosity: bool = False
    USE_COMPACT_VERBOSITY: bool = False

    # Posthog monitoring
    NEXT_PUBLIC_POSTHOG_KEY: str = ""

    # General settings
    allowed_origins: str = "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,https://interview-assistant-frontend-phi.vercel.app,https://hintly.tech,https://www.hintly.tech"

    # Prompt template overrides (for A/B testing different prompts)
    # Set these to the variable name defined in prompts.py
    # e.g., "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CLAUDE" or "PERSONALIZED_GENERATED_SYSTEM_PROMPT"
    personalized_prompt_template: str = "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE"  # Default fallback
    coach_prompt_template: str = "COACH_MODE_PROMPT_CLAUDE"

    # Role-based prompt template mapping (role_type → prompt template variable name in prompts.py)
    # SWE-related roles use the SWE prompt, TPM roles use TPM_PROMPT_VARIANT (A/B switch at top of file),
    # others use Claude prompt.
    role_prompt_mapping: Dict[str, str] = {
        "software_engineer": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "senior_software_engineer": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "senior_swe_l3_l5": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "staff_engineer": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "data_scientist": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "data_engineer": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "machine_learning_engineer": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "engineering_manager": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CHATGPT_SWE",
        "technical_program_manager": TPM_PROMPT_VARIANT,
        "senior_technical_program_manager": TPM_PROMPT_VARIANT,
        "product_manager": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CLAUDE",
        "other": "PERSONALIZED_GENERATED_SYSTEM_PROMPT_CLAUDE",
    }

    # ── Conversation Intelligence feature flags ──
    # All default to False for safe rollback. Enable via env vars.
    enable_conversation_memory: bool = True      # Track conversation history in session
    enable_intent_classification: bool = True     # 7-category intent classification
    enable_adaptive_tokens: bool = True           # Dynamic max_tokens per intent
    enable_challenge_strategy: bool = True        # Challenge/pushback response strategy
    enable_conversation_arc: bool = True          # Interview phase tracking
    conversation_memory_turns: int = 5             # Number of recent turns in Tier 1 buffer

    # Legacy setting for backwards compatibility
    use_mock_openai: bool = True  # Deprecated: use llm_provider instead

    @property
    def clean_frontend_url(self) -> str:
        """Frontend URL with trailing slashes/paths stripped to prevent broken email links."""
        return self.frontend_url.rstrip('/')

    @property
    def origins_list(self) -> list[str]:
        """Parse comma-separated origins into a list."""
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def effective_provider(self) -> str:
        """Get the effective provider, considering legacy settings."""
        # If llm_provider is explicitly set to something other than mock, use it
        if self.llm_provider != "mock":
            return self.llm_provider
        # Fall back to legacy use_mock_openai setting
        if not self.use_mock_openai:
            return "openai"
        return "mock"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
