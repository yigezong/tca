---
name: linear
description: Use when working from a Linear issue, comment, URL, or issue ID to understand scope, plan implementation, synchronize status, or draft Linear updates for this project.
---

# Linear Workflow

Use this skill when the user references Linear work, including issue URLs, issue IDs, comments, project labels, status updates, or requests to prepare a Linear reply.

## Workflow

1. Identify the Linear item from the prompt.
   - Prefer an explicit Linear URL or issue ID.
   - If the prompt is ambiguous, ask for the exact issue URL or issue ID before changing code.

2. Gather issue context.
   - If a Linear MCP server, connector, or app tool is available, use it to read the issue, comments, labels, status, assignee, linked PRs, and acceptance criteria.
   - If no Linear access is available, ask the user to paste the issue content or authorize/configure Linear access.
   - Do not invent issue details that were not provided.

3. Turn the issue into an implementation plan.
   - Summarize the requested outcome.
   - Extract acceptance criteria and constraints.
   - Identify relevant files, tests, and commands before editing.
   - Keep the first implementation narrow unless the issue explicitly asks for a broader refactor.

4. Implement and verify.
   - Follow project instructions from AGENTS.md and nearby documentation.
   - Run the smallest relevant checks first, then broader checks when the change touches shared behavior.
   - Report commands run and their results.

5. Prepare Linear communication.
   - Draft concise status updates when asked.
   - Include what changed, what was verified, and any remaining risk or blocker.
   - If blocked, state the exact missing access, unclear requirement, or failing command.

## Guardrails

- Do not modify Linear issue state, assignee, labels, comments, or status unless the user explicitly asks and confirms the exact final action.
- Do not start implementation from a Linear reference until the issue content is available.
- Do not install dependencies or change project setup unless the issue requires it and the user approves.
- Keep project changes scoped to the Linear issue.
