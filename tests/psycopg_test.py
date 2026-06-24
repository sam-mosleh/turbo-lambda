import time
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from tests.utils import settings
from turbo_lambda.errors import UnoptimizedQueryError
from turbo_lambda.psycopg import SeqScanDetectingRawCursor

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(scope="session")
def db() -> Generator[psycopg.Connection[dict[str, Any]]]:
    # logging.getLogger("psycopg").setLevel(logging.DEBUG)
    test_db_name = f"test_{time.time_ns()}"
    with psycopg.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        dbname=settings.DB_NAME,
        autocommit=True,
        connect_timeout=settings.DB_CONNECT_TIMEOUT,
        row_factory=dict_row,
        cursor_factory=psycopg.RawCursor,
    ) as default_conn:
        default_conn.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db_name))
        )
    with psycopg.connect(
        host=settings.DB_HOST,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        dbname=test_db_name,
        autocommit=True,
        connect_timeout=settings.DB_CONNECT_TIMEOUT,
        row_factory=dict_row,
        cursor_factory=SeqScanDetectingRawCursor,
    ) as test_conn:
        test_conn.execute("CREATE TABLE table1(id SERIAL PRIMARY KEY, name TEXT)")
        test_conn.execute("VACUUM ANALYZE")
        test_conn.execute("SET enable_seqscan = off")
        yield test_conn


def test_execute_skips_non_explainable_queries(
    db: psycopg.Connection[dict[str, Any]],
) -> None:
    with db.pipeline(), db.cursor() as cur:
        cur.execute("SET application_name = 'seq-scan-detector-test'")
        assert cur.statusmessage == "SET"
        cur.execute(
            sql.SQL("SELECT * FROM table1 WHERE id = $1"),
            (1,),
        )
        assert cur.statusmessage == "SELECT 0"


def test_execute_detects_sequential_scan(
    db: psycopg.Connection[dict[str, Any]],
) -> None:
    with db.cursor() as cur, pytest.raises(UnoptimizedQueryError):
        cur.execute(
            sql.SQL("SELECT id FROM table1 WHERE name = $1"),
            ("absent",),
        )


def test_executemany_skips_non_explainable_queries(
    db: psycopg.Connection[dict[str, Any]],
) -> None:
    with db.pipeline(), db.cursor() as cur:
        cur.executemany(
            "SELECT * FROM table1 WHERE id = $1",
            ((rec_id,) for rec_id in [1, 2]),
        )
        assert cur.statusmessage == "SELECT 0"
        cur.executemany("RESET application_name", [()])
        assert cur.statusmessage == "RESET"


def test_executemany_detects_sequential_scan(
    db: psycopg.Connection[dict[str, Any]],
) -> None:
    with db.cursor() as cur, pytest.raises(UnoptimizedQueryError):
        cur.executemany(
            "SELECT id FROM table1 WHERE name = $1",
            [("absent",)],
        )
