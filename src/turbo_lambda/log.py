import contextlib
import contextvars
import inspect
import logging
import os
import time
from collections.abc import Callable, Generator, Iterable, Mapping
from functools import wraps
from typing import Any, overload

import orjson
import pydantic

LOGGING_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "LOGGING_CTX"
)
_DEFAULT_TRANSLATOR = {
    "levelname": "level",
    "name": "logger",
    "asctime": "timestamp",
    "exc_text": "exc_info",
    "funcName": "func_name",
    "threadName": "thread_name",
    "processName": "process_name",
    "taskName": "task_name",
}
_DEFAULT_IGNORE = {
    "msg",
    "args",
    "levelno",
    "exc_info",
    "created",
    "msecs",
    "relativeCreated",
}


@contextlib.contextmanager
def logger_bind(**kwargs: Any) -> Generator[None]:
    token = LOGGING_CTX.set(LOGGING_CTX.get({}) | kwargs)
    try:
        yield
    finally:
        LOGGING_CTX.reset(token)


def _context_adder_filter(record: logging.LogRecord) -> bool:
    for k, v in LOGGING_CTX.get({}).items():
        setattr(record, k, v)
    return True


def _orjson_custom_type_handler(value: Any) -> Any:
    match value:
        case pydantic.BaseModel():
            return value.model_dump(mode="json")
        case set():
            return list(value)
        case _:
            raise TypeError()


class JsonFormatter(logging.Formatter):
    def __init__(
        self,
        translator_dict: Mapping[str, str] | None = None,
        ignored_keys: set[str] | None = None,
    ):
        self.translator_dict = translator_dict or _DEFAULT_TRANSLATOR
        self.ignored_keys = ignored_keys or _DEFAULT_IGNORE
        self.default_time_format = "%Y-%m-%dT%H:%M:%S"
        self.default_msec_format = "%s.%03dZ"
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        record.asctime = self.formatTime(record)
        if record.exc_info and record.exc_text is None:
            record.exc_text = self.formatException(record.exc_info)
        if record.stack_info:
            record.stack_info = self.formatStack(record.stack_info)
        message_dict = {
            self.translator_dict.get(rec_key, rec_key): rec_val
            for rec_key, rec_val in vars(record).items()
            if rec_key not in self.ignored_keys
        }
        return orjson.dumps(message_dict, default=_orjson_custom_type_handler).decode()


def config_default_logger() -> None:  # pragma: no cover
    handler = logging.StreamHandler()
    handler.setFormatter(json_formatter)
    logger.addHandler(handler)


def config_lambda_logger() -> None:  # pragma: no cover
    if log_level := os.environ.get("AWS_LAMBDA_LOG_LEVEL"):
        logger.setLevel(log_level)
    logging.getLogger().handlers[0].setFormatter(json_formatter)


@overload
def log_after_call[**P, T](func: Callable[P, T]) -> Callable[P, T]: ...


@overload
def log_after_call[**P, T](
    *,
    log_level: int = logging.INFO,
    log_message: str = "call",
    excluded_fields: Iterable[str] = ("self", "context"),
    result_extractor: Callable[[Any], dict[str, Any]] | None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]: ...


def log_after_call[**P, T](
    func: Callable[P, T] | None = None,
    log_level: int = logging.INFO,
    log_message: str = "call",
    excluded_fields: Iterable[str] = ("self", "context"),
    result_extractor: Callable[[Any], dict[str, Any]] | None = None,
) -> Callable[P, T] | Callable[[Callable[P, T]], Callable[P, T]]:
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        sig = inspect.signature(func)

        @wraps(func)
        def wrapper(*f_args: P.args, **f_kwargs: P.kwargs) -> T:
            extra: dict[str, Any] = {
                "function": {
                    "name": func.__qualname__,
                    "module": func.__module__,
                    "pathname": func.__code__.co_filename,
                    "firstlineno": func.__code__.co_firstlineno,
                },
                "arguments": get_arguments(sig, excluded_fields, f_args, f_kwargs),
            }
            exc_info = False
            st = time.monotonic()
            try:
                result = func(*f_args, **f_kwargs)
                if result_extractor:
                    extra.update(result_extractor(result))
                return result
            except Exception:
                exc_info = True
                raise
            finally:
                extra["duration"] = time.monotonic() - st
                logger.log(
                    log_level if not exc_info else logging.ERROR,
                    log_message,
                    exc_info=exc_info,
                    extra=extra,
                )

        return wrapper

    return decorator if func is None else decorator(func)


def get_arguments(
    sig: inspect.Signature,
    excluded_fields: Iterable[str],
    args: Iterable[Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    bind = sig.bind(*args, **kwargs)
    bind.apply_defaults()
    for field in excluded_fields:
        bind.arguments.pop(field, None)
    return bind.arguments


json_formatter = JsonFormatter()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addFilter(_context_adder_filter)
