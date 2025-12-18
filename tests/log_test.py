import datetime
import inspect
import json
import logging
from collections.abc import Generator
from io import StringIO
from typing import Any

import pydantic
import pytest

from turbo_lambda.log import _json_custom_default, log_after_call, logger, logger_bind


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if record.exc_info and record.exc_text is None:
            record.exc_text = self.formatException(record.exc_info)
        if record.stack_info:
            record.stack_info = self.formatStack(record.stack_info)
        record_dict = {k: v for k, v in vars(record).items() if k not in {"exc_info"}}
        return json.dumps(record_dict, default=_json_custom_default)


class SampleClass:
    @log_after_call
    def my_function_name(self, a: str) -> None:
        pass

    this_frame = inspect.currentframe()


@pytest.fixture
def logger_buffer() -> Generator[StringIO, None, None]:
    buffer = StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
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
    assert "ZeroDivisionError" in record["exc_text"]


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
        "exc_str": None,
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


def test_log_after_call_with_message(logger_buffer: StringIO) -> None:
    @log_after_call(log_message="new_message")
    def my_function(a: str) -> int:
        return 1

    my_function("some argument value")
    record = json.loads(logger_buffer.getvalue())
    assert record["message"] == "new_message"


def test_log_after_call_with_exception(logger_buffer: StringIO) -> None:
    @log_after_call(log_exceptions=True)
    def my_function() -> None:
        raise RuntimeError("some exception string")

    with pytest.raises(RuntimeError):
        my_function()
    record = json.loads(logger_buffer.getvalue())
    assert record["levelname"] == "ERROR"
    assert record["exc_str"] == "some exception string"


def test_log_after_call_without_exception_logging(logger_buffer: StringIO) -> None:
    @log_after_call
    def my_function() -> None:
        raise RuntimeError("some exception string")

    with pytest.raises(RuntimeError):
        my_function()
    record = json.loads(logger_buffer.getvalue())
    assert record["levelname"] == "INFO"
    assert record["exc_str"] is None


def test_log_after_call_with_extractor(logger_buffer: StringIO) -> None:
    def extractor(ret: int) -> dict[str, Any]:
        return {"ret": ret}

    @log_after_call(result_extractor=extractor)
    def my_function() -> int:
        return 1

    my_function()
    record = json.loads(logger_buffer.getvalue())
    assert record["ret"] == 1
