function ArrangeReel(
  candidates: PlannerCandidate[],
  strategy: ReelStrategy,
  repair_hint: string?
) -> ReelBlueprint {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Arrange candidates into an ordered beat list per the strategy. Each beat carries role,
    candidate_id, occurrence_index, VERBATIM span_quote, max_len_s, and at most one interrupt_out.
    If repair_hint is present, repair those exact failed quotes without changing unrelated strategy.
    Strategy: {{ strategy }}
    Repair hint: {{ repair_hint }}
    Candidates: {{ candidates }}
  "#
}

test ArrangeBasic {
  functions [ArrangeReel]
  args {
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
    strategy {
      template_ HookContextValuePayoffCta
      target_duration_s 24.0
      hook {
        type CuriosityGap
        banner_line "They fake it."
        span_quote "They pattern-match at a scale that feels like reasoning."
        candidate_id "c001"
        occurrence_index 0
      }
      engagement_primary Save
      cta {
        hardness Soft
        placements ["end"]
      }
    }
    repair_hint null
  }
}
