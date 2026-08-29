"""Small helpers over the ORM session that no layer above should reinvent."""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect


def identity_of(instance) -> int:
    """The primary key of ``instance`` without ever going back to the database.

    SQLAlchemy expires every instance on commit, so reading ``.id`` in a loop
    after any earlier commit issues one SELECT per object - an N+1 that appears
    and disappears depending on what the caller did first. The identity key is
    already in the session for any persisted object, so this reads it from
    there and falls back to the attribute only for something never flushed.
    """
    identity = sa_inspect(instance).identity
    return identity[0] if identity else instance.id


def identities_of(instances) -> list[int]:
    """The primary keys of a whole selection, in order and without duplicates.

    The shape every bulk path wants: a caller holding documents hands them over
    once, and everything downstream works on identifiers.
    """
    return list(dict.fromkeys(identity_of(instance) for instance in instances))
