"""Business logic.

Services own the rules; blueprints only translate HTTP to service calls and
back. Nothing here imports Flask views, so every rule is testable in isolation.
"""
