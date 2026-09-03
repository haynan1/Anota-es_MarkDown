import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


# The SQLite FTS5 search index is created with raw DDL (see
# app/services/search_service.py) and deliberately kept out of the ORM
# metadata. Without this filter every autogenerate proposes dropping it and
# its shadow tables, which would wipe the search index on the next upgrade.
FTS_TABLE_PREFIX = 'documents_fts'


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == 'table' and name and name.startswith(FTS_TABLE_PREFIX):
        return False
    return True


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=get_metadata(),
        literal_binds=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives
    conf_args.setdefault("include_object", include_object)

    connectable = get_engine()

    with connectable.connect() as connection:
        restore_foreign_keys = _disable_foreign_keys(connection)

        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        try:
            with context.begin_transaction():
                context.run_migrations()
        finally:
            restore_foreign_keys()


def _disable_foreign_keys(connection):
    """Desliga as chaves estrangeiras enquanto as migrações rodam.

    SQLite não sabe alterar uma coluna: o Alembic reescreve a tabela inteira -
    cria uma temporária, copia as linhas, **derruba a original** e renomeia.
    A aplicação liga ``PRAGMA foreign_keys`` em toda conexão, porque sem isso
    os ``ON DELETE CASCADE`` não fazem nada; e com ela ligada, aquele DROP
    dispara os cascades que apontam para a tabela sendo reescrita. Numa tabela
    que referencia a si mesma - os tópicos de um mapa, cada um filho de outro -
    isso apaga todo filho e deixa só as raízes.

    A própria documentação do SQLite manda desligá-las durante uma
    reconstrução de tabela. Aqui, e não em cada migração uma a uma, até alguém
    esquecer.

    Na conexão crua e com a transação fechada, porque ``PRAGMA foreign_keys``
    é ignorada em silêncio dentro de uma transação - e uma pragma ignorada em
    silêncio aqui é a diferença entre uma migração e uma perda de dados. Se
    ela não pegar, a migração não roda.
    """
    if connection.dialect.name != "sqlite":
        return lambda: None

    raw = connection.connection.dbapi_connection
    connection.rollback()
    raw.execute("PRAGMA foreign_keys=OFF")
    if raw.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError(
            "Não foi possível desligar as chaves estrangeiras para migrar. "
            "Migrar com elas ligadas pode apagar linhas ao reescrever uma "
            "tabela, então a migração foi interrompida."
        )

    def restore() -> None:
        # Depois, e não antes: uma violação que a migração tenha deixado deve
        # aparecer agora, e não no primeiro pedido de quem for usar o sistema.
        connection.rollback()
        violations = raw.execute("PRAGMA foreign_key_check").fetchall()
        raw.execute("PRAGMA foreign_keys=ON")
        if violations:
            raise RuntimeError(
                f"A migração deixou {len(violations)} referências quebradas: "
                f"{violations[:3]}"
            )

    return restore


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
