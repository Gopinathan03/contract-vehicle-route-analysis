from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


@contextmanager
def read_connection() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    connection = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
        row_factory=dict_row,
    )
    try:
        yield connection
    finally:
        connection.close()
