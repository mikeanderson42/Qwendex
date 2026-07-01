---
name: qwendex-auto-manager-estimator
description: Lightweight Auto-mode estimator for Qwendex manager orchestration.
---

# Qwendex Auto Manager Estimator

Use this skill only when Qwendex Auto lacks enough deterministic signals to pick
Lite, Medium, Heavy, or Manager Mode. Skip the estimator when a command already
sets a mode, the task is clearly a one-file/low-risk fix, local Qwen is toggled
off for all local-only work, or policy already requires a high-risk lane.

Default call:
- model: GPT-5.5 or the configured primary GPT model
- reasoning: medium
- input budget: at most the configured estimator max input tokens
- output budget: at most the configured estimator max output tokens

Questionnaire:
1. What is the task complexity: simple, medium, heavy, or manager?
2. What is the risk: low, medium, or high?
3. What is the likely file scope: single_file, few_files, or many_files?
4. What validation depth is needed: quick, focused, or full?
5. How useful are subagents: low, medium, or high?
6. Which mode is recommended: auto, lite, medium, heavy, or manager?
7. What confidence applies: low, medium, or high?
8. Does any subagent lane need higher reasoning than medium?

Return JSON only:

```json
{
  "task_complexity": "simple|medium|heavy|manager",
  "risk": "low|medium|high",
  "likely_file_scope": "single_file|few_files|many_files",
  "validation_depth": "quick|focused|full",
  "subagent_usefulness": "low|medium|high",
  "recommended_mode": "auto|lite|medium|heavy|manager",
  "confidence": "low|medium|high",
  "higher_reasoning_lanes": [
    {
      "lane": "security-review",
      "selected_reasoning": "high|xhigh",
      "escalation_reason": "specific bounded reason"
    }
  ]
}
```

Escalation limits:
- Do not escalate the main session; it keeps the user's selected model and
  reasoning.
- Reserve high/xhigh for specific architecture, security, release, protocol,
  credential, or migration lanes.
- Prefer local Qwen only for bounded low-risk read-heavy lanes when the Local
  toggle is on and availability is confirmed.
