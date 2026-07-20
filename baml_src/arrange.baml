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
    completion_rationale MUST explain how the beat list satisfies strategy.arc.completion_criteria.
    rationale MUST explain why this beat order resolves the strategy, why the payoff lands where it
    does, and why the final R8 loop span is the right distinct source echo.

    NON-NEGOTIABLE STORY CONTRACT:
    - The ordered beats MUST follow one narrative thread from the hook candidate to a payoff span
      that answers the hook promise.
    - The payoff is not "the most impressive quote"; it is the span that resolves the tension created
      by strategy.hook.banner_line and strategy.hook.span_quote.
    - Drop strong but unrelated candidates when they do not move this single thread forward.
    - Do not mix three topics just because all candidates are individually good.
    - Every beat must satisfy one strategy.arc.completion_criteria item, bridge two named criteria,
      or provide non-duplicative proof/mechanism for the payoff.
    - Do not add repeated examples, optional-only branches, or recap beats just to make the reel
      longer.
    - Do not default to the old five-beat hook/context/value/payoff/CTA skeleton. If one
      completion_criteria item contains several distinct source ideas, split those ideas into
      separate beats when each one is required for coherence.

    CONTENT-DRIVEN LENGTH AND CAP:
    - First select every beat required to complete strategy.arc.
    - Then estimate total duration from max_len_s plus explicit black/transition durations.
    - If the complete arc exceeds strategy.duration_policy.effective_cap_s, reduce to the strongest
      coherent under-cap cut by dropping optional candidates, repeated proof, and lower-value
      support first.
    - Never drop the hook promise, minimum context, proof/mechanism required for credibility,
      direct payoff, or distinct R8 loop just to fit a shorter advisory preference.
    - If you omit candidates under cap pressure, fill omitted_candidate_ids and cap_rationale.
    - The reel may be shorter than strategy.duration_policy.advisory_min_s when the content is
      already complete. Never pad.

    BEAT ORDER THAT BUILDS:
    - Hook: first beat only, use strategy.hook candidate_id/occurrence_index and span_quote or a
      VERBATIM substring of that same candidate.
    - Context: the minimum setup needed to understand the promise.
    - Value: mechanism, contrast, or stakes that narrows the answer.
    - Payoff: the beat that directly answers the hook promise. It should land after the setup, not
      appear as an unrelated flex.
    - Cta/loop: final beat echoes the hook idea so the ending loops back cleanly without
      replaying the identical source clip.

    R3 PACING ESCALATION:
    - Start with a hook max_len_s <= 3.5.
    - Middle beats may breathe, but every beat longer than 5s needs a cutin or interrupt_out.
    - For short reels, the back half should tighten toward the payoff.
    - For long reels, use sectional pacing instead of strict monotone duration: intro establishes
      the promise, middle advances proof without low-change repetition, and payoff-approach beats
      have higher change density or shorter unresolved median length than the middle.
    - Use a stronger interrupt immediately before the payoff when the source topic turns or stakes rise.
    - Do not place transitions mechanically after every beat.

    R8 LOOP TIE-BACK:
    - The final beat MUST echo strategy.hook.span_quote while using a source span DISTINCT from
      the hook beat. Never reuse the identical hook source clip as the final loop.
    - The final beat MUST NOT reuse strategy.hook candidate_id/occurrence_index when that would
      duplicate the hook span. Prefer a different candidate_id/occurrence_index whose start_s/end_s
      are also different from the hook candidate's start_s/end_s.
    - If the same phrase recurs in the source, choose the OTHER occurrence for the final loop.
      A repeated phrase is good craft only when it is cut from a distinct utterance.
    - If no distinct recurrence exists, end on a different payoff-adjacent span with high token
      overlap or the same idea instead of duplicating the hook source interval.
    - loop.final_span_quote MUST match the final beat span_quote, and loop.candidate_id plus
      loop.occurrence_index MUST match that final distinct beat.
    - If the payoff uses a different candidate, place payoff immediately before the distinct loop echo.

    R9 SHARE CUE:
    - Emit exactly ONE engagement object across all beats.
    - That one engagement MUST be kind Send or Share, primary true, with a line that tells the
      viewer exactly WHO to send/share it to and WHY that person needs it.
    - The line must be earned from the selected reel topic, not a generic growth hack. Tie the
      recipient and reason to the hook/payoff tension, e.g. "Send this to the dev on your team who
      still lets the AI grade its own homework."
    - Output engagement_primary as Send or Share to match that cue, even if strategy.engagement_primary
      says Save.
    - Keep exactly one primary engagement. Do not stack a CTA, save prompt, comment prompt, or second
      share cue on another beat.
    - BANNED R11 bait: "share if you agree", "share this", "tag 5 friends", "like for more",
      "comment below", "follow for more", vague saves, or any cue without a concrete recipient
      and reason.
    - The share cue belongs on the payoff beat unless it would crowd the line; otherwise put it on
      the final loop beat.

    INTERRUPT AND CUT-IN PLACEMENT:
    - Use Join only when adjacent beats continue the same sentence or idea with no visual reset AND
      the next beat starts later in source time. Never Join into an earlier source span.
    - Use Trans, not Join, for any non-adjacent source jump, reordered source-time jump, topic turn,
      or beat whose candidate start_s is earlier than the prior beat's candidate start_s.
    - Use Trans with a concrete effect for topic turns, rising stakes, or the move into payoff.
    - Use Black only for a hard reset or tonal snap; never as filler.
    - Scale interrupt density to beat count and story turns. Long reels may need more than 2 or 3
      interrupts, but every interrupt must mark a real source jump, topic turn, stakes rise, or
      payoff move.
    - Leave breathing room where the source already carries the beat; do not use transitions as
      filler.
    - The strongest interrupt should be immediately before the payoff. The final loop beat should
      normally have interrupt_out null.
    - A cutin should clarify a number, mechanism, or contrast already present in the VERBATIM span.
    - Keep cutin offset_s and dur_s inside the containing beat.

    SPAN POLICY:
    - Every span_quote must be VERBATIM from its referenced candidate.
    - Single-candidate trim: span_quote may be any contiguous verbatim source-token substring inside
      the referenced PlannerCandidate.word_range.
    - Adjacent span join: span_quote may be one continuous source-token range that starts inside the
      referenced candidate and extends through following adjacent candidates only when each next
      candidate starts at previous.word_range[-1] + 1.
    - For joined spans, keep candidate_id and occurrence_index set to the FIRST candidate in
      source-time order. Do not use the later candidate's identity.
    - Do not skip source words inside the emitted quote. No added words, no reworded words, and no
      gap words outside covered adjacent candidates.
    - If only whole candidates are available or adjacency is unclear, degrade gracefully: use whole
      candidate quotes, split ideas across multiple beats, and never invent glue text.

    FEW-SHOT ARRANGEMENT EXEMPLARS:
    - Bad: Hook c010 occurrence 0 "Pay now or pay later" at start_s 366.185 -> "AI is not a wonder
      box" -> "200-250 commands" -> "10,000 lines in four days" -> final echo c010 occurrence 0
      "Pay now or pay later." This duplicates the hook source span and the metric does not resolve
      what "pay now" means.
    - Good: Hook c010 occurrence 0 "pay now or you pay later" at start_s 366.185 -> context
      "The AIs are stupid" -> mechanism "You don't let the AI make the check in its own memory" ->
      payoff "use the AI's memory to create the script to run the check" -> final echo c027
      occurrence 0 "Pay now or pay later." at start_s 385.060. One thread: cost means verification,
      and the loop uses the distinct standalone utterance.
    - Bad: Payoff first, then context, then a new claim. The viewer gets the answer before the
      question and the ending cannot loop.
    - Good: Context narrows the question, mechanism raises confidence, payoff answers the hook, final
      echo makes the first line feel inevitable.
    - Banned R9 bait: Payoff beat engagement { kind Share, primary true, line "Share this if you
      agree." }. It gives no concrete recipient and no earned reason.
    - Good R9 cue: Payoff beat engagement { kind Send, primary true, line "Send this to the dev on
      your team who still lets the AI grade its own homework." }. One recipient, one reason, derived
      from the AI-checking-its-own-work payoff.
    - Good medium arc: 14 beats, 82s. Hook promises why AI self-checking fails; context explains the
      failure mode; three proof beats add distinct mechanisms; payoff gives the external-check loop;
      final loop echoes the hook from a later candidate. No filler.
    - Good long arc: 24 beats, 166s. Early setup, middle failure cascade, late repair mechanism, and
      final payoff all satisfy named completion criteria. Optional anecdotes are excluded.
    - Bad long arc: 24 beats where eight beats repeat the same "teams ship bugs" idea. Repetition is
      padding unless each quote adds a new mechanism, consequence, or proof.
    - Bad short arc: 5 beats ending before the proof candidate, even though strategy.arc.required_candidate_ids
      names the missing proof. Short is not better when the hook remains unresolved.
    - Bad compressed long-source arc: 5 beats where the value beat hides setup, mechanism, proof, and
      consequence from four distinct candidates. That is old skeleton bias; split the required ideas.

    RATIONALE STYLE:
    - Explain the ordered story arc from hook to payoff.
    - Name how the beats satisfy strategy.arc.completion_criteria.
    - Name the loop candidate_id/occurrence_index and why it is distinct from the hook source span.
    - Mention the one R9 cue placement if present.
    - Mention cap_rationale only when the complete arc needed an under-cap cut.
    - Keep rationale to 2-4 concise sentences.

    Strategy:
    {{ strategy }}

    Repair hint: {{ repair_hint }}

    Candidates:
    {{ candidates }}
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
      engagement_primary Save
      cta {
        hardness Soft
        placements ["end"]
      }
      rationale "the arc is complete when the pattern-match claim explains the hook and loops cleanly"
    }
    repair_hint null
  }
}
