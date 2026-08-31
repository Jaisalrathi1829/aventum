Refine the EXISTING Project Aventum Figma design.

IMPORTANT:
This is a FINAL PRODUCT-QUALITY CORRECTION PASS.

Do NOT redesign the product.
Do NOT change the overall visual language.
Do NOT create new core screens.
Do NOT replace the current layout system.
Do NOT introduce new product capabilities.

Preserve the existing premium dark fintech/infrastructure aesthetic,
navigation, typography direction, hero screens, workflow, and component language.

The goal is to fix the remaining UX/data-semantic inconsistencies so the design
is safe to hand directly to a React/TypeScript implementation team.

==================================================
1. SINGLE SOURCE OF TRUTH — UI SEMANTICS
==================================================

The current frontend must visually distinguish these concepts:

OBSERVED
Historical transaction facts.

SYNTHETIC
Modeled gateway/infrastructure state.

SIMULATED
Counterfactual/action outcome.

PROJECTED
Forward-looking modeled estimate.

VERIFIED
Measured post-action simulated result.

DETERMINISTIC
System-calculated result such as policy, detector, simulator, verifier.

AI-GENERATED
Qualitative explanation/rationale from Aventum Agent.

Do NOT use "OBSERVED" for deterministic policy/system outputs.

Do NOT imply AI generated authoritative metrics.

Use semantic badges consistently.

==================================================
2. FIX GMV TERMINOLOGY
==================================================

Do NOT use the same value interchangeably for:

GMV AT RISK
PROJECTED GMV RETAINED
ACTUAL SIMULATED GMV RETAINED
VERIFIED OUTCOME

The known verified Aventum example is:

PROJECTED GMV RETAINED
₹19,126.26

Therefore:

If an actual GMV-at-risk value is not available, do NOT invent one.

Instead show:

GMV AT RISK
UNAVAILABLE

and separately:

PROJECTED GMV RETAINED
₹19,126.26

On verification:

EXPECTED / PROJECTED
₹19,126.26

ACTUAL SIMULATED
₹19,126.26

VARIANCE
₹0.00

Only show these values where their semantics are actually valid.

==================================================
3. FIX FAILURE-RATE SEMANTICS
==================================================

Do not imply:

baseline failure rate = 96.20%

when 96.20% is actually a success-rate value.

Keep the metrics explicitly separated.

For example:

SUCCESS RATE
Baseline: 96.20%

FAILURE RATE
Current: 20.83%

On verification:

FAILURE RATE
20.83% → 17.42%

SUCCESS RATE
79.17% → 82.58%

Never infer one metric's label from the opposite metric's value.

==================================================
4. INCIDENT COMMAND CENTER
==================================================

Preserve the current excellent layout.

Make the metric hierarchy more explicit:

WHAT HAPPENED?
→ BUSINESS IMPACT
→ WHY?
→ WHAT CAN WE DO?

Ensure the first viewport immediately communicates:

- severity
- failure degradation
- affected gateway
- significance
- confidence
- projected/at-risk business impact where actually available

Do not introduce misleading numbers just to fill empty space.

==================================================
5. AGENT LANGUAGE
==================================================

The agent is NOT the authoritative decision-maker.

Avoid UI wording that implies:

"AI decided the action."

Prefer:

AGENT RECOMMENDATION

or:

RECOMMENDATION

instead of:

AI DECISION

The system should visually communicate:

AI INTERPRETS + ORCHESTRATES
↓
DETERMINISTIC SYSTEMS CALCULATE + VALIDATE
↓
HUMAN APPROVES
↓
SYSTEM EXECUTES
↓
VERIFIER MEASURES OUTCOME

Keep the agent visually secondary to the operational decision.

==================================================
6. DECISION STATE RAIL
==================================================

Preserve the existing rail:

Diagnosed
Simulated
Recommended
Policy validated
Human approved
Executed
Verified

Improve the semantics slightly:

- Diagnosed → DETERMINISTIC + AGENT
- Simulated → DETERMINISTIC
- Recommended → AGENT + DETERMINISTIC candidate
- Policy validated → DETERMINISTIC
- Human approved → HUMAN
- Executed → SIMULATED SYSTEM
- Verified → DETERMINISTIC

The user should understand who/what produced each state.

==================================================
7. OVERVIEW SCREEN
==================================================

Do NOT make the Overview pretend it is a live production dashboard.

Keep:

SIMULATION MODE
Synthetic infrastructure • Simulated execution • No live routing changes

Make it clear which metrics are:

OBSERVED
PROJECTED
SIMULATED

Do not show:

"Actions executed: 0"

if the selected application state represents an executed action.

The visual design should anticipate one canonical backend state source.

Show state-neutral component variants where appropriate:

0 actions
1 awaiting approval
1 executed
1 verified

Do not hard-code the interface around only the initial state.

==================================================
8. CROSS-SCREEN STATE CONSISTENCY
==================================================

All screens belong to ONE incident workflow.

The design must support state progression:

INVESTIGATING
→ DIAGNOSED
→ SIMULATED
→ RECOMMENDED
→ AWAITING APPROVAL
→ APPROVED
→ EXECUTED
→ VERIFYING
→ VERIFIED / RESOLVED

Do not design screens as disconnected static snapshots.

Create reusable variants for these state changes.

==================================================
9. RECOMMENDATION SCREEN
==================================================

Preserve:

AI PROPOSES.
POLICY DECIDES.

Make the recommendation hierarchy:

ACTION
→ EXPECTED IMPACT
→ POLICY
→ RISK
→ ALTERNATIVES
→ HUMAN DECISION

All projected values must be visibly marked:

PROJECTED
or
SIMULATED

Do NOT use "verified" language here.

==================================================
10. POLICY
==================================================

Preserve the 13 deterministic gates.

Do not turn them into:

AI confidence
AI safety score
AI approval score

Instead clearly label:

DETERMINISTIC SAFETY POLICY

Possible statuses:

PASS
BLOCKED
UNAVAILABLE
NOT APPLICABLE

Do not invent a capacity value.

Capacity:
UNAVAILABLE

Eligibility:
UNCONDITIONAL

==================================================
11. HUMAN APPROVAL
==================================================

Preserve the current approval layout.

Make these visually distinct:

EXPECTED
vs
ACTUAL

The approval view should communicate:

"This is a proposed simulated action awaiting human authorization."

Do NOT imply it has already happened.

Primary controls:

REJECT
APPROVE

No direct execution button before approval.

==================================================
12. EXECUTION
==================================================

Preserve the progressive execution states:

READY
→ EXECUTING
→ EXECUTED
→ VERIFYING

Use system-language rather than dramatic animation.

Show:

Approved by
Adapter
Action
Status

Activity should remain factual:

Validated approval
Revalidated simulation
Revalidated policy
Checked target health
Checked idempotency
Applied simulated routing change

Do NOT create fake live infrastructure animations.

==================================================
13. VERIFICATION
==================================================

Make verification the strongest business-value moment.

Header:

POST-ACTION VERIFICATION

Question:

DID THE ACTION ACTUALLY WORK?

Show prominently:

FAILURE RATE
20.83% → 17.42%

SUCCESS RATE
79.17% → 82.58%

EXPECTED
vs
ACTUAL SIMULATED

Then:

RECOVERY EFFECTIVE

Make the distinction visually unmistakable:

EXECUTED ≠ VERIFIED

Also show:

verification window
cohort
metric definition
timestamp

Verification must look like independent measurement,
not confirmation of the previous prediction.

==================================================
14. AUDIT
==================================================

Preserve the existing audit timeline.

Every event should visually identify its origin:

OBSERVED
DETERMINISTIC
AI-GENERATED
SIMULATED
VERIFIED
HUMAN

Example:

INCIDENT DETECTED — OBSERVED
EVIDENCE COLLECTED — OBSERVED
RCA COMPLETED — AI-GENERATED
SIMULATION COMPLETED — SIMULATED
POLICY VALIDATED — DETERMINISTIC
RECOMMENDATION CREATED — AI-GENERATED
APPROVAL GRANTED — HUMAN
ACTION EXECUTED — SIMULATED
VERIFICATION COMPLETED — VERIFIED

Do not call deterministic policy output "OBSERVED."

==================================================
15. PROVENANCE / TECHNICAL DETAIL
==================================================

Keep technical details accessible but secondary:

- simulation ID
- recommendation ID
- action ID
- audit reference
- fingerprints
- timestamps
- model version
- policy version

Use drawers/expanders rather than allowing IDs to dominate the screen.

==================================================
16. FAILURE STATES
==================================================

Preserve reusable variants for:

NO_ACTION
POLICY BLOCKED
INSUFFICIENT EVIDENCE
STALE SIMULATION
AGENT UNAVAILABLE
SIMULATION INVALID
EXECUTION REJECTED

The interface must remain operationally useful in every state.

Especially:

AGENT UNAVAILABLE

must communicate:

Agent unavailable.

Deterministic incident analysis remains available.

Do NOT make the entire application look broken.

==================================================
17. AGENT ACTIVITY
==================================================

Keep the compact Aventum Agent activity panel.

Show only factual system/agent actions such as:

Incident context
Evidence
Gateway health
Routing options
Counterfactual comparison
Policy validation

Do NOT add:

Thinking...
Reasoning...
AI is thinking...
AI decided...

Do NOT expose chain-of-thought.

==================================================
18. VISUAL STYLE
==================================================

Keep the current visual system.

Do NOT push it back toward cyberpunk.

Target:

Premium fintech infrastructure
85%

AI / technical character
15%

Keep:

dark theme
restrained cyan
restrained purple
semantic red/amber/green
precise typography
technical metadata styling

Reduce:

glowing effects
neon
excessive monospace
cyber-security-console styling

The product should look like a serious payment operations system
with AI embedded inside it.

==================================================
19. RESPONSIVENESS / COMPONENTS
==================================================

Do not create unnecessary new components.

Create reusable variants for:

- metrics
- status badges
- provenance badges
- policy gates
- evidence cards
- simulation candidates
- recommendation cards
- approval panel
- verification comparison
- audit events
- agent activity
- loading
- error
- unavailable
- stale

==================================================
20. FINAL CROSS-SCREEN CONSISTENCY CHECK
==================================================

Before finishing, verify that these concepts never contradict each other
between screens:

- incident status
- failure rate
- success rate
- gateway
- severity
- confidence
- significance
- evidence strength
- projected GMV
- GMV at risk
- recommendation
- approval state
- execution state
- verification state
- provenance
- simulation mode

A value should have ONE semantic meaning everywhere.

Do not reuse a number merely because it looks visually convenient.

==================================================
21. FINAL DESIGN TEST
==================================================

The final UI should tell this story:

Something went wrong.
↓
Aventum detected it.
↓
Evidence supports the likely cause.
↓
The agent interprets that evidence.
↓
Deterministic simulations evaluate bounded options.
↓
Policy determines what is permitted.
↓
A human decides.
↓
A simulated action executes.
↓
The verifier measures what happened.
↓
The audit trail reconstructs everything.

A reviewer should understand this flow without reading the code.

==================================================
22. FINAL ACCEPTANCE
==================================================

DO NOT:

- redesign the application
- add unnecessary screens
- invent metrics
- invent production capabilities
- imply real Razorpay execution
- confuse projected with verified values
- confuse success rate with failure rate
- label deterministic results as observed
- make AI appear to have authority it does not have

The finished result must be:

PREMIUM
CALM
PRECISE
TRUSTWORTHY
FINTECH-GRADE
OPERATIONALLY CREDIBLE
AI-NATIVE WITHOUT BEING AI-GIMMICKY

The design is now being prepared for direct React/TypeScript implementation.

Optimize for implementation clarity as well as visual quality.