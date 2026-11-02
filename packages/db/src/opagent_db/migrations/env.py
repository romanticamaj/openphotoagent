"""Alembic environment: always driven programmatically by ``opagent_db.lifecycle`` with a live connection."""

from alembic import context

from opagent_db.schema import metadata

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata, transaction_per_migration=True)
with context.begin_transaction():
    context.run_migrations()
