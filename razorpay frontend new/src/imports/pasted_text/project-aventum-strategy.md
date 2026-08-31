Do NOT choose A or B exactly.

Use a HYBRID, QUALITY-FIRST approach for Project Aventum.

The goal is not to make the largest number of screens.
The goal is to create a coherent, premium fintech operations product whose
most important workflow is exceptionally clear and presentation-ready.

==================================================
1. ESTABLISH THE DESIGN SYSTEM FIRST
==================================================

Before building full screens, establish a reusable design system containing:

- typography hierarchy
- spacing/grid system
- semantic color system
- status/severity system
- buttons and controls
- metric cards
- tables
- charts
- evidence components
- policy-gate components
- simulation components
- recommendation components
- approval controls
- verification components
- audit-event components
- provenance badges
- drawers/modals
- loading/error/empty states

The design system must support:
- OBSERVED
- SYNTHETIC
- SIMULATED
- PROJECTED
- VERIFIED
- AI-GENERATED EXPLANATION

These states must remain visually distinct.

==================================================
2. HERO-FIRST STRATEGY
==================================================

After the foundations are established, spend the majority of design effort on
the four HERO SCREENS:

1. Incident Command Center
2. Recovery Simulation
3. Recommendation + Safety
4. Execution + Verification

These four screens must receive the highest level of refinement.

They should communicate the core Aventum story almost immediately.

Do NOT let secondary screens receive equal design effort at the expense of
these hero experiences.

==================================================
3. HERO SCREEN SUCCESS TEST
==================================================

A first-time reviewer should understand within approximately 10 seconds:

- what happened
- how serious it is
- business impact
- likely cause
- what intervention is being considered
- whether it is permitted/safe
- whether the action is simulated
- whether the result has been verified

Do not require the reviewer to understand the database or implementation.

==================================================
4. BUILD THE REMAINING CORE SCREENS
==================================================

Once the hero screens establish the visual language, build:

- Operations Overview
- Human Approval
- Audit Trail
- reusable failure/state variants

Do NOT create generic placeholder screens merely to increase screen count.

Every screen must have a clear operational purpose.

==================================================
5. INFORMATION ARCHITECTURE
==================================================

Prefer a shallow structure.

Primary navigation:

Overview
Incidents
Audit

Within an incident:

Overview
Evidence & RCA
Simulation
Recommendation
Approval
Verification

Do not create unnecessary top-level sections.

The core mental model is:

WHAT HAPPENED?
→ WHY?
→ WHAT CAN WE DO?
→ IS IT SAFE?
→ DID IT WORK?

==================================================
6. COMPLETE CLICKABLE PROTOTYPE
==================================================

After all core screens are established, create ONE coherent clickable prototype.

Primary flow:

Incident
→ Evidence / RCA
→ Simulation
→ Recommendation
→ Policy
→ Human Approval
→ Simulated Execution
→ Verification
→ Audit

The transitions must feel like one product workflow, not separate generated pages.

==================================================
7. FAILURE STATES
==================================================

Create reusable variants, not unnecessary standalone pages, for:

- Loading
- Analyzing
- Simulating
- Approval Required
- Executing
- Verifying
- Success
- NO_ACTION
- Policy Blocked
- Insufficient Evidence
- Stale Simulation
- Agent Unavailable
- Simulation Invalid
- Execution Rejected

These states must use the same design system.

Important:

AGENT UNAVAILABLE must NOT look like the whole system is broken.

Instead communicate:

"Agent unavailable"

while preserving:

"Deterministic incident analysis remains available."

==================================================
8. PRODUCT HONESTY
==================================================

Do NOT invent product capabilities.

Do NOT imply:

- live Razorpay infrastructure
- live gateway routing
- real production execution
- real production telemetry
- actual recovered GMV

The current environment is:

SIMULATION MODE
Synthetic infrastructure • Simulated execution • No live routing changes

Keep this distinction visible throughout the product.

==================================================
9. DATA FIDELITY
==================================================

Use only verified Aventum values where provided.

Known flagship examples may be shown:

- gateway_C
- 3-day incident
- 9.26σ significance
- 68.81% confidence
- 74.03% evidence strength
- CRITICAL severity
- gateway_C → gateway_A @ 30%
- ₹19,126.26 projected GMV retained
- +3.41% projected success improvement
- 20.83% → 17.42% simulated failure rate
- 79 transactions moved

Every modeled value must be labelled appropriately:

PROJECTED
SIMULATED

Observed values must remain clearly marked:

OBSERVED

Unavailable data must say:

UNAVAILABLE

Never fabricate capacity data.

Capacity:
UNAVAILABLE

Eligibility:
UNCONDITIONAL

==================================================
10. VISUAL DIRECTION
==================================================

Create an ORIGINAL premium fintech control-plane aesthetic.

Do NOT copy Razorpay's exact branding, logo, proprietary UI, or visual identity.

Aim for:

- premium fintech
- enterprise observability
- operational clarity
- precise typography
- sophisticated spacing
- restrained surfaces
- strong numerical hierarchy
- dense but readable information
- clear status semantics
- polished charts
- trustworthy decision interfaces

Avoid:

- generic SaaS styling
- giant chatbot interfaces
- neon AI aesthetics
- excessive gradients
- glassmorphism
- decorative AI imagery
- excessive cards
- unnecessary animation

The interface should feel operational, not decorative.

==================================================
11. PRIORITY ORDER
==================================================

When information competes for space, prioritize:

1. operational state
2. business impact
3. decision/action
4. safety/policy
5. evidence/explanation
6. technical provenance
7. decorative elements

If a technical detail is useful but secondary,
move it into:

- drawer
- expandable section
- tooltip
- secondary panel

Do NOT clutter the primary workflow.

==================================================
12. AGENT EXPERIENCE
==================================================

Do NOT build a full-screen ChatGPT clone.

Use a compact operational copilot:

AVENTUM AGENT

Show:

- current task
- tools used
- key findings
- decision
- evidence references
- uncertainty

Do NOT display chain-of-thought.

The UI should communicate:

AI interprets and orchestrates.

Deterministic systems provide authoritative numbers.

Policy controls what is permitted.

Human approval controls intervention.

==================================================
13. BUSINESS IMPACT
==================================================

Business impact must be visually prominent.

Prioritize:

- GMV at Risk
- Projected GMV Retained
- Failure Rate
- Success Rate
- Transactions Affected
- Latency

Do NOT confuse:

PROJECTED GMV

with:

ACTUAL / VERIFIED RESULT

The verification screen must make this distinction unmistakable.

==================================================
14. VERIFICATION IS A HERO MOMENT
==================================================

Treat verification as one of the most important product moments.

The screen must answer:

DID THE ACTION ACTUALLY WORK?

Show:

EXPECTED
vs
ACTUAL SIMULATED

Then show:

RECOVERY EFFECTIVE
or
PARTIALLY EFFECTIVE
or
RECOVERY NOT VERIFIED

The hierarchy must communicate:

EXECUTED ≠ VERIFIED

This should be visually obvious within seconds.

==================================================
15. SAFETY / POLICY EXPERIENCE
==================================================

Make the bounded-action model obvious.

Do not represent safety as one vague AI score.

Show deterministic gates such as:

RCA confidence
Evidence strength
Significance
Severity
Primary alert
Simulation validity
Simulation freshness
Target health
Target eligibility
Traffic bound
Concentration bound
Benefit threshold
Simulation provenance

Use clear:

PASS
FAIL
BLOCKED

states.

Central message:

AI PROPOSES.
POLICY DECIDES.

==================================================
16. HUMAN APPROVAL EXPERIENCE
==================================================

Approval must feel like a real financial-operation decision.

Show:

- proposed action
- expected benefit
- risk
- evidence
- policy result
- simulation
- provenance
- expiry

Provide:

Reject
Approve

Do NOT expose direct execution as a UI action before approval.

==================================================
17. AUDIT EXPERIENCE
==================================================

Audit should visually reconstruct:

Incident
→ Evidence
→ RCA
→ Simulation
→ Policy
→ Recommendation
→ Approval
→ Execution
→ Verification

Each event should expose:

- timestamp
- actor
- event type
- reference
- provenance
- fingerprint
- status

Do not expose hidden chain-of-thought.

==================================================
18. RESPONSIVE / DESKTOP TARGET
==================================================

Primary design target:

1440 × 900

Secondary:

1280 × 800

Prioritize desktop control-plane usability.

Ensure the layout degrades gracefully without compromising the primary desktop experience.

==================================================
19. INTERACTION QUALITY
==================================================

Prototype meaningful interactions:

- selecting an incident
- expanding evidence
- selecting a simulation
- opening simulation details
- expanding policy gates
- requesting approval
- approving/rejecting
- simulated execution state
- verification result
- audit-event expansion
- failure-state recovery

Do not animate merely for decoration.

==================================================
20. FINAL DESIGN QUALITY BAR
==================================================

The result should feel like:

A serious fintech operations product with embedded AI.

NOT:

A dashboard with an AI chatbot attached.

The visual hierarchy should make Aventum's differentiator obvious:

**Aventum does not let AI guess and act on payments.**

**It gives the agent deterministic evidence and bounded tools, keeps humans in control, executes only within explicit constraints, and independently verifies whether the resulting action actually worked.**

==================================================
21. FINAL DELIVERABLE STRUCTURE
==================================================

Create Figma pages in this order:

01 — Foundations
02 — Components
03 — Hero Screens
04 — Overview
05 — Human Approval
06 — Audit
07 — State Variants
08 — Prototype Flows

Under Hero Screens, prioritize:

1. Incident Command Center
2. Recovery Simulation
3. Recommendation + Safety
4. Execution + Verification

==================================================
22. FINAL SELF-CHECK
==================================================

Before considering the design complete, verify:

- Is the flagship incident understandable within 10 seconds?
- Is the business impact obvious?
- Is RCA visibly evidence-backed?
- Is simulation clearly separated from observation?
- Is the recommended action obvious?
- Are safety gates understandable?
- Is human approval unmistakable?
- Is execution distinguished from verification?
- Is failure handled gracefully?
- Is the agent visible without becoming the product?
- Is technical provenance available without clutter?
- Does the entire workflow feel like ONE coherent product?

If any answer is NO, improve hierarchy and interaction design rather than simply adding more components or screens.

Do NOT optimize for screen count.

Optimize for:
CLARITY
→ TRUST
→ DECISION QUALITY
→ OPERATIONAL USEFULNESS
→ BUSINESS IMPACT
→ FINTECH-GRADE POLISH.