import datetime
import inspect
import json
import logging
from collections.abc import Generator
from io import StringIO
from typing import Any

import pydantic
import pytest

from turbo_lambda.log import json_formatter, log_after_call, logger, logger_bind


class SampleClass:
    @log_after_call
    def my_function_name(self, a: str) -> None:
        pass

    this_frame = inspect.currentframe()


@pytest.fixture
def logger_buffer() -> Generator[StringIO, None, None]:
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(json_formatter)
    logger.addHandler(handler)
    try:
        yield buffer
    finally:
        logger.removeHandler(handler)


def test_logger_invalid_type(logger_buffer: StringIO) -> None:
    class SomeClass:
        pass

    logger.info("check", extra={"key": SomeClass()})
    assert logger_buffer.getvalue() == ""


def test_logger_bind(logger_buffer: StringIO) -> None:
    class SampleClass(pydantic.BaseModel):
        s: str

    msg, value1, value2, value3 = (
        "some message",
        "some value 1",
        datetime.datetime.now(),
        {1, 2},
    )
    partially_expected = {
        "message": msg,
        "key1": {"s": value1},
        "key2": value2.isoformat(),
        "key3": list(value3),
    }
    with logger_bind(key1=SampleClass(s=value1)):
        logger.info(msg, extra={"key2": value2, "key3": value3})
    record = json.loads(logger_buffer.getvalue())
    assert {k: record[k] for k in partially_expected} == partially_expected


def test_logging_exceptions(logger_buffer: StringIO) -> None:
    try:
        _ = 1 / 0
    except ZeroDivisionError:
        logger.exception("error occured", stack_info=True)

    record = json.loads(logger_buffer.getvalue())
    assert "ZeroDivisionError" in record["exc_info"]


def test_log_after_call_without_args(logger_buffer: StringIO) -> None:
    assert SampleClass.this_frame
    random_a = "some random value"
    SampleClass().my_function_name(random_a)
    partially_expected = {
        "message": "call",
        "function": {
            "name": "SampleClass.my_function_name",
            "module": __name__,
            "pathname": __file__,
            "firstlineno": SampleClass.this_frame.f_lineno - 4,
        },
        "arguments": {"a": random_a},
    }
    record = json.loads(logger_buffer.getvalue())
    assert {k: record[k] for k in partially_expected} == partially_expected
    assert record["duration"] > 0


def test_log_after_call_with_parameters(logger_buffer: StringIO) -> None:
    def result_extractor(x: int) -> dict[str, Any]:
        return {"some_key": x}

    @log_after_call(excluded_fields={"a"}, result_extractor=result_extractor)
    def my_function_name(a: str) -> int:
        return 1

    my_function_name("some argument value")
    record = json.loads(logger_buffer.getvalue())
    assert not record["arguments"]
    assert record["some_key"] == 1
