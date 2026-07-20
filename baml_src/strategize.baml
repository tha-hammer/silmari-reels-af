function StrategizeReel(
  transcript_text: string,
  candidates: PlannerCandidate[],
  duration_policy: DurationPolicy
) -> ReelStrategy {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Build the reel strategy around ONE narrative thread, not a highlight reel.

    REQUIRED OUTPUT DECISIONS:
    - Choose ONE template whose structure fits the candidate chain, not the most exciting quote.
    - Choose ONE hook referencing exactly one candidate_id/occurrence_index.
    - Define the ArcPlan: promise, thread, completion_criteria, required_candidate_ids, optional
      candidate_ids, and any excluded candidates.
    - Propose duration_range_s as latitude for the arc, not a target to hit.
    - Choose ONE primary engagement lever.
    - Choose one CTA plan.
    - Preserve candidate identity.
    - Copy duration_policy into the output unchanged.
    - rationale MUST state why the selected template_, hook, arc, duration_range_s, engagement
      lever, and CTA plan fit the candidate chain and retention rules.

    CONTENT-DRIVEN LENGTH LATITUDE:
    - The reel is exactly as long as the content justifies to complete a coherent narrative arc.
    - Do NOT fit to a requested timecode. Do NOT pad to reach duration_policy.advisory_min_s.
    - Do NOT truncate a coherent narrative just because duration_policy.advisory_max_s is short.
    - duration_policy.effective_cap_s is the active cap. Stay under it unless a later cap cut is
      needed by ArrangeReel.
    - Use duration_range_s to estimate the coherent arc: 20-40s for one complete idea, 60-100s for
      a proof/mechanism arc, 120-175s for a source video whose complete narrative needs many
      non-duplicative beats.
    - A broad cap is not permission to sprawl. Every required candidate must answer one named
      completion criterion.
    - A long source with strong early, middle, and late candidates must not be compressed into the
      old hook/context/value/payoff/CTA five-beat skeleton by default. Split setup, mechanisms,
      examples, consequences, payoff, and loop into separate completion criteria when they are
      distinct source ideas.
    - A compact five-beat strategy is valid only when the rationale explains why candidates from
      other source windows are optional, excluded, or unrelated to the chosen complete arc.

    TEMPLATE CHOICE:
    - HookContextValuePayoffCta: default for a clear claim, context, mechanism, payoff.
    - ProblemAgitateSolve: use only when the hook names a pain/cost and the payoff states the fix.
    - BeforeAfterBridge: use only when candidates contain before state, after state, and bridge.
    - MythBust: use only when the payoff disproves a named belief.
    - Listicle: use only when candidates contain parallel numbered or named items on one topic.
    - Storytime: use only when candidates form one causal event sequence with a turn.

    HOOK TYPE CHOICE:
    - CuriosityGap: promise a specific missing answer that a payoff candidate can answer.
    - ResultFirst: use only when the payoff candidate proves a concrete outcome or metric.
    - PainPoint: use only when the hook candidate names a recognizable pain.
    - BoldClaim: use only when the payoff candidate substantiates the claim.
    - DirectCallout: use only when a specific audience is named or obvious from candidates.
    - Question: ask only a question the payoff span answers directly.
    - Number: use only when the number appears in the selected hook or payoff candidate.
    - PatternInterrupt: use only when the surprising hook is resolved by the payoff, not abandoned.

    R6 HOOK-PAYOFF CONTRACT:
    - Before choosing the hook, identify the candidate that would answer the hook promise in ArrangeReel.
    - The hook promise and payoff candidate MUST be the same topic, same tension, and same answer.
    - Reject any hook whose best payoff is merely adjacent, impressive, or from another topic.
    - The final arranged reel must make the viewer feel: "that answered what the first line promised."

    COMPLETION CONTRACT:
    - completion_criteria must name the minimum conditions for the arc to feel complete: hook
      promise, minimum context, proof/mechanism/example material, direct payoff, and R8 loop echo.
    - required_candidate_ids must include every candidate needed for those criteria.
    - optional_candidate_ids are useful support only; they are first to drop under cap pressure.
    - excluded_candidate_ids should name attractive candidates that would create a second topic,
      repeated proof, or padding.
    - Do not mark an arc complete if the selected candidates cannot resolve the hook.

    SPAN POLICY:
    - Quotes must stay VERBATIM.
    - A hook span may trim to any contiguous verbatim source-token substring inside the referenced
      PlannerCandidate.word_range.
    - A joined span is allowed only when it is one continuous source-token range that starts inside
      the referenced candidate and extends through following adjacent candidates. For joins, keep
      candidate_id/occurrence_index on the FIRST candidate in source-time order.
    - If only whole candidates are available or adjacency is unclear, design the strategy to work with
      whole candidates; do not depend on illegal stitching or invented bridge text.

    ENGAGEMENT:
    - For this A1 producer, engagement_primary MUST be Send or Share so ArrangeReel can place one
      specific R9 share cue.
    - Prefer Send when the likely cue names one concrete person or role; use Share when it names a
      concrete team, meeting, or situation.
    - The eventual cue must be topic-derived: the recipient and reason should come from the selected
      hook/payoff tension, not from generic engagement mechanics.
    - Do not choose Save, Comment, Follow, or NoEngagement.
    - Never steer ArrangeReel toward bait such as "share if you agree", "tag 5 friends", "like for
      more", vague "share this", or any cue without a concrete recipient and reason.

    FEW-SHOT CHOICE EXEMPLARS:
    - Bad R6: hook "Pay now or pay later" with payoff "10,000 lines in four days". The metric is
      impressive but does not answer what "pay" means.
    - Good R6: hook "Pay now or pay later" with payoff "You use the AI's memory to create the script
      to run the check." The payoff explains that paying now means external verification.
    - Good ResultFirst: hook "10,000 lines in four days with no regressions" only if the payoff path
      explains the mechanism that made no regressions possible.
    - Bad Listicle: three strong candidates about unrelated AI limits, CRM magic, and delivery speed.
      They are not a list unless they answer one shared promise.
    - Good R9 lever: for a reel about not letting AI check its own work, choose Send because the
      earned cue can name "the dev on your team" and the reason is "still lets the AI grade its own
      homework."
    - Banned R9 lever: do not choose Share to imply "share if you agree" or "tag 5 friends." Those
      are generic bait, not a specific distribution cue.
    - Good 25s arc: one hook, one proof, one payoff, one loop; duration_range_s 22-32s because the
      content is complete quickly.
    - Good 85s arc: hook plus context, two non-duplicative mechanisms, one consequence, payoff, and
      loop; duration_range_s 75-100s because each proof criterion needs a separate source span.
    - Good 165s arc: a long source where early setup, middle failure, late mechanism, and final
      payoff all matter; duration_range_s 140-175s, with optional branches excluded.
    - Over-cap arc: if the complete source story would exceed duration_policy.effective_cap_s, mark
      optional candidates clearly so ArrangeReel can make a coherent under-cap cut.
    - Bad short arc: five beats chosen just because older examples were short, or five compound
      criteria where each criterion hides multiple distinct mechanisms that should be separate beats.

    RATIONALE STYLE:
    - Explain the specific choice, not the rules in general.
    - Name the candidate relationship that makes the hook/payoff work.
    - Name why the duration range is enough for the arc without padding.
    - Keep rationale to 1-3 concise sentences.

    Candidates:
    {{ candidates }}

    Transcript:
    {{ transcript_text }}

    Duration policy:
    {{ duration_policy }}
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
    duration_policy {
      soft_cap_s 180.0
      effective_cap_s 180.0
      advisory_min_s 20.0
      advisory_max_s 35.0
      cap_overridden false
    }
  }
}
