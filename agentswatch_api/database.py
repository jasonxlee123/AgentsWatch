import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ.get("AGENTSWATCH_DATABASE_URL")
        if not self.dsn:
            raise RuntimeError("AGENTSWATCH_DATABASE_URL is required")

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            yield connection

    def fetch_all(self, sql, params=None):
        with self.connection() as connection:
            return list(connection.execute(sql, params or {}).fetchall())

    def fetch_one(self, sql, params=None):
        with self.connection() as connection:
            return connection.execute(sql, params or {}).fetchone()


class WriteDatabase:
    def __init__(self, dsn=None):
        self.dsn = dsn or os.environ.get("AGENTSWATCH_WRITE_DATABASE_URL")
        if not self.dsn:
            raise RuntimeError("AGENTSWATCH_WRITE_DATABASE_URL is required for management operations")

    @contextmanager
    def connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row) as connection:
            with connection.transaction():
                yield connection
