# Master Workflow — AI Development Workforce

**Status:** Operating specification; implementation is staged and must be verified before being treated as autonomous.

## 1. Source of truth and priority

The Notion page **Development Control Centre — Master Project Register** is the authoritative portfolio register. Every work cycle starts by reading it, selecting the highest-priority eligible project, and checking blockers and dependencies. Do not maintain a competing priority list in this repository.

Before implementation begins, each project must have an approved specification covering objective, scope, exclusions, dependencies, constraints, required engine/model for each agent, acceptance criteria, and approval gates.

## 2. End-to-end sequence

1. Read the Master Project Register.
2. Select the highest-priority eligible project.
3. Define and approve scope, requirements, exclusions, dependencies, required engines/models, and acceptance checks.
4. ChatGPT creates a dedicated child handover under Notion **Handovers from ChatGPT**.
5. Claude implements only the approved handover in bounded batches.
6. After each batch, save a resumable checkpoint and evidence.
7. If the required engine/model is unavailable or its usage allowance is exhausted, pause the affected agent.
8. Resume only when the same required engine/model is available again.
9. Run relevant GitHub Actions and project acceptance checks.
10. ChatGPT reviews implementation evidence and checks results against the approved scope.
11. Update the Master Project Register only after verification; then move to the next eligible priority.

## 3. Strict engine/model policy

- Select the best suitable engine/model for each project's actual requirements and record the choice and rationale in the handover.
- Claude and ChatGPT follow this policy independently.
- No silent fallback to a weaker, cheaper, or alternate model is allowed.
- If the required engine/model is unavailable, the task remains paused or blocked. It must not continue under another model without explicit approval to change the requirement.
- Do not claim to detect a usage reset or engine availability automatically unless an authorised, reliable signal is available and tested.
- If the service does not expose such a signal, use a scheduled recheck or explicit confirmation. Unknown availability means blocked, not permission to continue.

## 4. Batch checkpoints and resume

Every batch report/checkpoint must record:

- Project ID/name and Notion handover link.
- Current status and required engine/model.
- Last completed step and exact next step.
- Files changed and relevant branch.
- Commit SHA/PR link only when verified.
- Tests/checks actually run and their real results.
- Blockers, wait condition, and reset/availability evidence if available.
- Resume instructions and approvals needed.

A checkpoint must be sufficient to resume without losing verified work or unnecessarily repeating completed tasks.

## 5. Status values

- **New:** handover ready; work not started.
- **In progress:** work is proceeding with the required engine/model.
- **Paused — usage/reset:** state saved; waiting for the required engine's allowance or availability to recover.
- **Blocked — engine unavailable:** required engine is not available and no reliable recovery signal exists.
- **Blocked — decision needed:** scope, permission, access, or approval unresolved.
- **Ready for ChatGPT review:** implementation report and evidence are available.
- **Done:** all stated acceptance criteria have been verified.

A green workflow proves only the checks that workflow actually performs. It is not sufficient by itself when the acceptance criteria include additional build, installation, user-journey, security, or content checks.

## 6. Controller and GitHub Actions safety

The controller should be implemented in small, testable stages:

1. Validate repository baseline and existing tests.
2. Add a state/checkpoint schema and tests.
3. Add a manual-dispatch controller that validates state and reports the next safe action.
4. Add bounded scheduled rechecks only where they are useful and do not falsely imply model availability.
5. Integrate Notion only through an authorised, tested route.
6. Add real engine execution only if the relevant provider exposes a supported and authorised API/interface and the user has approved the integration.

Until an integration is proven, document the step as manual. Do not fake API support, scrape logged-in sessions, or assume the system can read private ChatGPT/Claude usage counters.

Use least-privilege permissions, concurrency safeguards, timeouts, clear logs, and explicit failure states. Unknown, invalid, or missing state must fail closed.

## 7. Approval gates

Require explicit approval before:
- Merging into a protected/default branch.
- Deleting, overwriting unrelated work, renaming, or archiving repositories.
- Changing the approved scope or required model/engine.
- Deploying to production or publishing externally.
- Enabling paid services, billing, or new permissions.
- Performing any destructive or consequential action.

## 8. Completion and reporting

A project may be marked **Done** only when the specified acceptance checks have evidence. Record actual files, test results, commit/PR references, unresolved limitations, and the next priority. Never invent results or claim an integration is operational before a real end-to-end test.

## 9. Current implementation constraint

This file defines the required behaviour; it does not claim the full workforce is already autonomous. The repository currently has ZIP inbox workflows. The controller, Notion automation, provider execution routes, usage-reset detection, and same-engine resume must each be implemented and verified separately.
