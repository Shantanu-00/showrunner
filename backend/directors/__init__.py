"""The two planning agents (spec 05, spec 06).

Kept apart from `workers/` because the distinction is real and the fleet census depends on it
(HANDOFF §5): the perception workers make *judgments* and take no actions — they emit a structured
opinion onto a document and stop. The directors set a goal, plan, call guarded tools, and correct
themselves. Perception agents deliberately cannot act, and that separation is the trust architecture.
"""
