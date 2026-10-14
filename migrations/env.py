"""
Alembic environment.

Two things differ from the stock template and both matter:

  1. The URL comes from the Flask config (and therefore .env), never from
     alembic.ini. One place holds the connection string.
  2. target_metadata is the app's own metadata, so `alembic revision
     --autogenerate` sees the models in app/models.py.

THIS CHAIN IS OWNED BY THIS APPLICATION ALONE, AND THIS DATABASE IS NOT SHARED.

trainiq shares a schema with the marketing hub and therefore needs an
`include_object` filter to stop autogenerate emitting DROP TABLE for tables it
does not model. That filter is a permanent tax on every migration and a standing
invitation to a wrong one. Do not create a second instance of it: if someone
later proposes sharing a database with trainiq, the hub or promo, the answer is
no, and the reason is written in trainiq's own migrations/env.py.

Autogenerate is a DRAFT, not an answer. Review every revision by hand, and run
it forward AND backward against local Postgres before committing it.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import create_app, db

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the app so .env is loaded and the models are imported/registered.
flask_app = create_app()
import app.models  # noqa: E402, F401  — registers every table on db.metadata

config.set_main_option(
    'sqlalchemy.url',
    (flask_app.config.get('SQLALCHEMY_DATABASE_URI') or '').replace('%', '%%'),
)

target_metadata = db.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option('sqlalchemy.url'),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
