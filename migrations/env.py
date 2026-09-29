from alembic import context
from sqlalchemy import engine_from_config, pool

from database import Base
import models

config = context.config
config.set_main_option("sqlalchemy.url", __import__("os").getenv("DATABASE_URL", "sqlite:///./naijaclip.db"))
target_metadata = Base.metadata


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
