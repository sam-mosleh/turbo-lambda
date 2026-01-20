import inspect
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from functools import wraps
from types import TracebackType
from typing import Any, Protocol, overload

import pydantic
from opentelemetry.trace import format_span_id, format_trace_id, get_current_span

from turbo_lambda import schemas
from turbo_lambda.errors import (
    GeneralError,
    RequestValidationError,
    general_error_to_gateway_response,
)
from turbo_lambda.log import log_after_call, logger, logger_bind


class LambdaHandlerT[ResponseT](Protocol):
    def __call__(
        self, event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> ResponseT: ...


class ModelDumpProtocol[DumpOutput](Protocol):
    def model_dump(self) -> DumpOutput: ...


@overload
def validated_handler[RequestT: pydantic.BaseModel, DumpOutput](
    func: Callable[[RequestT], ModelDumpProtocol[DumpOutput]],
) -> LambdaHandlerT[DumpOutput]: ...


@overload
def validated_handler[RequestT: pydantic.BaseModel, ResponseT](
    func: Callable[[RequestT], ResponseT],
) -> LambdaHandlerT[ResponseT]: ...


def validated_handler[RequestT: pydantic.BaseModel, ResponseT](
    func: Callable[[RequestT], ResponseT],
) -> LambdaHandlerT[Any]:
    func_annotations = inspect.signature(func, eval_str=True)
    request_type: type[RequestT] = next(
        iter(func_annotations.parameters.values())
    ).annotation
    response_type_adapter: pydantic.TypeAdapter[ResponseT] = pydantic.TypeAdapter(
        func_annotations.return_annotation
    )

    def wrapper(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> Any:
        try:
            validated_event = request_type.model_validate(event)
        except pydantic.ValidationError as e:
            raise RequestValidationError(e) from e
        logger.debug("parsed_event", extra={"event": validated_event})
        return response_type_adapter.dump_python(
            func(validated_event), mode="json", by_alias=True
        )

    return wrapper


def context_manager_middleware[**P, T](
    cm: Callable[P, AbstractContextManager[Any]],
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with cm(*args, **kwargs):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def error_transformer_handler[**P, T, E: Exception](
    error_handler: Callable[[E], T],
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    error_handler_annotations = inspect.signature(error_handler, eval_str=True)
    error_type: type[E] = next(
        iter(error_handler_annotations.parameters.values())
    ).annotation

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except error_type as e:
                return error_handler(e)

        return wrapper

    return decorator


def request_logger_handler[ResponseT](
    func: LambdaHandlerT[ResponseT],
) -> LambdaHandlerT[ResponseT]:
    def bind_extractor(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> AbstractContextManager[None]:
        ctx = get_current_span().get_span_context()
        return logger_bind(
            lambda_context={
                "name": context.function_name,
                "memory_size": context.memory_limit_in_mb,
                "arn": context.invoked_function_arn,
                "request_id": context.aws_request_id,
            },
            trace_id=format(ctx.trace_id, "032x"),
            span_id=format(ctx.span_id, "016x"),
            trace_sampled=ctx.trace_flags.sampled,
        )

    @context_manager_middleware(bind_extractor)
    @log_after_call(
        log_level=logging.DEBUG,
        log_message="request",
        log_exceptions=True,
    )
    @wraps(func)
    def handler(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> ResponseT:
        return func(event, context)

    return handler


def gateway_handler[RequestT: pydantic.BaseModel](
    func: Callable[[RequestT], schemas.ApiGatewayResponse],
) -> LambdaHandlerT[schemas.ApiGatewaySerializedResponse]:
    def bind_extractor(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> AbstractContextManager[None]:
        ctx = get_current_span().get_span_context()
        return logger_bind(
            lambda_context={
                "name": context.function_name,
                "memory_size": context.memory_limit_in_mb,
                "arn": context.invoked_function_arn,
                "request_id": context.aws_request_id,
            },
            correlation_id=event["requestContext"].get("requestId"),
            trace_id=format_trace_id(ctx.trace_id),
            span_id=format_span_id(ctx.span_id),
            trace_sampled=ctx.trace_flags.sampled,
        )

    def result_extractor(
        response: schemas.ApiGatewaySerializedResponse,
    ) -> dict[str, Any]:
        return {"status_code": response["statusCode"]}

    @context_manager_middleware(bind_extractor)
    @log_after_call(
        log_level=logging.DEBUG,
        log_message="request",
        log_exceptions=True,
        result_extractor=result_extractor,
    )
    @error_transformer_handler(general_error_to_gateway_response)
    @validated_handler
    @wraps(func)
    def handler(event: RequestT) -> schemas.ApiGatewayResponse:
        return func(event)

    return handler


def parallel_sqs_handler[RequestT](
    *,
    max_workers: int,
) -> Callable[
    [Callable[[RequestT], None]],
    Callable[
        [schemas.SqsEvent[schemas.OnErrorNone[RequestT]]],
        schemas.LambdaCheckpointResponse,
    ],
]:
    def decorator(
        func: Callable[[RequestT], None],
    ) -> Callable[
        [schemas.SqsEvent[schemas.OnErrorNone[RequestT]]],
        schemas.LambdaCheckpointResponse,
    ]:
        func_annotations = inspect.signature(func, eval_str=True)
        request_type: type[RequestT] = next(
            iter(func_annotations.parameters.values())
        ).annotation

        def single_record_processor(
            rec: schemas.SqsRecordModel[schemas.OnErrorNone[RequestT]],
        ) -> schemas.LambdaCheckpointItem | None:
            if rec.body is None:
                logger.warning(
                    "sqs_message_ignored",
                    extra={"message_id": rec.message_id},
                )
                return None
            try:
                func(rec.body)
            except GeneralError:
                pass
            except Exception:
                return schemas.LambdaCheckpointItem(item_identifier=rec.message_id)
            return None

        def wrapper(
            event: schemas.SqsEvent[schemas.OnErrorNone[request_type]],  # type: ignore[valid-type]
        ) -> schemas.LambdaCheckpointResponse:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                responses = executor.map(single_record_processor, event.records)
            return schemas.LambdaCheckpointResponse(
                batch_item_failures=[item for item in responses if item is not None]
            )

        return wrapper

    return decorator


class CachedContextManager[T]:
    def __init__(self, context_manager: AbstractContextManager[T]) -> None:
        self._context_manager = context_manager

    def __enter__(self) -> T:
        self._value = self._context_manager.__enter__()
        return self._value

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._context_manager.__exit__(exc_type, exc_value, traceback)

    def __call__(self) -> T:
        return self._value
