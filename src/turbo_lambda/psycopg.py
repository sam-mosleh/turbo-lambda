from typing import TYPE_CHECKING, Any

import psycopg
from psycopg import pq, sql
from psycopg.raw_cursor import RawCursorMixin
from psycopg.rows import Row

from turbo_lambda.errors import UnoptimizedQueryError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Self

    from psycopg.abc import Params, Query


_EXPLAINABLE_COMMAND_TAGS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def _plan_uses_seq_scan(node: dict[str, Any]) -> bool:
    if "Seq Scan" in str(node.get("Node Type", "")):
        return True
    return any(_plan_uses_seq_scan(child) for child in node.get("Plans", []))


def _reject_seq_scan(query: str, payload: list[dict[str, Any]]) -> None:
    root_plan = payload[0]["Plan"]
    if _plan_uses_seq_scan(root_plan):
        raise UnoptimizedQueryError(query, root_plan)


class SeqScanDetectingCursor(psycopg.Cursor[Row]):
    """Raw cursor that rejects any statement planned with a sequential scan.

    After running each explainable statement it transparently re-plans it with
    ``EXPLAIN (FORMAT JSON)`` under ``SET LOCAL enable_seqscan = off``. A
    sequential scan in that plan means no index can serve the query, even when
    the session allows the planner to prefer sequential scans for optimization.
    """

    def execute(
        self,
        query: Query,
        params: Params | None = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Self:
        result = super().execute(
            query,  # type: ignore
            params,
            prepare=prepare,
            binary=binary,
        )
        if self._status_command() in _EXPLAINABLE_COMMAND_TAGS:
            self._explain_no_seq_scan(query, params)
        return result

    def executemany(
        self,
        query: Query,
        params_seq: Iterable[Params],
        *,
        returning: bool = False,
    ) -> None:
        params_list = list(params_seq)
        super().executemany(query, params_list, returning=returning)
        if self._status_command() in _EXPLAINABLE_COMMAND_TAGS:
            for params in params_list:
                self._explain_no_seq_scan(query, params)

    def _status_command(self) -> str | None:
        if self.connection.info.pipeline_status != pq.PipelineStatus.OFF:
            self.connection._pipeline.sync()  # type: ignore[union-attr]
        return self.statusmessage.split(maxsplit=1)[0] if self.statusmessage else None

    def _explain_no_seq_scan(self, query: Query, params: Params | None) -> None:
        explain_query = sql.SQL("EXPLAIN (FORMAT JSON) {}").format(
            sql.SQL(query) if isinstance(query, str) else query
        )
        with psycopg.RawCursor(self.connection) as plan_cursor:
            plan_cursor.execute(explain_query, params)
            row = next(plan_cursor)
        query_label = (
            query
            if isinstance(query, str)
            else explain_query.as_string(self.connection)
        )
        _reject_seq_scan(query_label, row["QUERY PLAN"])


class SeqScanDetectingRawCursor(
    RawCursorMixin[psycopg.Connection[Any], Row], SeqScanDetectingCursor[Row]
):
    __module__ = "psycopg"
