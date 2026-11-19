from logging.config import fileConfig
import os
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

project_root = Path(config.config_file_name).resolve().parent.parent
sys.path.insert(0, str(project_root))
from backend.app.database import Base  # noqa: E402
from backend.app import models  # noqa: F401,E402
from backend.app.config import database_connect_args, normalize_database_url, settings  # noqa: E402

target_metadata = Base.metadata
ini_url = config.get_main_option("sqlalchemy.url")
database_url = (
    config.attributes.get("database_url")
    or os.getenv("ALEMBIC_DATABASE_URL")
    or os.getenv("DATABASE_URL")
    or (settings.database_url if settings.database_url != ini_url else ini_url)
)
if database_url:
    config.set_main_option("sqlalchemy.url", normalize_database_url(database_url).replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    if provided_connection is not None:
        context.configure(connection=provided_connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    connect_args = database_connect_args(
        config.get_main_option("sqlalchemy.url"),
        settings.database_ssl_mode,
        settings.database_disable_prepared_statements,
    )
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
