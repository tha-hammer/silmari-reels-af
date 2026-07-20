function CheckScriptCoherence(
  blueprint: ReelBlueprint,
  script_beats: ScriptBeatText[],
  transitions: ScriptTransition[],
  strategy: ReelStrategy,
  candidate_contexts: CandidateTranscriptContext[],
  repair_hint: string?
) -> ScriptCoherenceReport {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Read the actual concatenated span text in order after arrangement and timecode resolution.
    This is a SCRIPT-COHERENCE pass over the edited short script, not a candidate scoring pass.

    For every transition, answer as a writing editor:
    - does beat N+1 follow from beat N?
    - is the why present or dropped?
    - would a listener understand the logical thread without having watched the missing source?
    - is this a clean idea boundary, an acceptable jump, a non-sequitur, or an unbridged jump?

    PRINCIPLES:
    - Scope stays the tightest local thread from strategy.arc. Do not switch to whole-talk synthesis.
    - value_score gates eligibility for candidate selection, but adjacency is a writing decision.
    - Never leave the listener without the why between two lines.
    - Use candidate transcript context to see the source words around each cut.
    - Do NOT hardcode bridge-vs-drop. Decide per transition whether the best fix is
      bridge / drop / reorder / trim boundary / keep.
    - Prefer contiguous or near-contiguous source spans when they read coherently.
    - A non-adjacent cut is acceptable only when the script text itself carries enough why.
    - Do not invent glue text. A bridge fix must name candidate_ids already present in the
      candidate context / arrangement candidate pool.

    OUTPUT CONTRACT:
    - Emit exactly one per-transition verdict for every item in transitions.
    - transition_index, from_beat_index, and to_beat_index MUST match the input transition.
    - coherent is true only when every transition can stay as written.
    - rationale MUST state the writing logic, the missing or present why, and the alternatives
      weighed (keep, bridge, drop, reorder, trim boundary).
    - If verdict is NeedsBridge or UnbridgedJump, set fix_action Bridge unless dropping/reordering
      clearly reads better. Include suggested_bridge_candidate_ids when a local ctx_ bridge is best.
    - If verdict is NonSequitur, choose Drop or Reorder unless a local bridge genuinely supplies
      the missing why.
    - If verdict is NeedsCleanBoundary, choose TrimBoundary and explain the cleaner cut.
    - repair_hint should be concise and directly usable by ArrangeReel. Name only the transitions
      that need repair and the chosen fix.

    Strategy:
    {{ strategy }}

    Previous repair hint:
    {{ repair_hint }}

    Blueprint:
    {{ blueprint }}

    Assembled script beats:
    {{ script_beats }}

    Script transitions:
    {{ transitions }}

    Candidate transcript context:
    {{ candidate_contexts }}

    {{ ctx.output_format }}
  "#
}

test CheckScriptCoherenceBasic {
  functions [CheckScriptCoherence]
  args {
    blueprint {
      template_ HookContextValuePayoffCta
      duration_range_s {
        min_s 20.0
        max_s 35.0
        rationale "one complete idea can resolve without padding"
      }
      duration_policy {
        soft_cap_s 180.0
        effective_cap_s 180.0
        advisory_min_s 20.0
        advisory_max_s 35.0
        cap_overridden false
      }
      arc {
        promise "They fake reasoning."
        thread "pattern matching can feel like reasoning"
        completion_criteria [
          "hook names the fake reasoning tension",
          "payoff explains what pattern matching means",
          "loop echoes the hook without reusing the source span"
        ]
        required_candidate_ids ["c001"]
        optional_candidate_ids []
        excluded_candidate_ids []
      }
      hook {
        type CuriosityGap
        banner_line "They fake it."
        span_quote "They pattern-match at a scale that feels like reasoning."
        candidate_id "c001"
        occurrence_index 0
      }
      beats [
        {
          role Hook
          span_quote "They pattern-match at a scale that feels like reasoning."
          candidate_id "c001"
          occurrence_index 0
          max_len_s 4.0
          rationale "the hook states the local premise before the consequence"
        }
        {
          role Payoff
          span_quote "you ship the bug"
          candidate_id "c002"
          occurrence_index 0
          max_len_s 3.0
          rationale "the payoff names the consequence of trusting the feeling"
        }
      ]
      loop {
        strategy "tie_final_to_hook"
        final_span_quote "you ship the bug"
        candidate_id "c002"
        occurrence_index 0
      }
      engagement_primary Send
      cta {
        hardness Soft
        placements ["end"]
      }
      completion_rationale "the hook premise and consequence satisfy the local arc"
      rationale "the beats move from false reasoning to the shipping consequence"
    }
    script_beats [
      {
        index 0
        role Hook
        candidate_id "c001"
        occurrence_index 0
        span_quote "They pattern-match at a scale that feels like reasoning."
        start_s 1.0
        end_s 5.0
        rationale "the hook states the local premise before the consequence"
      }
      {
        index 1
        role Payoff
        candidate_id "c002"
        occurrence_index 0
        span_quote "you ship the bug"
        start_s 8.0
        end_s 10.0
        rationale "the payoff names the consequence of trusting the feeling"
      }
    ]
    transitions [
      {
        index 0
        from_beat_index 0
        to_beat_index 1
        from_candidate_id "c001"
        to_candidate_id "c002"
        from_text "They pattern-match at a scale that feels like reasoning."
        to_text "you ship the bug"
        source_gap_s 3.0
        connective_text "And the moment you trust the feeling"
      }
    ]
    strategy {
      template_ HookContextValuePayoffCta
      duration_range_s {
        min_s 20.0
        max_s 35.0
        rationale "one complete idea can resolve without padding"
      }
      duration_policy {
        soft_cap_s 180.0
        effective_cap_s 180.0
        advisory_min_s 20.0
        advisory_max_s 35.0
        cap_overridden false
      }
      arc {
        promise "They fake reasoning."
        thread "pattern matching can feel like reasoning"
        completion_criteria ["hook", "payoff"]
        required_candidate_ids ["c001", "c002"]
        optional_candidate_ids []
        excluded_candidate_ids []
      }
      hook {
        type CuriosityGap
        banner_line "They fake it."
        span_quote "They pattern-match at a scale that feels like reasoning."
        candidate_id "c001"
        occurrence_index 0
      }
      engagement_primary Send
      cta {
        hardness Soft
        placements ["end"]
      }
      rationale "the local arc moves from false reasoning to consequence"
    }
    candidate_contexts []
    repair_hint null
  }
}
