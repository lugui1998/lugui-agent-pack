# Planning around multiple expiring resets

Status: proposal for discussion. No evaluator, agent launch or model change is
implemented by this document.

## Objective

Help the user finish useful work before banked capacity expires, while respecting
their credit budget and preferred working schedule. Measure value by completed
work and avoided interruptions. Token consumption alone is not a useful outcome.

## Proposed trigger

The hook could flag a planning opportunity when:

- At least two available resets have known expirations within the existing
  near-expiration window.
- Their expirations are close enough that ordinary usage may leave one unused.
- The current interaction concerns active work, or the user has explicitly
  described work they plan to do.
- That inventory and its expirations have not already been evaluated.

The exact meaning of "close enough" remains a design choice. Using the existing
five-day window as the first filter avoids adding another setting before examples
show that it is needed. A near-expiration inventory alone does not establish that
the user should work more.

## Division of responsibility

The hook gathers a bounded summary from existing observations and emits a planning
notice. It does not launch another Codex process, start a background task, select a
more expensive model or change the reset policy.

The main agent can discuss the opportunity with the user and use a brainstorming
agent to compare plans. The evaluator proposes work and timing. Redemption remains
with the deterministic hook and its existing eligibility, consent, locking and
recovery rules.

An installation preference could separately enable these planning suggestions.
Consent to automatic redemption does not by itself authorize extra planning runs
or more expensive work.

## Inputs

| Input | Purpose |
| --- | --- |
| Available reset count and known expirations | Identify capacity that might expire unused |
| Current quota and each natural reset time | Estimate when another reset might help |
| Recent aggregate usage, with its date range and gaps | Provide a rough consumption estimate |
| Credit reserve and saved reset preferences | Respect the user's spending choices |
| Active work, priorities and user availability | Tie recommendations to useful results |

Treat incomplete inventory and missing history as uncertainty. Do not infer future
availability from transcripts or personal data. Use cached observations first;
avoid recurring queries or agent calls just to refine a speculative forecast.

## Questions for the user

Ask only for information needed for the proposed plan:

- Do you expect to keep working before these resets expire?
- Which planned tasks would benefit most from the available capacity?
- Would you prefer to preserve credits, finish sooner or keep your current pace?

A more capable model is a useful suggestion when a planned task benefits from its
judgment, such as a difficult design decision or review. The agent should explain
that expected benefit and obtain the user's choice before changing models or
starting extra work.

## Suggested output

Present the relevant expiration and renewal times, a short recommendation, and its
main uncertainty. For example:

> Two resets expire over the next few days. At your recent pace, you may need only
> one before then. Do you expect to work on the architecture review this week?
> If so, we can discuss whether a more capable model would help with that review.

The proposal can recommend keeping the current plan. It should not invent work to
use an expiring reset.

## Frequency and persistence

Keep a durable record keyed by the relevant reset identities and expirations.
Ordinary quota fluctuations and repeated tool events should not trigger another
evaluation. Use a cooldown as an additional bound; inventory changes caused by
using one reset should not produce a chain of planning calls.

Allow the user to dismiss the suggestion for that inventory or request another
assessment after their plans change. Idle time does not start a new conversation.

## Decisions before implementation

Validate a few fictitious scenarios first:

1. Two resets expire soon, and the user has substantial work planned.
2. Several resets expire soon, but the user is about to stop working.
3. Credits are low and natural quota recovery is close.
4. Inventory details or usage history are incomplete.

These examples should settle the opt-in default, the expiration clustering rule,
what counts as planned work, and the maximum frequency and cost of evaluations.
