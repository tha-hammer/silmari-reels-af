retry_policy PlannerRetry {
  max_retries 2
  strategy {
    type exponential_backoff
    delay_ms 200
    multiplier 2
    max_delay_ms 2000
  }
}

client<llm> PlannerLLM {
  provider openrouter
  retry_policy PlannerRetry
  options {
    model "anthropic/claude-sonnet-5"
    temperature 0.4
    api_key env.OPENROUTER_API_KEY
  }
}

client<llm> PlannerBackupLLM {
  provider openrouter
  retry_policy PlannerRetry
  options {
    model "openai/gpt-5-mini"
    temperature 0.2
    api_key env.OPENROUTER_API_KEY
  }
}

client<llm> PlannerFallback {
  provider fallback
  retry_policy PlannerRetry
  options {
    strategy [
      PlannerLLM
      PlannerBackupLLM
    ]
  }
}
