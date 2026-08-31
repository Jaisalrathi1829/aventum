Design a HIGH-FIDELITY, PRODUCTION-QUALITY desktop web application UI/UX for:

# PROJECT AVENTUM

Aventum is an AI Payment Incident Intelligence and Revenue Recovery Operations platform.

It detects meaningful payment incidents, diagnoses likely root causes using evidence, evaluates bounded counterfactual recovery options, recommends a safe intervention, requests human approval, performs simulated execution, independently verifies the result, and maintains a complete audit trail.

This is a HIGH-STAKES FINTECH / PAYMENTS OPERATIONS PRODUCT.

The design must feel like a serious payment control plane used by experienced operations, reliability, risk, and payments teams.

It must NOT feel like:
- a generic SaaS dashboard
- a chatbot with charts
- an AI demo
- a student project
- a collection of disconnected pages

The product should look credible enough that an experienced fintech engineer or operator could imagine using it.

==================================================
1. CORE PRODUCT STORY
==================================================

The primary workflow is:

INCIDENT
→ EVIDENCE
→ ROOT CAUSE
→ SIMULATE
→ RECOMMEND
→ HUMAN APPROVAL
→ EXECUTE
→ VERIFY
→ AUDIT

The UI must make this workflow obvious without documentation.

The product's core principle:

**AI does not independently control payment decisions.**

Instead:

DETERMINISTIC SYSTEMS
calculate facts, simulations, risk, and policy

QWEN AGENT
interprets evidence, chooses tools, compares existing deterministic options, explains tradeoffs, and formulates a recommendation

HUMAN
approves intervention

SYSTEM
executes only bounded simulated actions

VERIFICATION
independently determines whether the action actually helped

Visually communicate this authority model.

==================================================
2. 10-SECOND JUDGE TEST
==================================================

A first-time reviewer should understand within approximately 10 seconds:

1. What payment incident is happening?
2. How serious is it?
3. How much business impact is at risk?
4. What does Aventum believe is causing it?
5. What intervention is being considered?
6. Is the action safe / bounded / simulated?
7. Has the outcome actually been verified?

The first viewport should answer these questions before exposing deep technical details.

==================================================
3. DATA FIDELITY — STRICT
==================================================

Do NOT invent production metrics.

Use only values explicitly supplied below or values clearly marked as placeholders.

Known verified Aventum values that may be used:

Flagship incident:
- gateway_C
- 3-day window
- significance: 9.26σ
- confidence: 68.81%
- evidence strength: 74.03%
- severity: CRITICAL

Flagship recommendation:
- gateway_C → gateway_A
- 30%
- projected GMV retained: ₹19,126.26
- projected success improvement: +3.41%

Verified simulated execution example:
- pre-action failure rate: 20.83%
- post-action failure rate: 17.42%
- 79 transactions moved

If a metric is unavailable:
display:

UNAVAILABLE

Do not fabricate capacity data.

Capacity:
UNAVAILABLE

Eligibility:
UNCONDITIONAL

Never invent:
- Razorpay production metrics
- real production gateway telemetry
- real production recovery values
- real production execution
- actual realized GMV

==================================================
4. TRUTH / DATA LANGUAGE
==================================================

Visually distinguish:

OBSERVED
Historical transaction facts.

SYNTHETIC
Modeled gateway / infrastructure state.

SIMULATED
Counterfactual or simulated action outcome.

PROJECTED
Forward-looking modeled estimate.

VERIFIED
Measured post-action result in the simulation environment.

AI-GENERATED EXPLANATION
Qualitative reasoning from Aventum Agent.

Never blur these categories.

Never use "AI says" for authoritative metrics.

Prefer:

Observed
Synthetic
Projected
Simulated
Policy result
Verified

The application is currently a simulation environment.

Persistent environment badge:

SIMULATION MODE
Synthetic infrastructure • No live routing changes

This should remain visible throughout the entire application.

==================================================
5. VISUAL DIRECTION
==================================================

Create an ORIGINAL premium fintech control-plane aesthetic.

Do NOT copy Razorpay's exact visual identity, logo, proprietary UI, or branding.

Aim for:

- sophisticated fintech
- enterprise observability
- operational clarity
- calm, precise visual language
- strong typography
- exceptional spacing
- high information density without clutter
- restrained surfaces
- clear charts
- premium tables
- meaningful status indicators
- excellent hierarchy

Avoid:

- excessive gradients
- neon
- glassmorphism
- decorative AI graphics
- giant robot/AI illustrations
- unnecessary 3D
- excessive rounded cards
- excessive shadows
- generic purple AI aesthetics

The design should feel fast and operational.

==================================================
6. VISUAL PRIORITY
==================================================

When information competes for space, prioritize:

1. Current operational state
2. Business impact
3. Decision / recommended action
4. Evidence / explanation
5. Safety / policy
6. Provenance / technical detail

Do not let database IDs dominate business information.

IDs, fingerprints, model versions, and technical metadata should be accessible through secondary UI such as drawers, expandable sections, or tooltips.

==================================================
7. DESKTOP
==================================================

Primary frame:

1440 × 900

Also support:

1280 × 800

Desktop is the primary environment.

==================================================
8. INFORMATION ARCHITECTURE
==================================================

Keep primary navigation small.

Use:

Overview
Incidents
Audit

Do NOT make Simulations and Recommendations mandatory top-level destinations if they can naturally live inside an incident workflow.

Within an incident, use contextual navigation:

Overview
Evidence & RCA
Simulation
Recommendation
Approval
Verification

Audit remains a global traceability view.

Mental model:

What's happening?
→ Why?
→ What can we do?
→ Is it safe?
→ Did it work?

The flagship incident must be reachable in ≤2 clicks from Overview.

==================================================
9. GLOBAL APP SHELL
==================================================

LEFT SIDEBAR

Aventum wordmark

Navigation:
- Overview
- Incidents
- Audit

TOP BAR

Include:
- SIMULATION MODE badge
- system health
- current time
- user identity
- compact agent status

Persistent banner must communicate:

SIMULATION MODE
Synthetic infrastructure • Simulated execution • No live routing changes

==================================================
10. SCREEN 1 — OPERATIONS OVERVIEW
==================================================

Purpose:

The operator's starting point.

This is NOT a generic KPI dashboard.

The first screen should immediately communicate operational urgency.

TOP SUMMARY

Use a concise set of metrics:

Payment Success Rate
Failure Rate
GMV at Risk
Active Incidents
Median Latency
P95 Latency

PRIMARY VISUAL

Payment Health Timeline

Show:
- baseline
- incident onset
- degradation
- current state

Clearly mark the incident period.

ACTIVE INCIDENTS

Use a compact high-quality table or cards.

Columns:

Incident
Severity
Affected Surface
Primary Signal
Confidence
Significance
GMV at Risk
State

Flagship example:

Gateway degradation
gateway_C
CRITICAL
9.26σ
68.81%
₹[verified/projected value]
INVESTIGATING

Do not fabricate unavailable values.

RECOVERY STATUS

Show:
- candidates evaluated
- recommendations awaiting approval
- actions executed
- verification state

PRIMARY CTA:

Open Incident

==================================================
11. SCREEN 2 — INCIDENT COMMAND CENTER
==================================================

This is the MOST IMPORTANT SCREEN.

It is the central Aventum workspace.

Header:

CRITICAL
Payment degradation detected

gateway_C
3-day incident window

Top-right:

Incident ID
Status
SIMULATION MODE

MAIN LAYOUT:

LEFT / CENTER:
Evidence + incident visualization

RIGHT:
Aventum Agent + decision state

----------------------------------------
A. WHAT HAPPENED
----------------------------------------

Large but compact metrics:

Failure Rate
Baseline
Current
Delta

Latency
P50
P95
Delta

GMV At Risk

Affected Transactions

Incident Window

Add an incident timeline underneath.

----------------------------------------
B. PRIMARY SIGNAL
----------------------------------------

Make this visually dominant.

PRIMARY

gateway_C

Significance:
9.26σ

Confidence:
68.81%

Evidence Strength:
74.03%

Severity:
CRITICAL

IMPORTANT:

These MUST remain separate.

Do NOT create:
"AI Confidence: 92%"

----------------------------------------
C. ROOT CAUSE
----------------------------------------

Show:

Likely root cause:
gateway_C degradation

Provide a concise evidence-backed explanation.

Below:

SUPPORTING EVIDENCE

Each evidence row/card:

Dimension
Value
Effect
Strength
Evidence ID

Clearly distinguish:

PRIMARY

and

DERIVATIVE

For example:

Gateway
gateway_C
Failure spike
PRIMARY

Issuer
SBI
Secondary correlation
DERIVATIVE

Region
Delhi
Secondary signal
DERIVATIVE

----------------------------------------
D. WHY AVENTUM KNOWS
----------------------------------------

Add a compact evidence/explanation panel.

Show:

AVENTUM AGENT
ANALYSIS COMPLETE

Example:

gateway_C is the strongest supported explanation for the observed payment degradation.

Supporting references:
E-17
E-24
E-31

Add:

Why this conclusion?

Expandable section containing only concise, evidence-linked reasoning.

Never show chain-of-thought.

----------------------------------------
E. AGENT ACTIVITY
----------------------------------------

Show an unobtrusive activity stream:

✓ Incident context
✓ Evidence
✓ Gateway health
✓ Routing options
✓ Counterfactual comparison
✓ Policy validation

This is not a chat window.

It simply shows what the agent/system actually did.

==================================================
12. SCREEN 3 — RECOVERY SIMULATION
==================================================

Purpose:

Prove Aventum does not jump from diagnosis directly to action.

Header:

Recovery Simulation

Subtext:

Compare bounded interventions under the same incident conditions.

Show a visually strong comparison:

HELD CONSTANT

Transaction population
Amounts
Incident window
Gateway health
Gateway profiles
Policy version

CHANGED

Traffic allocation only

This should be immediately understandable.

----------------------------------------
CANDIDATE COMPARISON
----------------------------------------

Display:

NO ACTION
gateway_C → gateway_A @ 10%
gateway_C → gateway_A @ 20%
gateway_C → gateway_A @ 30%

For each candidate show:

Projected Success Rate
Success Delta
Projected GMV Retained
Latency Delta
Concentration
Risk
Policy Result

Every projected value must have:

PROJECTED
or
SIMULATED

label.

Use the actual verified flagship values where provided.

Do not fabricate missing numbers.

Make the selected candidate visually obvious.

Include:

"Same incident state. Same transaction population. Traffic allocation is the changed variable."

----------------------------------------
SIMULATION DETAIL
----------------------------------------

Use an expandable drawer.

Show:

Simulation ID
Input Fingerprint
Simulation Fingerprint
Seed
Model Version
Policy Version
Held-Constant Variables
Changed Variables
Assumptions
Limitations

Do not crowd the primary screen.

==================================================
13. SCREEN 4 — RECOMMENDATION + SAFETY
==================================================

Purpose:

Show how Aventum turns simulation results into a bounded recommendation.

Header:

Recovery Recommendation

Recommendation:

Reroute gateway_C → gateway_A
30%

EXPECTED IMPACT

₹19,126.26
Projected GMV retained

+3.41%
Projected success improvement

Latency:
[verified/projected]

Affected:
79 transactions

Clearly mark:

PROJECTED
SIMULATED

----------------------------------------
POLICY
----------------------------------------

Show the 13 deterministic safety gates.

Example:

RCA confidence ✓ PASS
Evidence strength ✓ PASS
Significance ✓ PASS
Severity ✓ PASS
Primary alert ✓ PASS
Simulation valid ✓ PASS
Simulation current ✓ PASS
Target healthy ✓ PASS
Target eligible ✓ PASS
Traffic bound ✓ PASS
Concentration bound ✓ PASS
Benefit threshold ✓ PASS
Simulation provenance ✓ PASS

Use expandable rows.

When something fails:

show exact machine-readable reason.

Do NOT show:
"AI Safety Score 93"

The key visual message:

AI PROPOSES.
POLICY DECIDES.

----------------------------------------
RISK
----------------------------------------

Separate:

Concentration
Target Health
Latency
Evidence Uncertainty
Routing Uncertainty

Also show:

Capacity
UNAVAILABLE

Eligibility
UNCONDITIONAL

Do not create fake capacity gauges.

----------------------------------------
ALTERNATIVES
----------------------------------------

Show:

NO_ACTION
10%
20%
30%

Explain why the chosen candidate is preferable.

Primary CTA:

Request Human Approval

Secondary:

Choose NO_ACTION

There must be NO execution button.

==================================================
14. SCREEN 5 — HUMAN APPROVAL
==================================================

Purpose:

Make the human decision explicit and trustworthy.

Header:

Approval Required

ACTION

Move 30% of gateway_C traffic
to gateway_A

WHY

2–3 concise evidence-backed reasons

EXPECTED EFFECT

Projected GMV retained
Success improvement
Latency impact
Concentration after action

RISK

Risk components

POLICY

13/13 gates passed

SIMULATION

Simulation ID
Fingerprint
Timestamp
Expiry

PROVENANCE

Synthetic incident
Simulated execution
Observed transaction amounts
Synthetic gateway model

IMPORTANT:

Make:

EXPECTED

and:

ACTUAL

visually unmistakable.

Approval controls:

[ Reject ]

[ Approve ]

Show expiration countdown.

No automated approval.

==================================================
15. SCREEN 6 — EXECUTION + VERIFICATION
==================================================

Use progressive states rather than separate unrelated pages.

----------------------------------------
STATE A — READY
----------------------------------------

Approved by:
HUMAN:jaisal

Adapter:
SimulatedRoutingAdapter

Action:
gateway_C → gateway_A @ 30%

Status:
READY

----------------------------------------
STATE B — EXECUTING
----------------------------------------

Activity:

✓ Validated approval
✓ Revalidated simulation
✓ Revalidated policy
✓ Checked target health
✓ Checked idempotency
✓ Applied simulated routing change

Do NOT fake a real infrastructure animation.

----------------------------------------
STATE C — EXECUTED
----------------------------------------

Show:

Action ID
Execution fingerprint
Traffic moved
Timestamp
Audit reference

Then:

VERIFYING

----------------------------------------
VERIFICATION
----------------------------------------

This is one of the strongest product screens.

Header:

Post-Action Verification

Show a clear BEFORE vs AFTER comparison.

Failure Rate:

20.83% → 17.42%

Success Rate:

79.17% → 82.58%

Traffic Distribution:

Before → After

Latency:

Before → After

GMV:

Expected
vs
Actual Simulated

Then:

RECOVERY EFFECTIVE

or:

PARTIALLY EFFECTIVE

or:

RECOVERY NOT VERIFIED

Make the hierarchy explicit:

EXECUTED ≠ VERIFIED

This distinction is critical.

Show:

Verification Window
Cohort Definition
Metric Definition
Evidence Timestamp

==================================================
16. SCREEN 7 — AUDIT TRAIL
==================================================

Purpose:

Prove the entire decision is reconstructable.

Header:

Audit Trail

Show a clean vertical timeline:

INCIDENT DETECTED
↓
EVIDENCE COLLECTED
↓
RCA COMPLETED
↓
SIMULATION COMPLETED
↓
POLICY VALIDATED
↓
RECOMMENDATION CREATED
↓
HUMAN APPROVAL REQUESTED
↓
APPROVAL GRANTED
↓
SIMULATED ACTION EXECUTED
↓
VERIFICATION COMPLETED

Each event expands to:

Timestamp
Actor
Event Type
Reference ID
Input / Output References
Provenance
Fingerprint
Status

Filters:

Incident
Recommendation
Action
Agent Run
Simulation

Button:

Export Audit Record

Do NOT expose hidden chain-of-thought.

==================================================
17. AGENT EXPERIENCE
==================================================

Do NOT create a full-screen chatbot.

Use a compact embedded:

AVENTUM AGENT

side panel / command panel.

Display:

Current task
Tools used
Key findings
Decision
Evidence references
Uncertainty

Example:

AVENTUM AGENT
Analysis complete

✓ Incident context
✓ Gateway health
✓ Routing options
✓ Counterfactual comparison
✓ Policy validation

Decision:
Reroute gateway_C → gateway_A @ 30%

Evidence:
E17 · E24 · E31

Add:

"View reasoning summary"

This contains concise evidence-linked rationale ONLY.

Never display chain-of-thought.

==================================================
18. FAILURE / EDGE STATES
==================================================

Do NOT make every failure a separate page.

Create reusable variants.

### NO_ACTION

"No safe intervention beats the current outcome."

Show:
- what was evaluated
- why intervention was not selected
- relevant policy/evidence

### POLICY BLOCKED

"ACTION BLOCKED"

Show exact failed gate.

Example:
Target gateway unhealthy.

### INSUFFICIENT EVIDENCE

"Aventum does not have enough evidence to recommend intervention."

Show missing evidence.

CTA:
Continue monitoring

### STALE SIMULATION

"Simulation is no longer valid."

Show:
Health changed
Policy changed
World fingerprint changed

CTA:
Re-simulate

### AGENT UNAVAILABLE

"Agent unavailable"

Then immediately show:

"Deterministic incident analysis remains available."

Do not make the application look broken.

### EXECUTION REJECTED

"Action was not executed."

Show exact reason.

No ambiguous success language.

### SIMULATION INVALID

"Simulation could not be used."

Show exact reason and safe next action.

==================================================
19. COMPONENT SYSTEM
==================================================

Create a coherent design system with reusable:

- status badge
- severity badge
- metric card
- evidence card
- evidence citation
- chart
- timeline
- audit event
- policy gate
- simulation candidate
- recommendation card
- approval panel
- verification comparison
- agent activity row
- provenance badge
- fingerprint field
- tooltip
- modal
- drawer
- loading state
- empty state
- error state

Create variants for:
- normal
- warning
- critical
- blocked
- simulated
- verified
- stale
- unavailable

==================================================
20. INTERACTION FLOWS
==================================================

Prototype these exact flows.

----------------------------------------
FLOW A — FLAGSHIP INCIDENT
----------------------------------------

Overview
→ Open gateway_C incident
→ Review RCA
→ Review evidence
→ Open simulation
→ Compare candidates
→ Open recommendation
→ Review policy
→ Request human approval
→ Approve
→ Simulated execution
→ Verification
→ Audit

----------------------------------------
FLOW B — NO ACTION
----------------------------------------

Incident
→ evidence/diagnosis
→ simulation comparison or applicability decision
→ NO_ACTION
→ no approval
→ monitoring

----------------------------------------
FLOW C — POLICY BLOCK
----------------------------------------

Recommendation
→ failed policy gate
→ action blocked
→ explanation
→ no execution

----------------------------------------
FLOW D — STALE
----------------------------------------

Approved recommendation
→ state changes
→ stale detected
→ execution rejected
→ re-simulate

----------------------------------------
FLOW E — AGENT FAILURE
----------------------------------------

Incident
→ Qwen unavailable
→ deterministic analysis remains available
→ no fabricated agent output

==================================================
21. DESIGN PRINCIPLE — PROGRESSIVE DISCLOSURE
==================================================

Do NOT put every technical field on the primary screen.

Use:

- expandable rows
- drawers
- tabs
- details panels
- tooltips

The primary screen should tell the story.

Technical provenance should be one interaction away.

==================================================
22. DESIGN PRINCIPLE — OPERATIONAL CLARITY
==================================================

Every major screen should answer:

WHAT IS HAPPENING?
WHY?
WHAT CAN WE DO?
IS IT SAFE?
DID IT WORK?

If a component does not help answer one of these questions, remove it.

==================================================
23. DESIGN PRINCIPLE — BUSINESS IMPACT
==================================================

Business impact must be visible without becoming misleading.

Prioritize:

GMV at Risk
Projected GMV Retained
Success Rate
Failure Rate
Transactions Affected
Latency

Keep:

Projected
Simulated
Observed
Verified

visually distinct.

Do not imply financial realization before verification.

==================================================
24. ACCESSIBILITY
==================================================

Support:

- sufficient contrast
- keyboard navigation
- visible focus
- semantic labels
- readable tables
- accessible status messages
- color + text/icon for state
- responsive layout behavior

==================================================
25. RESPONSIVE BEHAVIOR
==================================================

Primary target is desktop.

Do not optimize for mobile at the expense of desktop control-plane usability.

However, define sensible collapse behavior for:
- sidebar
- evidence columns
- simulation tables
- policy gates
- approval controls

==================================================
26. FINAL FIGMA STRUCTURE
==================================================

Create Figma pages/frames in this order:

01 — Foundations
02 — Components
03 — Overview
04 — Incident Command Center
05 — Recovery Simulation
06 — Recommendation + Safety
07 — Human Approval
08 — Execution + Verification
09 — Audit
10 — State Variants
11 — Prototype Flows

Do NOT create unnecessary application pages.

==================================================
27. MOST IMPORTANT SCREENS
==================================================

Spend the majority of design quality on:

1. Incident Command Center
2. Recovery Simulation
3. Recommendation + Policy
4. Verification

These four screens are the heart of the Aventum product story.

==================================================
28. FINAL VISUAL TEST
==================================================

Before finalizing the design, check:

Can a reviewer understand:

- the incident
- the root cause
- the business impact
- the available interventions
- why one intervention is preferred
- why the action is permitted
- that a human approved it
- what was executed
- whether it actually worked

without reading code or documentation?

If not, improve hierarchy rather than adding more UI.

==================================================
29. FINAL PRODUCT MESSAGE
==================================================

The design must communicate this idea:

**Aventum does not let AI guess and act on payments.**

**It gives the agent deterministic evidence and bounded tools, keeps humans in control, executes only within explicit safety constraints, and independently verifies whether the resulting action actually worked.**

The finished Figma prototype should feel:

- premium
- trustworthy
- operational
- technically credible
- AI-native without being AI-gimmicky
- appropriate for serious fintech infrastructure

Do not sacrifice clarity for visual decoration.
Do not sacrifice operational truth for aesthetics.
Do not create UI for capabilities that do not exist.
Do not imply production integration that does not exist.