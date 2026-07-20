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
  source_window_id string?
  source_window_index int?
  source_window_start_s float?
  source_window_end_s float?
  value_score float
  emotion string?
  is_claim bool?
  payoff_worthy bool?
  rationale string?
}

class PlannerCandidate {
  candidate_id string
  quote string
  occurrence_index int
  word_range int[]
  start_s float
  end_s float
  source_window_id string?
  source_window_index int?
  source_window_start_s float?
  source_window_end_s float?
  quality float
  value_score float
  emotion string?
  is_claim bool?
  payoff_worthy bool?
  rationale string?
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
  rationale string?
  completion_role string?
  completion_criterion_ids string[]?
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

class DurationPolicy {
  soft_cap_s float
  effective_cap_s float
  advisory_min_s float?
  advisory_max_s float?
  cap_overridden bool
}

class DurationRange {
  min_s float
  max_s float
  rationale string
}

class ArcPlan {
  promise string
  thread string
  completion_criteria string[]
  required_candidate_ids string[]
  optional_candidate_ids string[]?
  excluded_candidate_ids string[]?
}

class CandidateTranscriptContext {
  candidate_id string
  occurrence_index int
  start_s float
  end_s float
  before_text string?
  after_text string?
  source_neighborhood string
  prev_candidate_id string?
  next_candidate_id string?
  gap_to_prev_s float?
  gap_to_next_s float?
}

class ScriptBeatText {
  index int
  role BeatRole
  candidate_id string
  occurrence_index int
  span_quote string
  start_s float?
  end_s float?
  rationale string?
}

class ScriptTransition {
  index int
  from_beat_index int
  to_beat_index int
  from_candidate_id string
  to_candidate_id string
  from_text string
  to_text string
  source_gap_s float?
  connective_text string?
}

enum ScriptTransitionVerdict {
  Coherent @alias("coherent")
  NeedsBridge @alias("needs_bridge")
  UnbridgedJump @alias("unbridged_jump")
  NonSequitur @alias("non_sequitur")
  NeedsCleanBoundary @alias("needs_clean_boundary")
}

enum ScriptCoherenceFixAction {
  Keep @alias("keep")
  Bridge @alias("bridge")
  Drop @alias("drop")
  Reorder @alias("reorder")
  TrimBoundary @alias("trim_boundary")
}

class ScriptTransitionReview {
  transition_index int
  from_beat_index int
  to_beat_index int
  verdict ScriptTransitionVerdict
  fix_action ScriptCoherenceFixAction
  why_present bool
  rationale string
  missing_why string?
  suggested_bridge_candidate_ids string[]?
  suggested_repair string?
}

class ScriptCoherenceReport {
  coherent bool
  transitions ScriptTransitionReview[]
  overall_rationale string
  repair_hint string?
}

class ReelStrategy {
  template_ Template
  duration_range_s DurationRange
  duration_policy DurationPolicy
  arc ArcPlan
  hook Hook
  engagement_primary EngagementKind
  cta CtaPlan
  rationale string?
}

class ReelBlueprint {
  template_ Template
  duration_range_s DurationRange
  duration_policy DurationPolicy
  arc ArcPlan
  hook Hook
  beats Beat[]
  loop LoopPlan
  engagement_primary EngagementKind
  cta CtaPlan
  completion_rationale string
  cap_rationale string?
  omitted_candidate_ids string[]?
  rationale string?
}
