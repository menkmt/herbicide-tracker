"""Alembic environment.

The database URL comes from the application settings rather than alembic.ini,
so migrations always target the same database the app does and no credential
is stored in a config file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Leave PostGIS's own bookkeeping tables alone."""
    if type_ == "table" and name in {"spatial_ref_sys", "geometry_columns", "geography_columns"}:
        return False
    # GeoAlchemy2 manages spatial indexes itself; autogenerate should not try
    # to drop and recreate them on every revision.
    if type_ == "index" and name and name.startswith("idx_") and name.endswith(("_geom", "_boundary")):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
