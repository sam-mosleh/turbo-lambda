import contextlib
import contextvars
import datetime
import inspect
import logging
import time
from collections.abc import Callable, Generator, Iterable
from functools import wraps
from typing import Any, overload

import pydantic

from turbo_lambda.schemas import IS_LAMBDA

LOGGING_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "LOGGING_CTX"
)


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


def _json_custom_default(value: Any) -> Any:
    match value:
        case pydantic.BaseModel():
            return value.model_dump(mode="json")
        case datetime.datetime() | datetime.date():
            return value.isoformat()
        case set():
            return list(value)
        case _:
            raise TypeError(value.__class__.__name__)


def _setup_logger() -> None:  # pragma: no cover
    if IS_LAMBDA:
        from awslambdaric import (  # type: ignore # noqa: PLC0415
            lambda_runtime_log_utils,
        )

        lambda_runtime_log_utils._json_encoder.default = _json_custom_default


@overload
def log_after_call[**P, T](func: Callable[P, T]) -> Callable[P, T]: ...


@overload
def log_after_call[**P, T](
    *,
    log_level: int = logging.INFO,
    log_message: str = "call",
    log_exceptions: bool = False,
    excluded_fields: Iterable[str] = ("self", "context"),
    result_extractor: None = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]: ...


@overload
def log_after_call[**P, T](
    *,
    log_level: int = logging.INFO,
    log_message: str = "call",
    log_exceptions: bool = False,
    excluded_fields: Iterable[str] = ("self", "context"),
    result_extractor: Callable[[T], dict[str, Any]],
) -> Callable[[Callable[P, T]], Callable[P, T]]: ...


def log_after_call[**P, T](  # noqa: PLR0913
    func: Callable[P, T] | None = None,
    log_level: int = logging.INFO,
    log_message: str = "call",
    log_exceptions: bool = False,
    excluded_fields: Iterable[str] = ("self", "context"),
    result_extractor: Callable[[T], dict[str, Any]] | None = None,
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
                "duration": None,
                "exc_str": None,
            }
            st = time.monotonic()
            try:
                result = func(*f_args, **f_kwargs)
                if result_extractor:
                    extra.update(result_extractor(result))
                return result
            except Exception as e:
                if log_exceptions:
                    extra["exc_str"] = str(e)
                raise
            finally:
                extra["duration"] = time.monotonic() - st
                logger.log(
                    logging.ERROR if extra["exc_str"] is not None else log_level,
                    log_message,
                    exc_info=extra["exc_str"] is not None,
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


logger = logging.getLogger(__name__)
logger.addFilter(_context_adder_filter)
_setup_logger()
