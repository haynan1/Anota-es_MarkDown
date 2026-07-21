"""Data-access layer.

Repositories own query construction; services own business rules. Keeping the
two apart means a query can be tuned (indexes, eager loading, FTS) without
touching the rules that call it.
"""
