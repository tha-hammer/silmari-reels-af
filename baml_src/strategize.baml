function StrategizeReel(
  transcript_text: string,
  candidates: PlannerCandidate[],
  bounds: DurationBounds
) -> ReelStrategy {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Choose ONE template, target length within [{{ bounds.min_s }}, {{ bounds.max_s }}]s, a hook
    referencing exactly one candidate_id/occurrence_index, ONE primary engagement lever, and a CTA
    plan. Preserve candidate identity.
    Candidates: {{ candidates }}
    Transcript: {{ transcript_text }}
  "#
}

test StrategizeBasic {
  functions [StrategizeReel]
  args {
    transcript_text "They don't reason. They pattern-match at a scale that feels like reasoning."
    candidates [
      {
        candidate_id "c001"
        quote "They pattern-match at a scale that feels like reasoning."
        occurrence_index 0
        word_range [3, 12]
        start_s 1.0
        end_s 5.0
        quality 1.0
        value_score 0.9
        emotion "surprise"
        is_claim true
        payoff_worthy true
      }
    ]
    bounds {
      min_s 20.0
      max_s 35.0
    }
  }
}
