function MineCandidates(transcript_text: string, register: string) -> CandidateSpan[] {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Register: {{ register }}
    Mine quotable high-value spans. Quote VERBATIM. approx_start_s/approx_end_s are optional hints,
    never authoritative timecodes. Score value 0-1; flag claims and payoff-worthy spans.
    --- TRANSCRIPT ---
    {{ transcript_text }}
    ---
  "#
}

test MineBasic {
  functions [MineCandidates]
  args {
    transcript_text "They don't reason. They pattern-match at a scale that feels like reasoning."
    register "educational"
  }
  @@assert(nonempty, {{ this|length > 0 }})
}
