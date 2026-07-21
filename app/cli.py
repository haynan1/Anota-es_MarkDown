"""Custom ``flask`` CLI commands."""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext

from app.extensions import db

WELCOME_BODY = """# Bem-vindo ao Markdown Studio

Este documento existe para você experimentar o editor. Pode editar, exportar
ou mandar para a lixeira sem dó.

## O que dá para fazer

- Escrever em **Markdown** com pré-visualização ao vivo
- Organizar por *categorias* e etiquetas
- Voltar no tempo pelo histórico de versões
- Exportar em PDF ou baixar o `.md` original

## Sintaxe suportada

| Recurso   | Exemplo            |
|:----------|:-------------------|
| Negrito   | `**texto**`        |
| Itálico   | `*texto*`          |
| Código    | `` `codigo()` ``   |

- [x] Criar o primeiro documento
- [ ] Escrever alguma coisa boa

> Atalhos: `Ctrl+S` salva, `Ctrl+B` deixa em negrito, `Ctrl+K` insere um link.

```python
def bem_vindo(nome: str) -> str:
    return f"Ola, {nome}!"
```
"""


def register_commands(app: Flask) -> None:
    @app.cli.command("init-db")
    @with_appcontext
    def init_db() -> None:
        """Create every table and the search index."""
        from app.services.search_service import search_index

        db.create_all()
        search_index.ensure()
        click.echo("Banco de dados pronto.")

    @app.cli.command("seed-welcome")
    @with_appcontext
    def seed_welcome() -> None:
        """Create one example document (safe to delete afterwards)."""
        from app.models import Document
        from app.repositories.taxonomy_repository import CategoryRepository
        from app.services.document_service import DocumentService

        if db.session.scalar(db.select(db.func.count(Document.id))):
            click.echo("O banco já possui documentos. Nada foi criado.")
            return

        category = CategoryRepository.get_or_create("Guias", "#4F46E5")
        db.session.commit()
        document = DocumentService.create(
            title="Bem-vindo ao Markdown Studio",
            content_markdown=WELCOME_BODY,
            category_id=category.id,
            tag_names=["introdução", "exemplo"],
        )
        click.echo(f"Documento criado: {document.title}")

    @app.cli.command("reindex")
    @with_appcontext
    def reindex() -> None:
        """Rebuild the full-text search index."""
        from app.services.search_service import search_index

        total = search_index.rebuild()
        click.echo(f"{total} documento(s) reindexado(s).")

    @app.cli.command("backup")
    @click.option("--label", default="", help="Sufixo opcional para o nome do arquivo.")
    @with_appcontext
    def backup(label: str) -> None:
        """Create a ZIP backup in the configured backup directory."""
        from app.services.backup_service import create_backup

        info = create_backup(label=label)
        click.echo(f"Backup criado: {info.name} ({info.size_readable})")

    @app.cli.command("prune-media")
    @click.option(
        "--hours",
        default=24,
        show_default=True,
        help="Idade mínima de um arquivo para ser considerado órfão.",
    )
    @with_appcontext
    def prune_media(hours: int) -> None:
        """Remove uploads that no document references any more."""
        from app.services.media_service import prune_orphans

        rows, files = prune_orphans(max_age_hours=hours)
        if rows:
            click.echo(f"{rows} mídia(s) órfã(s) removida(s); {files} arquivo(s) apagado(s).")
        else:
            click.echo("Nenhuma mídia órfã encontrada.")

    @app.cli.command("pdf-engine")
    @with_appcontext
    def pdf_engine() -> None:
        """Report which PDF engine is active on this machine."""
        from app.services.pdf_service import engine_info

        info = engine_info()
        click.echo(f"Motor ativo:  {info['active_label']}")
        click.echo(f"WeasyPrint:   {'disponível' if info['weasyprint_available'] else 'indisponível'}")
        click.echo(f"xhtml2pdf:    {'disponível' if info['xhtml2pdf_available'] else 'indisponível'}")
