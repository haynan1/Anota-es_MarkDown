"""Schema reconciliation on startup.

The bug these cover: a database created by an earlier release stays behind
when new code arrives, and the feature whose migration never ran answers 500
on its first request. The application owns bringing its own schema forward.

Each test builds its own application against its own file, because the point
under test is what happens *before* a session exists - the shared ``app``
fixture has already created the schema by the time a test sees it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade

from app import SNAPSHOTS_KEPT, bootstrap_database, create_app
from app.extensions import db as _db
from app.extensions import migrate


def build(tmp_path, database, **overrides):
    application = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
            "BACKUP_DIR": tmp_path / "backups",
            "EXPORT_DIR": tmp_path / "exports",
            "UPLOAD_DIR": tmp_path / "uploads",
            "TESTING": True,
            **overrides,
        },
    )
    with application.app_context():
        bootstrap_database(application)
    return application


def head(application):
    with application.app_context():
        return ScriptDirectory.from_config(migrate.get_config()).get_current_head()


def revision(database):
    connection = sqlite3.connect(database)
    try:
        row = connection.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()


def tables(database):
    connection = sqlite3.connect(database)
    try:
        return {
            name for (name,) in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()


def schema(database):
    """Every ``(table, column)`` the database has.

    Compared before and after rather than inspected by name, so the test says
    "the migration ran and the schema actually moved" without having to be
    rewritten into a lie every time the head advances.
    """
    connection = sqlite3.connect(database)
    try:
        found = set()
        for (table,) in connection.execute(
            "select name from sqlite_master where type='table'"
        ):
            for row in connection.execute(f"pragma table_info({table})"):
                found.add((table, row[1]))
        return found
    finally:
        connection.close()


def rewind(application):
    """Put a database back in the state the previous release left it in.

    By running the head migration's own ``downgrade`` rather than by undoing
    its effects by hand here. Two things follow from that. It stays correct
    when the head moves - a hardcoded list of tables to drop describes one
    particular migration, and quietly stops describing the schema the moment
    another one lands, which is how a test about *being behind* ends up
    testing nothing. And it means every release's migration is exercised in
    both directions, which is the promise each of them makes.
    """
    with application.app_context():
        downgrade(revision="-1")


def previous(application):
    with application.app_context():
        script = ScriptDirectory.from_config(migrate.get_config())
        return script.get_revision(script.get_current_head()).down_revision


def test_fresh_database_is_created_and_stamped(tmp_path):
    """A fresh clone must not leave Alembic with nothing to build on."""
    database = tmp_path / "fresh.db"
    application = build(tmp_path, database)

    assert "documents" in tables(database)
    assert revision(database) == head(application)


def test_pending_migration_is_applied_on_startup(tmp_path):
    database = tmp_path / "behind.db"
    application = build(tmp_path, database)
    behind = previous(application)
    rewind(application)
    assert revision(database) == behind

    before = schema(database)
    build(tmp_path, database)
    after = schema(database)

    assert revision(database) == head(application)
    # Not only the number: a test that compared revisions alone would pass
    # against a migration that ran and did nothing at all.
    assert after != before, "a migration da vez subiu sem mexer no schema"
    # And the concrete fact, which names today's head on purpose. This line
    # moves with the head; a generic assertion cannot tell "did the right
    # thing" from "did some thing".
    assert ("mind_map_nodes", "mirror_of_id") in after - before


def test_pending_migration_leaves_a_snapshot_behind(tmp_path):
    database = tmp_path / "snapshot.db"
    application = build(tmp_path, database)
    behind = previous(application)
    rewind(application)

    build(tmp_path, database)

    snapshots = list((tmp_path / "backups" / "pre-migracao").glob("*.db"))
    assert len(snapshots) == 1
    # A copy, not a placeholder: it still answers as the database it was.
    assert revision(snapshots[0]) == behind
    # And it stays out of the backup list the user actually chose to build.
    assert not list((tmp_path / "backups").glob("*.zip"))


def test_snapshots_do_not_accumulate(tmp_path):
    database = tmp_path / "many.db"
    application = build(tmp_path, database)
    behind = previous(application)

    for _ in range(SNAPSHOTS_KEPT + 2):
        rewind(application)
        build(tmp_path, database)

    snapshots = list((tmp_path / "backups" / "pre-migracao").glob("*.db"))
    assert len(snapshots) == SNAPSHOTS_KEPT


def test_startup_is_idempotent(tmp_path):
    database = tmp_path / "again.db"
    application = build(tmp_path, database)

    build(tmp_path, database)
    build(tmp_path, database)

    assert revision(database) == head(application)
    assert not (tmp_path / "backups" / "pre-migracao").exists()


def test_unversioned_database_is_left_alone(tmp_path):
    """Guessing a revision would apply the wrong upgrade to real data."""
    database = tmp_path / "legacy.db"
    build(tmp_path, database)
    connection = sqlite3.connect(database)
    connection.execute("delete from alembic_version")
    connection.commit()
    connection.close()

    build(tmp_path, database)

    assert revision(database) is None
    assert not (tmp_path / "backups" / "pre-migracao").exists()


def test_opting_out_leaves_the_schema_to_flask_db(tmp_path):
    """AUTO_CREATE_DB=0 is what generating a migration relies on."""
    database = tmp_path / "manual.db"
    build(tmp_path, database, AUTO_CREATE_DB=False)

    assert "documents" not in tables(database)


def rows_of(database, table):
    connection = sqlite3.connect(database)
    try:
        return connection.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        connection.close()


def seed_a_map(database):
    """Um mapa com uma árvore de três níveis, escrito em SQL cru.

    Cru porque o ORM escreve as colunas de hoje, e este teste semeia bancos
    parados em versões antigas do schema - é justamente essa a situação que
    ele existe para exercitar.

    Uma tabela que referencia a si mesma é a única em que a reescrita de
    tabela do SQLite pode disparar cascades contra as próprias linhas que
    acabou de copiar, e é por isso que o mapa é o que se semeia aqui.
    """
    agora = "2026-01-01 00:00:00"
    connection = sqlite3.connect(database)
    try:
        def insert(table, values):
            # Cada versão do schema tem as suas colunas e as suas obrigações.
            # Em vez de listar as de todas elas, as que faltam ganham um vazio
            # do tipo certo - o teste é sobre não perder linhas, não sobre o
            # conteúdo delas.
            colunas_info = list(connection.execute(f"pragma table_info({table})"))
            usadas = {}
            for _, nome, tipo, notnull, default, _pk in colunas_info:
                if nome in values:
                    usadas[nome] = values[nome]
                elif notnull and default is None and nome != "id":
                    usadas[nome] = 0 if tipo.upper() in ("INTEGER", "FLOAT", "REAL") else ""
            colunas = ",".join(usadas)
            marcas = ",".join("?" * len(usadas))
            cur = connection.execute(
                f"insert into {table} ({colunas}) values ({marcas})",
                list(usadas.values()),
            )
            return cur.lastrowid

        mapa = insert("mind_maps", {
            "uuid": "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa", "title": "Mapa",
            "slug": "mapa-dados", "description": "", "color": "#4F46E5",
            "layout": "right", "revision": 1, "viewport_x": 0.0, "viewport_y": 0.0,
            "viewport_zoom": 1.0, "is_favorite": 0, "is_deleted": 0,
            "created_at": agora, "updated_at": agora,
        })
        anterior = None
        for nivel, texto in enumerate(("Raiz", "Meio", "Fundo")):
            anterior = insert("mind_map_nodes", {
                "uuid": f"{nivel + 1}" * 8 + "-1111-4111-8111-111111111111",
                "map_id": mapa, "parent_id": anterior, "position": 0,
                "kind": "topic", "text": texto, "note": "", "url": "",
                "image_url": "", "x": 0.0, "y": 0.0, "width": 180.0, "height": 48.0,
                "color": "", "shape": "rounded", "is_collapsed": 0,
                "created_at": agora, "updated_at": agora,
            })
        insert("documents", {
            "uuid": "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb", "title": "Um documento",
            "slug": "um-documento", "content_markdown": "", "content_html": "",
            "excerpt": "", "word_count": 0, "reading_minutes": 0,
            "is_favorite": 0, "is_archived": 0, "is_deleted": 0,
            "created_at": agora, "updated_at": agora,
        })
        connection.commit()
    finally:
        connection.close()


def test_an_upgrade_never_takes_the_data_with_it(tmp_path):
    """Subir de qualquer versão até a atual não pode apagar linha nenhuma.

    O defeito que isto fixa custou o mapa mental de quem usa o sistema.

    SQLite não sabe alterar uma coluna: o Alembic reescreve a tabela inteira -
    cria uma temporária, copia as linhas, **derruba a original** e renomeia. A
    aplicação liga ``PRAGMA foreign_keys`` em toda conexão, porque sem isso os
    ``ON DELETE CASCADE`` não fazem nada; e com ela ligada, aquele DROP dispara
    os cascades que apontam para a tabela sendo reescrita. Numa tabela que
    referencia a si mesma - os tópicos de um mapa, cada um filho de outro -
    isso apaga todo filho e deixa só as raízes.

    Passou por vinte migrações sem aparecer porque nenhuma outra tabela do
    sistema aponta para si mesma. Apareceu na primeira que reescreveu a dos
    tópicos, e apareceu como uma tela em branco.

    A propriedade é sobre *subir*: um ``downgrade`` pode legitimamente
    descartar o que a versão nova trouxe, mas atualizar um banco em produção
    nunca pode perder o que já estava lá. Cada degrau da cadeia é semeado com
    dados e subido até o topo, um de cada vez, para que a falha aponte a
    migração culpada em vez de dizer só que a cadeia perdeu linhas.
    """
    application = build(tmp_path, tmp_path / "escada.db")
    with application.app_context():
        script_dir = ScriptDirectory.from_config(migrate.get_config())
        chain = [rev.revision for rev in script_dir.walk_revisions()][::-1]

    testadas = 0
    for target in chain[:-1]:
        database = tmp_path / f"de-{target}.db"
        one = build(tmp_path, database)
        with one.app_context():
            downgrade(revision=target)

        # Só faz sentido a partir da versão em que a árvore existe.
        if "mind_map_nodes" not in tables(database):
            continue
        seed_a_map(database)

        antes = {
            table: rows_of(database, table)
            for table in ("documents", "mind_maps", "mind_map_nodes")
        }
        assert antes["mind_map_nodes"] == 3, "o teste precisa de uma árvore de verdade"

        with one.app_context():
            upgrade()

        for table, total in antes.items():
            assert rows_of(database, table) == total, (
                f"subir de {target} até o topo levou linhas de {table}: "
                f"{total} viraram {rows_of(database, table)}"
            )
        testadas += 1

    assert testadas, "nenhuma versão da cadeia tinha a árvore para semear"


def test_the_migrations_run_with_foreign_keys_off(tmp_path):
    """E a razão de tudo isso, dita onde ela vale.

    Uma pragma que não pegou é a diferença entre uma migração e uma perda de
    dados, então ``env.py`` não confia: ele confere e recusa migrar se não
    conseguir desligá-las.
    """
    source = Path("migrations", "env.py").read_text(encoding="utf-8")

    assert "PRAGMA foreign_keys=OFF" in source
    assert "dbapi_connection" in source, "precisa ser na conexão crua"
    assert "connection.rollback()" in source, (
        "a pragma é ignorada em silêncio dentro de uma transação"
    )
    assert "PRAGMA foreign_key_check" in source, (
        "o que a migração deixou quebrado tem de aparecer agora"
    )
    assert "raise RuntimeError" in source, (
        "não conseguir desligar as chaves tem de parar a migração"
    )
