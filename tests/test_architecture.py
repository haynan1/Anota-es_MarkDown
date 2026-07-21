"""Structural invariants.

An architecture that is only described in a README erodes. These tests make
the layering, the dependency direction and the senior baseline executable, so
a violation fails CI instead of surviving review.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# Lower layers may not import higher ones.
LAYER_ORDER = ["utils", "models", "repositories", "services", "blueprints"]


def python_files():
    return [
        path
        for path in APP.rglob("*.py")
        if "__pycache__" not in str(path)
    ]


def module_name(path: pathlib.Path) -> str:
    parts = path.relative_to(APP.parent).with_suffix("").parts
    name = ".".join(parts)
    return name[: -len(".__init__")] if name.endswith(".__init__") else name


def layer_of(module: str) -> str | None:
    for layer in LAYER_ORDER:
        if module.startswith(f"app.{layer}"):
            return layer
    return None


def app_imports(path: pathlib.Path, module_level_only: bool = False):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if module_level_only and getattr(node, "col_offset", 0) != 0:
            continue
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.startswith("app"))
    return found


class TestLayering:
    def test_no_layer_imports_a_higher_one(self):
        """utils <- models <- repositories <- services <- blueprints."""
        violations = []
        for path in python_files():
            source_layer = layer_of(module_name(path))
            if source_layer is None:
                continue
            for imported in app_imports(path):
                target_layer = layer_of(imported)
                if target_layer is None:
                    continue
                if LAYER_ORDER.index(target_layer) > LAYER_ORDER.index(source_layer):
                    violations.append(
                        f"{module_name(path)} ({source_layer}) importa "
                        f"{imported} ({target_layer})"
                    )
        assert not violations, "violações de camada:\n  " + "\n  ".join(violations)

    def test_repositories_never_import_services(self):
        """The data layer must not depend on business rules."""
        for path in (APP / "repositories").rglob("*.py"):
            for imported in app_imports(path):
                assert not imported.startswith("app.services"), (
                    f"{path.name} importa {imported}"
                )

    def test_services_never_import_blueprints(self):
        """Business rules must be testable without HTTP."""
        for path in (APP / "services").rglob("*.py"):
            for imported in app_imports(path):
                assert not imported.startswith("app.blueprints"), (
                    f"{path.name} importa {imported}"
                )

    def test_models_do_not_import_services_or_repositories(self):
        for path in (APP / "models").rglob("*.py"):
            for imported in app_imports(path, module_level_only=True):
                assert not imported.startswith(("app.services", "app.repositories"))


class TestSeniorBaseline:
    """Rules that are binary, not matters of taste."""

    def test_no_hardcoded_secrets(self):
        """Including in test configuration - a checked-in key is a real key."""
        pattern = re.compile(
            r'(SECRET_KEY|PASSWORD|API_KEY|TOKEN)\s*=\s*["\'][^"\']{8,}["\']',
            re.IGNORECASE,
        )
        for path in python_files():
            for line in path.read_text(encoding="utf-8").splitlines():
                if pattern.search(line) and "os.getenv" not in line:
                    pytest.fail(f"segredo embutido em {path.name}: {line.strip()}")

    def test_broad_excepts_are_justified(self):
        """`except Exception` is allowed only at a documented boundary."""
        for path in python_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines, start=1):
                if "except Exception" not in line and "except BaseException" not in line:
                    continue
                assert "noqa: BLE001" in line, (
                    f"{path.name}:{number} captura genérica sem justificativa "
                    f"explícita: {line.strip()}"
                )

    def test_no_bare_except(self):
        for path in python_files():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    pytest.fail(f"except nu em {path.name}:{node.lineno}")

    def test_no_any_type_annotations(self):
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "Any":
                    pytest.fail(f"tipo Any em {path.name}:{node.lineno}")

    def test_no_print_statements_in_the_application(self):
        """run.py is a CLI entry point; the application package logs instead."""
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    pytest.fail(f"print() em {path.name}:{node.lineno}")

    def test_no_eval_or_exec(self):
        for path in python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"eval", "exec"}
                ):
                    pytest.fail(f"{node.func.id}() em {path.name}:{node.lineno}")

    def test_raw_sql_is_confined_to_the_search_index(self):
        """FTS5 needs raw SQL; nothing else may build statements by hand.

        Matched on a word boundary so ``app_context()`` is not mistaken for
        SQLAlchemy's ``text()``.
        """
        raw_sql = re.compile(r"\btext\(")
        for path in python_files():
            source = path.read_text(encoding="utf-8")
            if not raw_sql.search(source):
                continue
            assert path.name in {"search_service.py", "extensions.py"}, (
                f"SQL textual fora do índice de busca: {path.name}"
            )

    def test_raw_sql_never_interpolates_user_input(self):
        source = (APP / "services" / "search_service.py").read_text(encoding="utf-8")
        for match in re.finditer(r'text\(\s*f?"""?(.*?)"""?\s*\)', source, re.S):
            statement = match.group(1)
            # Only the table name may be interpolated; values are bound.
            placeholders = re.findall(r"\{(\w+)\}", statement)
            assert set(placeholders) <= {"FTS_TABLE"}, (
                f"interpolação inesperada em SQL: {placeholders}"
            )


class TestListingService:
    """The seam introduced to keep the repository free of service imports."""

    def test_search_is_resolved_before_pagination(self, app, make_document):
        from app.repositories.document_repository import DocumentQuery
        from app.services.listing_service import list_documents

        make_document(title="Encontrável", content="conteúdo com xyzzy")
        query = DocumentQuery(search="xyzzy", per_page=10)

        result = list_documents(query)

        assert result.total == 1
        assert query.matched_ids is not None

    def test_snippets_are_produced_for_every_result(self, app, make_document):
        from app.repositories.document_repository import DocumentQuery
        from app.services.listing_service import list_documents

        make_document(title="Alvo", content="um texto contendo marketing digital")
        result = list_documents(DocumentQuery(search="marketing", per_page=10))

        assert result.items
        for document in result.items:
            assert document.id in result.snippets

    def test_like_fallback_still_returns_snippets(self, app, db, make_document):
        """With no FTS table, results must still be highlighted."""
        from sqlalchemy import text as sql_text

        from app.repositories.document_repository import DocumentQuery
        from app.services.listing_service import list_documents
        from app.services.search_service import search_index

        make_document(title="Fallback", content="texto com marketing digital")
        db.session.execute(sql_text("DROP TABLE IF EXISTS documents_fts"))
        db.session.commit()
        search_index._available_by_engine.clear()

        result = list_documents(DocumentQuery(search="marketing", per_page=10))

        assert result.used_full_text is False
        assert result.total == 1
        assert result.snippets

    def test_empty_search_results_match_nothing(self, app, make_document):
        from app.repositories.document_repository import DocumentQuery
        from app.services.listing_service import list_documents

        make_document(title="Existe", content="conteúdo")
        result = list_documents(DocumentQuery(search="termoquenaoexiste", per_page=10))

        assert result.total == 0
