enum Template {
  HookContextValuePayoffCta @alias("hook_context_value_payoff_cta")
  ProblemAgitateSolve @alias("problem_agitate_solve")
  BeforeAfterBridge @alias("before_after_bridge")
  MythBust @alias("myth_bust")
  Listicle @alias("listicle")
  Storytime @alias("storytime")
}

enum HookType {
  CuriosityGap @alias("curiosity_gap")
  BoldClaim @alias("bold_claim")
  DirectCallout @alias("direct_callout")
  ResultFirst @alias("result_first")
  Question @alias("question")
  PainPoint @alias("pain_point")
  Number @alias("number")
  PatternInterrupt @alias("pattern_interrupt")
}

enum BeatRole {
  Hook @alias("hook")
  Context @alias("context")
  Value @alias("value")
  Payoff @alias("payoff")
  Cta @alias("cta")
}

enum InterruptKind {
  Trans @alias("trans")
  Join @alias("join")
  Black @alias("black")
}

enum EngagementKind {
  Send @alias("send")
  Save @alias("save")
  Share @alias("share")
  Comment @alias("comment")
  Follow @alias("follow")
  NoEngagement @alias("none")
}

enum CutInKind {
  Zoom @alias("zoom")
  Visual @alias("visual")
}

enum CtaHardness {
  Soft @alias("soft")
  Medium @alias("medium")
  Hard @alias("hard")
  NoCta @alias("none")
}

enum XfadeEffect {
  Dissolve @alias("dissolve")
  Smoothleft @alias("smoothleft")
  Smoothright @alias("smoothright")
  Smoothup @alias("smoothup")
  Smoothdown @alias("smoothdown")
  Hblur @alias("hblur")
  Circleopen @alias("circleopen")
  Radial @alias("radial")
  Pixelize @alias("pixelize")
  Fadeblack @alias("fadeblack")
  Fadewhite @alias("fadewhite")
  Fade @alias("fade")
  NoEffect @alias("none")
}

class CandidateSpan {
  quote string
  approx_start_s float?
  approx_end_s float?
  value_score float
  emotion string?
  is_claim bool?
  payoff_worthy bool?
}

class PlannerCandidate {
  candidate_id string
  quote string
  occurrence_index int
  word_range int[]
  start_s float
  end_s float
  quality float
  value_score float
  emotion string?
  is_claim bool?
  payoff_worthy bool?
}

class Hook {
  type HookType
  banner_line string
  span_quote string
  candidate_id string
  occurrence_index int
}

class Interrupt {
  kind InterruptKind
  effect XfadeEffect?
  dur_s float?
}

class CutIn {
  type CutInKind
  offset_s float
  dur_s float
  line string?
  image_prompt string?
  zoom_focus string?
}

class Engagement {
  kind EngagementKind
  line string?
  primary bool?
}

class Beat {
  role BeatRole
  span_quote string
  candidate_id string
  occurrence_index int
  max_len_s float
  cutin CutIn?
  interrupt_out Interrupt?
  engagement Engagement?
}

class LoopPlan {
  strategy string
  final_span_quote string
  candidate_id string
  occurrence_index int
}

class CtaPlan {
  hardness CtaHardness
  placements string[]?
}

class DurationBounds {
  min_s float
  max_s float
}

class ReelStrategy {
  template_ Template
  target_duration_s float
  hook Hook
  engagement_primary EngagementKind
  cta CtaPlan
}

class ReelBlueprint {
  template_ Template
  target_duration_s float
  hook Hook
  beats Beat[]
  loop LoopPlan
  engagement_primary EngagementKind
  cta CtaPlan
}
