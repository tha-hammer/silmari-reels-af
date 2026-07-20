function MineCandidates(transcript_text: string, register: string) -> CandidateSpan[] {
  client PlannerFallback
  prompt #"
    {{ RetentionRules() }}
    {{ _.role("user") }}
    Register: {{ register }}

    Mine CandidateSpan objects that can carry a content-driven reel. Quote VERBATIM.
    approx_start_s/approx_end_s are optional hints, never authoritative timecodes.
    The caller may pass one source window rather than the whole transcript; still judge spans by
    source value, not by filling a quota. Across windows, the planner will preserve whole-source
    coverage, so do not cluster around only one moment when the transcript contains stronger
    early/middle/late material.

    Selection bar:
    - Prefer spans that are BOTH hook-worthy and payoff-worthy.
    - Hook-worthy means the span creates immediate tension: contrarian claim, concrete pain,
      surprising result, high-stakes consequence, crisp question, or pattern interrupt.
    - Payoff-worthy means the span answers the hook, proves the claim, names the practical move,
      exposes the consequence, or lands the memorable reversal.
    - Reject filler, hedging, throat-clearing, greetings, transitions, meta setup, and setup with
      no payoff. Do not include "I think", "kind of", "maybe", "before I get into it", "you know",
      "so basically", or similar padding unless the surrounding verbatim quote is still the payoff.
    - Reject generic statements that could appear in any video, summary sentences with no concrete
      consequence, and context-only spans that need another missing sentence to matter.
    - Never emit low-value spans just to fill the list.
    - Never pad candidate output to satisfy length. A complete reel can be short, medium, or long,
      depending on what the source actually supports.

    value_score must use the full 0-1 range:
    - 0.90-1.00: reel anchor; strong hook/payoff, memorable language, concrete consequence.
    - 0.75-0.89: strong supporting beat; useful claim, example, or setup with clear payoff nearby.
    - 0.55-0.74: only keep if needed as concise bridge context.
    - Below 0.55: reject; do not output it.

    Field guidance:
    - is_claim true for falsifiable claims, causal claims, numbers, warnings, or recommendations.
    - payoff_worthy true only when the quote resolves tension or gives the usable takeaway.
    - emotion should be the audience reaction created by the quote. Use concise lowercase labels
      such as surprise, urgency, skepticism, frustration, relief, curiosity, conviction, alarm,
      contrarian, practical, or reflective. Use plain only for rare high-value neutral context.
    - rationale must explain why this exact span was selected and why its value_score, emotion,
      is_claim, and payoff_worthy fields fit the transcript. Keep it concise and evidence-based.

    Few-shot span selection examples:
    Example 1 transcript:
    "Okay so before I get into it, the issue is most teams let the model check its own work.
    That feels efficient until it ships the mistake. Use the model to write the check, then run
    the check outside the model."
    BAD reject: "Okay so before I get into it" -> throat-clearing with no payoff.
    GOOD keep: "most teams let the model check its own work" -> value_score 0.82, emotion
    "skepticism", is_claim true, payoff_worthy false, rationale "clear skeptical setup but the
    fix/payoff lands in the next sentence."
    GOOD keep: "Use the model to write the check, then run the check outside the model." ->
    value_score 0.97, emotion "practical", is_claim true, payoff_worthy true, rationale
    "actionable mechanism that resolves the setup."

    Example 2 transcript:
    "I have been thinking about this a lot, and maybe this is too spicy, but AI is not a wonder
    box. You either pay now with process or pay later with regressions."
    BAD reject: "I have been thinking about this a lot, and maybe this is too spicy" -> hedging
    and setup without value.
    GOOD keep: "AI is not a wonder box" -> value_score 0.90, emotion "skepticism", is_claim true,
    payoff_worthy false, rationale "sharp contrarian hook without the consequence yet."
    GOOD keep: "You either pay now with process or pay later with regressions." -> value_score
    0.96, emotion "urgency", is_claim true, payoff_worthy true, rationale "memorable tradeoff
    with a clear cost/payoff."

    Example 3 transcript:
    "The team shipped on Friday. It was fine. Then support woke up to 200 tickets because no one
    ran the parser against real transcripts."
    BAD reject: "The team shipped on Friday. It was fine." -> context-only setup with no payoff.
    GOOD keep: "support woke up to 200 tickets because no one ran the parser against real
    transcripts." -> value_score 0.93, emotion "alarm", is_claim true, payoff_worthy true.

    Example 4 long-form source windows:
    Window A: "The audit failed because the demo only used friendly transcripts."
    Window B: "The second failure was worse: the planner found proof but never linked it to the
    payoff."
    Window C: "The fix was not a longer reel. The fix was requiring every beat to pay off one
    named criterion."
    GOOD keep from A, B, and C when each span supplies a different required part of one arc.
    BAD reject: five near-identical examples from Window B when A and C contain the setup and
    payoff. That clustering makes the arc incomplete.

    Negative long-form example:
    BAD keep: "another team had the same problem" repeated three times from different moments.
    Repetition is not proof unless each quote adds a new mechanism, consequence, or payoff.

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
