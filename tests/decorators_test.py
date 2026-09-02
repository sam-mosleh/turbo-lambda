import uuid
from contextlib import contextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated

import pytest
from pydantic import BaseModel, Field, Json

from turbo_lambda import errors, schemas
from turbo_lambda.decorators import (
    CachedContextManager,
    error_transformer_handler,
    gateway_handler,
    parallel_sqs_handler,
    request_logger_handler,
    suppress,
    validated_handler,
)

if TYPE_CHECKING:
    from collections.abc import Generator


class SampleContext:
    function_name = "function_name"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:"
    aws_request_id = str(uuid.uuid4())


class Message(BaseModel):
    message: str


class MessageWithAlias(BaseModel):
    message_str: Annotated[str, Field(serialization_alias="MessageString")]


class EmptyEvent(BaseModel):
    pass


def test_logger_exception() -> None:
    @request_logger_handler
    def handler(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> int:
        raise RuntimeError(message)

    message = "some message"
    with pytest.raises(RuntimeError, match=message):
        handler(schemas.EventType({}), SampleContext())


def test_error_to_gateway_response_handler() -> None:
    @gateway_handler
    def handler(event: EmptyEvent) -> schemas.ApiGatewayResponse:
        raise errors.GeneralError(
            status_code=HTTPStatus.NOT_FOUND, detail="Item not found"
        )

    assert handler(schemas.EventType({"requestContext": {}}), SampleContext()) == {
        "statusCode": HTTPStatus.NOT_FOUND,
        "headers": {"Content-Type": "application/problem+json"},
        "body": '{"type":"about:blank","status":404,"title":"Nothing matches the given URI","detail":"Item not found","extensions":null}',
        "isBase64Encoded": False,
    }


def test_none_lambda_handler() -> None:
    @validated_handler
    def handler(req: EmptyEvent) -> None:
        pass

    assert handler(schemas.EventType({}), SampleContext()) is None


def test_string_lambda_handler() -> None:
    @validated_handler
    def handler(event: EmptyEvent) -> bool:
        return True

    assert handler(schemas.EventType({}), SampleContext())


def test_model_lambda_handler() -> None:
    @validated_handler
    def handler(event: EmptyEvent) -> Message:
        return Message(message=message)

    message = "some message"
    assert handler(schemas.EventType({}), SampleContext()) == {"message": message}


def test_model_http_json() -> None:
    @validated_handler
    def handler(event: EmptyEvent) -> schemas.ApiGatewayResponse:
        return schemas.ApiGatewayResponse(body=MessageWithAlias(message_str="hi"))

    assert handler(schemas.EventType({}), SampleContext()) == {
        "statusCode": 200,
        "body": '{"MessageString":"hi"}',
        "headers": {
            "Content-Type": "application/json",
        },
        "isBase64Encoded": False,
    }


def test_model_http_binary() -> None:
    @validated_handler
    def handler(event: EmptyEvent) -> schemas.ApiGatewayResponse:
        return schemas.ApiGatewayResponse(
            status_code=HTTPStatus.CREATED,
            body=b"This is the way",
        )

    assert handler(schemas.EventType({}), SampleContext()) == {
        "statusCode": 201,
        "body": "VGhpcyBpcyB0aGUgd2F5",
        "headers": {
            "Content-Type": "application/octet-stream",
        },
        "isBase64Encoded": True,
    }


def test_model_http_no_content() -> None:
    @validated_handler
    def handler(event: EmptyEvent) -> schemas.ApiGatewayResponse:
        return schemas.ApiGatewayResponse(
            status_code=HTTPStatus.NO_CONTENT,
            body=None,
        )

    assert handler(schemas.EventType({}), SampleContext()) == {
        "statusCode": 204,
        "body": None,
        "headers": {},
        "isBase64Encoded": False,
    }


def test_invalid_event() -> None:
    @validated_handler
    def handler(event: Message) -> None: ...

    with pytest.raises(errors.GeneralError) as exc:
        handler(schemas.EventType({}), SampleContext())
    assert exc.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def _raise_unauthorized_from_general_error(error: errors.GeneralError) -> int:
    raise errors.UnauthorizedError() from error


def test_error_transformer_raise() -> None:
    @error_transformer_handler(_raise_unauthorized_from_general_error)
    def handler(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> int:
        raise errors.GeneralError(status_code=HTTPStatus.BAD_REQUEST, detail="")

    with pytest.raises(errors.UnauthorizedError):
        handler(
            schemas.EventType({}),
            SampleContext(),
        )


def test_error_transformer_return() -> None:
    @error_transformer_handler(_raise_unauthorized_from_general_error)
    def handler(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> int:
        return 1

    assert (
        handler(
            schemas.EventType({}),
            SampleContext(),
        )
        == 1
    )


def _generate_sqs_record(
    message_id: str, body: str, group_id: str | None = None
) -> dict[str, object]:
    return {
        "messageId": message_id,
        "receiptHandle": "",
        "body": body,
        "attributes": {
            "ApproximateReceiveCount": "1",
            "SentTimestamp": "1758197089376",
            "SenderId": "AROA4BY23KGPOJ2IHSVCD:a89b997ffa993552a059e02d14416754",
            "ApproximateFirstReceiveTimestamp": "1758197089380",
            "MessageGroupId": group_id,
        },
        "messageAttributes": {},
        "md5OfBody": "",
        "eventSource": "aws:sqs",
        "eventSourceARN": "",
        "awsRegion": "us-east-1",
    }


def test_parallel_sqs_handler_success() -> None:
    message = "some message to test"
    sqs_event = schemas.EventType(
        {
            "Records": [
                _generate_sqs_record(
                    message_id="valid_schema_good_body",
                    body=Message(message=message).model_dump_json(),
                )
            ]
        }
    )

    @validated_handler
    @parallel_sqs_handler(max_workers=1)
    def handler(message_event: Annotated[Message, Json]) -> None:
        assert message_event.message == message

    assert handler(sqs_event, SampleContext()) == {"batchItemFailures": []}


def test_parallel_sqs_handler_failure() -> None:
    message = "message1"
    sqs_event = schemas.EventType(
        {
            "Records": [
                _generate_sqs_record(
                    message_id="valid_schema_recoverable_error_body",
                    body=Message(message=message).model_dump_json(),
                ),
                _generate_sqs_record(
                    message_id="valid_schema_bad_body",
                    body=Message(message="message2").model_dump_json(),
                ),
                _generate_sqs_record(
                    message_id="invalid_schema_body", body="bad schema"
                ),
            ]
        }
    )

    @validated_handler
    @parallel_sqs_handler(max_workers=1)
    @suppress(errors.GeneralError)
    def handler(message_event: Annotated[Message, Json]) -> None:
        if message_event.message == message:
            raise errors.GeneralError()
        raise RuntimeError()

    assert handler(sqs_event, SampleContext()) == {
        "batchItemFailures": [
            {"itemIdentifier": "valid_schema_bad_body"},
        ]
    }


def test_parallel_sqs_handler_skips_same_group_after_error() -> None:
    processed: list[str] = []
    sqs_event = schemas.EventType(
        {
            "Records": [
                _generate_sqs_record(
                    message_id="group_a_error",
                    body=Message(message="fail").model_dump_json(),
                    group_id="group-a",
                ),
                _generate_sqs_record(
                    message_id="group_b_ok",
                    body=Message(message="ok").model_dump_json(),
                    group_id="group-b",
                ),
                _generate_sqs_record(
                    message_id="group_a_unprocessed",
                    body=Message(message="skip-me").model_dump_json(),
                    group_id="group-a",
                ),
                _generate_sqs_record(
                    message_id="group_a_invalid_unprocessed",
                    body="",
                    group_id="group-a",
                ),
            ]
        }
    )

    @validated_handler
    @parallel_sqs_handler(max_workers=1)
    def handler(message_event: Annotated[Message, Json]) -> None:
        processed.append(message_event.message)
        if message_event.message == "fail":
            raise RuntimeError()

    assert handler(sqs_event, SampleContext()) == {
        "batchItemFailures": [
            {"itemIdentifier": "group_a_error"},
            {"itemIdentifier": "group_a_unprocessed"},
            {"itemIdentifier": "group_a_invalid_unprocessed"},
        ]
    }
    assert processed == ["fail", "ok"]


def test_cached_context_manager() -> None:
    @contextmanager
    def f() -> Generator[object]:
        yield o

    o = object()
    cm = CachedContextManager(f())
    with cm as val:
        assert cm() == val == o
