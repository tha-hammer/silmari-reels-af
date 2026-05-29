# reel-af — Viral Reel Research (2025-2026)

Two parallel research deliverables on what actually makes short-form video work, gathered from creator blogs, growth marketing sources, and platform analyses. Used to inform the upcoming architecture redesign.

- **Part 1 — Script structures & narration patterns** (hook patterns, frameworks, yapping, retention loops, closes, length, science-specific)
- **Part 2 — On-screen text conventions** (captions vs subtitles, muted viewing, karaoke captions, keyword highlighting, fonts, position, tools)

Each part ends with a "what we should actually use" recommendation. A cross-cutting synthesis is at the top.

---

## Cross-cutting synthesis (most important — read first)

Five findings that fall out when you read both deliverables together:

### 1. The hook does ~80% of the work. Structure does ~15%. CTA does ~5%.
Every script-research source converges on this distribution. Every "framework" (PAS, AIDA, BAB, Hormozi Hook-Retain-Reward, listicle, story arc, contrarian) is a flavor of one primitive: **Hook → Tension → Payoff → Loop**. They differ only in how tension is loaded and resolved. Spending compute on choosing between frameworks is investing in the wrong axis.

### 2. The "contrapuntal caption" we ship today is a misread of Hormozi.
Hormozi does NOT use contrapuntal captions. His captions are the **verbatim transcript** of his voiceover, word-by-word, with the **editorial decision being which word to color yellow**. MrBeast does the same with 2-3 words per card and a brand-color accent. The "contrapuntal" idea (caption ADDS info instead of echoing the voice) is real, but it's a Layer 2 *on top of* verbatim subtitles, not a replacement for them.

**80-85% of viewers watch muted.** Without burned-in verbatim subtitles, the majority of viewers cannot follow the dialogue. The current pipeline ships an editorial caption per scene as the *only* on-screen text. That is a P0 product bug for muted viewers.

### 3. Both research streams independently call for collapsing the architecture.
Script research: "kill the router + 2-architecture system; build one structure parameterized by hook variant." Text research: "stop trying to be clever with captions; burn verbatim subtitles + accent overlay on top." The current pipeline has four sources of LLM cleverness (router, arch-I 8-hook fan-out + pairwise pick, arch-F exemplar clone, contrapuntal caption rewriter). The research says you need approximately one (hook generation) plus verbatim subtitles auto-aligned to TTS timing plus occasional accent overlays constrained to a 6-pattern menu.

### 4. TTS gives you word-level timing for free.
Gemini 3.1 Flash TTS (already integrated) returns word-level timestamps as part of the audio output. The pipeline currently does silence detection to split audio per scene — a workaround. It could be doing **word-level forced alignment** directly from the TTS output, which is what drives karaoke subtitles natively. This is both a simplification AND an upgrade: per-word timing enables the dominant viral caption style, removes the silence-detection heuristic, and replaces it with a deterministic data flow.

### 5. The ONE routing axis worth keeping is `scientific` vs `general`.
Both research streams independently call out science Shorts as a distinct mode: slower WPM (120-140 vs 150-170), authority-framed hook, payoff-first reverse structure, mechanism-not-opinion in the body. This is a `content_mode` flag that gates prompt blocks (writing guide + WPM + hook variant default), NOT a separate architecture. The existing `content_mode: Literal["general", "scientific"]` axis from `distill` is already doing exactly the right thing — keep it, drop the rest.

---

## Part 1 — Script structures & narration patterns

### Opening hooks (first 1-3 seconds)

The "3-second rule" is the single most repeated data point across sources: ~50-60% of viewers who drop off do so inside the first 3 seconds. Facebook's own data: 65% of viewers who survive the first 3 seconds watch at least 10s; 45% watch 30s+. Instagram explicitly uses 3-second hold as an early ranking signal. **Target: 3-second retention ≥ 70% or your hook failed.**

Hook patterns that recur across 5+ sources:

| Hook | What it is | When it wins |
|---|---|---|
| **Pattern interrupt (visual)** | Jump cut, snap-zoom, on-screen text slam in frame 1 | Entertainment, hard-to-telegraph topics. +23% retention when used in first 5s |
| **Shock stat / surprising number** | Specific number or counter-intuitive data point | Science, business, education |
| **Contrarian / negative hook** | "Stop doing X" / "Everything you know about X is wrong" | Anything with received wisdom to attack. 1.3-1.8x higher hook-rate than positive framing |
| **Contradiction + promise** | Bold counter-claim + immediate "I'll prove it" | Educational content with a reveal coming |
| **Curiosity gap / open loop** | State an outcome, withhold the mechanism | Storytelling, transformation. +32% watch-time with open loops |
| **Question + visual proof** | Ask, then immediately show the result | Science explainers, demos |
| **Listicle hook** | "3 things..." / "5 reasons..." | Explainer/tip content. N=3 beats N=10 on completion |
| **Authority hook** | Lead with credential or expert framing | Science / medical / technical |
| **Reverse / "payoff first"** | Show the end result, then explain how | Transformations, science |
| **POV / pattern-of-life** | "POV: you just realized..." | TikTok-native, casual modes |

**What kills hooks** (every source agrees): "Hey guys, welcome back," logos, slow context, vague hooks like "this changed everything" (no specificity → no curiosity), bait-and-switch where the body doesn't deliver.

### Narration frameworks

Six frameworks creators actually use. All collapse to one underlying shape: **Hook → Tension/Body → Payoff → Loop/CTA**.

- **Hormozi: Hook → Retain → Reward.** Distinctive move: every piece must reward the *consumer*, not the *creator*. Fits all lengths.
- **PAS (Problem-Agitate-Solution).** Most versatile, consistently converting across platforms. Welsh's PAIPS variation adds "Intrigue" to soften the mechanical feel.
- **AIDA.** Works for 30-60s ads, longer consideration cycles, ends in CTA.
- **BAB (Before-After-Bridge).** Visible transformations. +120% engagement vs other formats for transformation content (Atlabs Oct 2025).
- **Listicle / numbered explainer.** Each item is a mini hook-payoff loop. 3-5 items beats 10. Self-pacing.
- **Story arc / setup-punchline.** The setup must NOT be satisfying on its own — it loads the slingshot.
- **Contrarian explainer.** Open with counter-claim, justify with one mechanism + one proof, close with reframe.

**Length sweet spots:** PAS/listicle 15-30s; AIDA/BAB 30-45s; story arc with contrarian 30-60s; Hormozi works at any length.

### "Yapping" / unscripted-feeling content

Dominant TikTok mode in 2025. Defining traits:

- Single talking head, casual framing, original audio, minimal cuts. No B-roll, no trending sounds, no transitions.
- **Counterintuitive performance:** highest-performing yapping videos beat the same creator's "growth-hacked" videos.
- **NOT structureless.** It's hook + opinion + supporting reasoning. The "selfish vs valuable" cut is the predictor — same creator's "A week of realistic outfits" got 6K views; "You know what's a marketing scam? Capsule wardrobes" got 291K. Same format, different value-density.
- **Pacing:** ~150-160 WPM, but no on-screen cuts — verbal pacing carries retention, pattern interrupts come from vocal pitch shifts.
- **Searchability bonus:** spoken keywords get indexed by TikTok's search-driven discovery.

For an automated pipeline this matters: **TTS-generated narration on simple visuals can credibly mimic yapping IF the script reads like a stated opinion + reasoning, not a polished essay.**

### Pattern interrupts and retention loops

Every source converges on the same cadence rules:

- **Change something visually every 3-5 seconds.** Camera angle, text slam, B-roll, zoom.
- **Never more than 8 seconds of speech without a visual change** — guaranteed drop-off point.
- **MrBeast canonical cadence:** cut every 1.3-1.5s for entertainment shorts; for 25s reels this is ~1 cut every 2s.
- **Strong reset at 40% and 80% of runtime.** A bigger interrupt (music drop, camera flip) at predictable drop points.
- **Verbal interrupts ("mid-reel hooks"):** "But here's where it gets weird," "Step 2 of 3." Reopen attention via curiosity gaps.
- **"Numbering as progress bar":** "Step 2 of 3" lets viewers feel the payoff getting closer.

**MrBeast publicly reversed his position on ultra-fast cuts in early 2024 — for >2min content, slower wins. For <60s shorts, fast cuts still win.** For a 25s reel, 1 cut / 2s cadence is correct.

### Closes / CTAs / loop-backs

Biggest tactical finding: **the loop-back close beats every CTA on every metric except direct conversion.**

- **YouTube counts every loop as a new view** (rule change March 31, 2025). Loop-engineered Shorts routinely push retention >100% because segments are rewatched.
- **How to engineer the loop:** final frame matches first frame conceptually; final spoken line callbacks the opening line; **end abruptly, not gently** — soft endings ("thanks for watching") destroy the loop.
- **CTA is a tax on the loop.** A 3-second "follow for more" at the end of a 20s short is 15% of runtime and breaks the loop. Move CTA to caption.
- **If you must have a verbal CTA, bake it into the loop:** last line references opening such that finishing = restarting.

| CTA pattern | Drives |
|---|---|
| "Save this for later" | Saves (heavily weighted on Reels) |
| "Comment [KEYWORD] and I'll DM you" | Comments + DM funnel (5-15% conversion vs 1-3% for link-in-bio) |
| "Part 1 of 3, follow for the rest" | Follows (Zeigarnik effect) |
| Implicit question / cliffhanger | Comments + rewatches (highest-engagement default) |
| "Link in bio" | Almost nothing on Reels |

**Rule:** one CTA per video. Multiple CTAs cut total action.

### Length and pacing

Two different goals = two different lengths:

| Goal | Best length |
|---|---|
| Maximum completion + loops + virality | **7-15s** |
| Safe default for value + retention | **15-30s** |
| Maximum engagement (comments/shares) + view count | **60-90s** |
| Hard ceiling before distribution penalty | **90s** (Mosseri statement) |

A 30s reel at 50% retention delivers 15s watch-time per viewer; a 10s reel at 80% delivers 8s. The algorithm cares about *watch-time per view*, not completion alone. **For an automated 25s pipeline you are in the safe-default zone — strong completion AND meaningful watch-time.**

**Pacing / WPM:**
- General short-form sweet spot: **150-170 WPM (2.5-2.8 words/sec)**.
- Under 130 WPM sounds sluggish; over 180 sounds rushed.
- **Technical/science content goes slower: 120-140 WPM** for processing time.
- For 25s reel: **~62-70 words of narration**. Tight.

**Cuts:** ~12-15 cuts for a 25s reel (every 1.5-2s), with stronger interrupts at ~10s and ~20s marks.

### Science / educational reels specifically

Long-form science (Veritasium, Kurzgesagt) doesn't translate literally to 25s vertical. Shorts-specific structure:

1. **Reverse structure (payoff first).** Inverts academic "build up to conclusion" instinct.
2. **Contradiction + promise.** Compressed Veritasium open in 3s.
3. **Authority hook is cheap and powerful** — "physicist explains...", "this is what neutrinos actually do."
4. **Slightly longer optimal length** — 35-45s instead of 18-25s. For a 25s pipeline, science is at the SHORT end — compress hard.
5. **Slower WPM (120-140)** for cognition; lean on visual pattern interrupts to compensate.
6. **One single summarizable idea per video.** Vidpros heuristic: "if you can summarize it in one sentence, it was probably a great educational video."

**Codified science-Shorts structure:**
- 0-3s: Counter-intuitive fact OR authority hook + one visual proof
- 3-8s: Promise/stakes — "here's why it works"
- 8-20s: Mechanism in 2-3 chunks, B-roll every 2-3s
- 20-25s: Payoff that reframes the opening + loop callback

### Part 1 — what we should actually use

**Pick one structure and parameterize the hook/payoff/loop slots:**

1. **Primary: Contrarian-or-Surprising-Fact + Mechanism + Loop-back.** Hook is either contrarian or counter-intuitive fact (variants: `shock_stat`, `contrarian`, `authority`). Body is one specific mechanism with 2-3 visual changes. Payoff lands the reframing and callbacks the hook. Highest hook-rate of any tested pattern.
2. **Secondary: Listicle "3 things."** Only when content genuinely decomposes into discrete enumerable points. Self-paces. Smaller N wins on completion.

Drop everything else.

**Replace the two-architecture router with a single structure.** Research consensus: hook does 80%, structure 15%, CTA 5%. A router that picks between architectures is solving the wrong problem. The "yapping" finding is the strongest argument: highest-performing TikToks in 2025 are *less* structured than scripted shorts. System intelligence should go into hook generation and loop-back close, not into architectural selection.

**Concrete shape:** URL → 1 `.harness()` that (a) extracts the single most surprising claim, (b) picks one hook variant from a typed enum, (c) generates 60-70 words at ~150 WPM in Hook→Mechanism→Payoff, (d) generates a loop-back final line that callbacks line 1, (e) emits a shot list with 12-15 visual changes every 1.5-2s.

---

## Part 2 — On-screen text conventions

### The foundational stat: viewers watch with sound off

Most cited figure across 2025-2026 sources: **80-85% of social viewers watch with sound off** on mobile feeds. Manchester Digital reports "over 85%" for 2025; AMZG Media confirms 85%; Mixcord cites 80% in Feb 2026. OpusClip's analysis of viral TikToks found **80.2% used captions and 78.6% animated them**.

**Implication:** verbatim time-aligned subtitles are no longer optional — they're the primary narration channel. Audio is secondary. Any strategy that *replaces* verbatim subtitles with editorial alternatives leaves the 80% mute majority unable to follow.

### Reframing the original question: it's not subtitles XOR editorial — top accounts do BOTH

Most consequential clarification from this research: **the Hormozi style and MrBeast style are NOT contrapuntal**. They are verbatim word-by-word transcripts, where the *highlight* on individual words is the editorial layer. Hormozi's captions transcribe what he says; the editorial decision is *which word to color yellow*. MrBeast: verbatim transcript, 2-3 words per card, keyword in brand color.

True contrapuntal text exists in **specific structural roles**, layered *in addition to* subtitles or in dedicated B-roll scenes without voiceover.

Genre breakdown:

| Genre | Dominant text strategy |
|---|---|
| Talking-head, business, educational, fitness, finance | Verbatim word-by-word karaoke subtitles + highlighted keywords (Hormozi/MrBeast pattern) |
| Storytime, "POV", lifestyle | Verbatim subtitles + editorial title-card hook in the first frame |
| Comedy/skits | Often *editorial* overlays — withheld punchline, reaction text ("WAIT WHAT"), character labels |
| Recipe / how-to | Step labels + ingredient overlays in addition to (or instead of) voiceover subtitles |
| Documentary / B-roll heavy | Hybrid: voiceover subtitles + accent overlays for numbers, named entities, locations |

### The four text strategies and what dominates

- **Auto-generated word-by-word subtitles** (karaoke): dominant default. CapCut/Submagic ship this baseline. ~80% of viral TikToks use them, ~79% animate them.
- **Editorial accent overlays**: layered on top, specific structural moments.
- **Hybrid**: most common in high-performing 2026 content — verbatim subtitles always, editorial overlays when script calls for them.
- **No text**: nearly extinct in talking-head; survives only in pure aesthetic Reels.

### Word-by-word karaoke captions — engineering reality

CapCut, Submagic, Captions.ai, Opus Clip all auto-align to speech using ASR (Deepgram/AssemblyAI/Speechmatics/Whisper-class) and produce word-level timestamps. Submagic claims 99% accuracy; Opus claims ~95%. **For TTS-generated audio (our case), alignment is EASIER, not harder — per-word timing is known at synthesis time, no ASR needed.**

Default reading pace: **3-7 words per caption card, 1-3 seconds on screen, 160-200 WPM**. Industry subtitle standards cap at 32-42 characters per line, max 2 lines.

### Keyword highlighting — what gets the accent color

Strong consensus:
- **Numbers** ("47 pounds", "$10K", "3 steps") — highest-impact category
- **Named entities** (people, products, places, brand names)
- **Punchlines / payoff words** — the noun that lands the reveal
- **Concrete nouns** carrying the core claim
- **Verbs of action** when they are the reveal ("EXPLODED", "DOUBLED")

Rule: **1-2 highlighted words per phrase maximum.** More dilutes emphasis. High contrast required: white-on-black-stroke text with yellow/green/brand-color highlight.

### Editorial / accent caption patterns (when used)

Six canonical patterns:
1. **First-frame hook title card** — bold question or claim, 5-8 words, full center frame
2. **The number** — standalone large numeric overlay ("$47,000", "3 STEPS", "85%")
3. **The named entity / label** — what the B-roll shows ("THE HIPPOCAMPUS", "DR. CHEN, STANFORD")
4. **The plain-English translation** — when voiceover uses jargon, overlay translates ("entanglement = spooky link")
5. **Reaction / interjection** — "WAIT WHAT", "NO WAY", "BIG IF TRUE"
6. **List position marker** — "1 of 3", "STEP 2", scaffolding for educational content

Unifying property: editorial overlays carry **information the voiceover does not explicitly say** but that the viewer needs to lock in (a number's exact value, a name's spelling, a beat the comedy depends on).

### Position, font, color, motion

**Position (9:16, 1080×1920 canvas):**
- TikTok native UI consumes the **bottom 480px** (caption, handle, CTA, music ticker) and **right 120-140px** (action bar).
- Reels has similar but slightly narrower bottom obstruction.
- **Safe stage:** ~840×1280 centered, biased upward. Upper-middle works for talking-head where speaker's face is mid-frame.
- **Avoid bottom 25% and right 12-15%** under all circumstances.

**Font:** bold sans-serif at weight 700-900. Montserrat Bold is the workhorse (~60% of analyzed captions). TikTok Sans (mid-2025) is the new native option. Bold scored 31% better readability than medium.

**Color:** white fill, 4-6px black stroke, optional drop shadow. Active word in yellow/green/brand accent. Background boxes used for high-noise backgrounds but no longer dominant — heavy stroke wins.

**Motion:** pop-in scale (80%→100% spring), word-by-word reveal synced to spoken word, fade for slower phrase pacing. 0.3-0.5s per word. **Avoid bouncing/spinning/glitch** — over-animation reads as amateur in 2026.

### Length per card

Industry standard: **3-7 words per card, 1.5-3s on screen, max 42 chars/line, max 2 lines**. MrBeast more aggressive at 2-3 words/card; Hormozi runs 4-6 words. Pacing: 160-200 WPM short-form. Faster than 220 WPM reads as "cheap."

### Tools and their defaults

| Tool | Default behavior |
|---|---|
| CapCut | Auto-caption, word-by-word highlight, karaoke preset |
| Submagic | Word-by-word, 12+ animated presets (Hormozi/MrBeast/Iman Gadzhi), auto keyword highlighting |
| Captions.ai | Word-by-word with karaoke highlighting, mobile-first |
| Opus Clip | Word-by-word with viral presets |

**Every dominant tool ships the same default:** verbatim word-by-word subtitles, bold white sans-serif with black stroke, active-word color highlight. **This is the floor an automated reel pipeline should mirror.**

### Part 2 — what we should actually use

Two stacked text layers:

**Layer 1 — Verbatim word-by-word burned-in subtitles.** Always on, every scene with voiceover. Auto-aligned to TTS using word-level timing the TTS returns (or forced-alignment if not). Bold sans-serif (Montserrat Bold), white fill, 4-6px black stroke, centered horizontally, upper-center safe zone. 3-5 words per card. 1.5-2s per card. Active-word highlight in single accent color. **Non-negotiable** — serves the 80-85% mute majority.

**Layer 2 — Editorial accent overlay, per scene, when warranted.** Short, bold, 2-6 words. Re-scoped from "echo with different info" to one of the **six canonical patterns** above. **Not every scene gets a Layer 2 overlay** — the generator should explicitly emit `null` when verbatim subtitles alone carry the load, and reserve overlays for moments matching the six patterns.

Position Layer 2 in the *opposite third* of the frame from Layer 1 to avoid stacking.

**One concrete decision rule:** if the most important word in the scene is a number, a named entity, or a jargon term the voiceover defines, emit an accent overlay containing that single thing, 1.5-2× subtitle size, hold the full scene. Otherwise emit `null`. This matches what Hormozi/MrBeast actually do: the editorial decision is *which word to make big*, not *what alternate sentence to invent*.

---

## Source URLs

### Part 1 sources
- [OpusClip — YouTube Shorts Hook Formulas](https://www.opus.pro/blog/youtube-shorts-hook-formulas)
- [OpusClip — Instagram Reels Hook Formulas](https://www.opus.pro/blog/instagram-reels-hook-formulas)
- [OpusClip — Ideal Instagram Reels Length](https://www.opus.pro/blog/ideal-instagram-reels-length)
- [OpusClip — Ideal YouTube Shorts Length](https://www.opus.pro/blog/ideal-youtube-shorts-length-format-retention)
- [Brandefy — Psychology of Viral Video Openers](https://brandefy.com/psychology-of-viral-video-openers/)
- [vidIQ — 18 Viral Hook Ideas](https://vidiq.com/blog/post/viral-video-hooks-youtube-shorts/)
- [Itsmostly — Hormozi's Hook, Retain, Reward](https://itsmostly.com/blog/alex-hormozis-content-strategy-hook-retain-and-reward-explained)
- [Powercademy — Hormozi Hook-Retain-Reward Framework](https://www.powercademy.com/blog/alex-hormozi-s-hook-retain-reward-framework)
- [Justin Welsh — PAIPS 5-Step Copywriting Formula](https://www.justinwelsh.me/newsletter/my-favorite-copywriting-formula-that-anyone-can-use)
- [Buffer — 10 Copywriting Formulas](https://buffer.com/resources/copywriting-formulas/)
- [Benly — Video Ad Structure Frameworks](https://benly.ai/learn/ad-creative/video-ad-structure-frameworks)
- [Socialync — Short-Form Video Structure Guide](https://www.socialync.io/blog/short-form-video-structure-guide-2026)
- [virvid — Looping Structure: Hidden Retention Trick](https://virvid.ai/blog/looping-structure-shorts-retention-2026)
- [virvid — Short Video Script Frameworks](https://virvid.ai/blog/short-video-script-frameworks-with-trending-examples)
- [Retention Rabbit — YouTube Hook Strategies](https://www.retentionrabbit.com/blog/youtube-hook-strategy-to-keep-viewers-watching)
- [Retention Rabbit — Audience Retention Hacks](https://www.retentionrabbit.com/blog/audience-retention-hacks-to-beat-the-algorithm)
- [AIR Media-Tech — Advanced Retention Editing](https://air.io/en/youtube-hacks/advanced-retention-editing-cutting-patterns-that-keep-viewers-past-minute-8)
- [Washington Post — MrBeast calls for slowing down](https://www.washingtonpost.com/technology/2024/03/30/video-editing-mrbeast-retention/)
- [GIGAZINE — End of retention editing era](https://gigazine.net/gsc_news/en/20240403-retention-editing-beastification-may-be-end/)
- [Laura Boyd — Yapping data](https://www.linkedin.com/posts/lauraboyd1_contentcreation-socialmediastrategy-tiktoktips-activity-7300008614474170368-i87Z)
- [Webgennie — 20 Proven Viral Negative Hooks](https://webgennie.com/20-proven-viral-negative-hooks-that-work-in-every-niche-2026-edition/)
- [Opus — 5 TikTok Hook Types](https://www.opus.pro/blog/tiktok-hooks-that-go-viral-2026)
- [Shortimize — Video Length Sweet Spots](https://www.shortimize.com/blog/video-length-sweet-spots-tiktok-reels-shorts)
- [Metricool — Instagram Reels Length](https://metricool.com/instagram-reels-length/)
- [Socialinsider — Ideal Reels Length Data](https://www.socialinsider.io/blog/instagram-reels-length/)
- [FlowShorts — WPM Speaking](https://flowshorts.app/blog/words-per-minute-speaking)
- [Edge Studio — Words-to-Time Calculator](https://edgestudio.com/words-to-time-calculator-give-better-vo-estimates-faster/)
- [Creator Flow — 50+ Instagram CTA Examples](https://creatorflow.so/blog/instagram-call-to-action-examples/)
- [Drive Editor — Best CTA Formats for Short Videos](https://driveeditor.com/blog/best-cta-formats-for-short-videos)
- [Klap — Creator's Guide to Viral Short Videos](https://klap.app/blog/short-videos-on-youtube)
- [VidPros — Best Explainer YouTube Channels](https://vidpros.com/best-explainer-youtube-channels/)
- [Miraflow — YouTube Shorts Best Practices](https://miraflow.ai/blog/youtube-shorts-best-practices-2026-complete-guide)
- [JoinBrands — YouTube Shorts Best Practices](https://joinbrands.com/blog/youtube-shorts-best-practices/)
- [Funny Muscle — Setup/Punchline Humor Blueprint](https://funnymuscle.com/write-better-jokes-without-ruining-the-setup/)
- [PrePublish — Visual Pattern Interrupts](https://prepublish.ai/blog/visual-pattern-interrupts-editing)

### Part 2 sources
- [Mixcord — The Mute Majority](https://www.mixcord.co/blogs/content-creators/the-mute-majority-stop-the-scroll)
- [AMZG Media — 85% watch without sound](https://amzg-media.com/blogs/everything-is-amzg/silent-scrollers-why-85-of-users-watch-without-sound-and-what-that-means-for-your-content)
- [Manchester Digital — Mute Is the New Norm 2025](https://www.manchesterdigital.com/post/title-productions/mute-is-the-new-norm-why-captions-win-in-2025-video)
- [OpusClip — Why 80% of viral TikToks use captions](https://www.opus.pro/blog/why-viral-tiktoks-use-captions)
- [OpusClip — TikTok caption best practices 2026](https://www.opus.pro/blog/tiktok-caption-subtitle-best-practices)
- [Submagic — How to make Hormozi captions](https://www.submagic.co/blog/how-to-make-alex-hormozi-captions)
- [Submagic — How to make MrBeast captions](https://www.submagic.co/blog/how-to-make-captions-like-mrbeast)
- [Submagic — Best fonts for subtitles](https://www.submagic.co/blog/best-font-for-subtitle)
- [Submagic vs Captions.ai](https://www.submagic.co/vs/submagic-vs-captions-ai)
- [Submagic vs CapCut](https://www.submagic.co/vs/capcut-vs-captions-ai)
- [Joyspace — Hormozi style 2026](https://joyspace.ai/hormozi-editing-style-2026-analysis)
- [Edimakor — MrBeast subtitle guide](https://edimakor.hitpaw.com/video-editing-tips/how-to-make-subtitles-like-mrbeast.html)
- [Captions.ai — Highlight keywords](https://captions.ai/help/guides/engagement/highlight-keywords)
- [Kreatli — TikTok safe zone 2026](https://kreatli.com/guides/tiktok-safe-zone)
- [TikAdSuite — TikTok ad safe zones 2026](https://tikadsuite.com/blog/tiktok-ad-safe-zones/)
- [Blitzcut — TikTok caption fonts 2026](https://blitzcutai.com/blog/best-caption-fonts-tiktok)
- [Vexub — Best subtitle styles](https://vexub.com/blog/best-subtitle-styles-social-media)
- [Alpha CRC — Subtitling guide 2025](https://alphacrc.com/insight/closed-captioning-subtitling-complete-guide/)
- [Clickyapps — Timing, readability, WPM](https://clickyapps.com/creator/captions/guides/timing-best-practices-readability-wpm)
- [Amra and Elma — 25 text overlay styles](https://www.amraandelma.com/text-overlay-styles-influencers-are-using/)
- [Overlaytext.com — Viral templates](https://overlaytext.com/blog/text-overlay-for-reels-tiktok-viral-templates)
- [Influencers Time — Kinetic typography 2026](https://www.influencers-time.com/boost-video-engagement-with-kinetic-typography-techniques-2/)
- [ACM 2024 — Useful but Distracting](https://dl.acm.org/doi/10.1145/3701571.3701574)
