"""eval-service: LLM-as-judge evaluation service.

Mirrors a production LLM-eval service: defines Scenarios with a
success_criteria (a semantic reference, not a literal script), drives a short
conversation against a target agent, then has an LLM judge grade the transcript
pass/fail against the success criteria.
"""
