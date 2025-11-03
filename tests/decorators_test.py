import uuid
from collections.abc import Generator
from contextlib import contextmanager
from http import HTTPStatus
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, Json

from turbo_lambda import errors, schemas
from turbo_lambda.decorators import (
    CachedContextManager,
    error_transformer_handler,
    gateway_handler,
    parallel_sqs_handler,
    request_logger_handler,
    validated_handler,
)


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


def test_error_transformer_raise() -> None:
    def raise_unauthorized(error: errors.GeneralError) -> None:
        raise errors.UnauthorizedError() from error

    @error_transformer_handler(raise_unauthorized)
    def handler(
        event: schemas.EventType, context: schemas.LambdaContextProtocol
    ) -> None:
        raise errors.GeneralError(status_code=HTTPStatus.BAD_REQUEST, detail="")

    with pytest.raises(errors.UnauthorizedError):
        handler(
            schemas.EventType({}),
            SampleContext(),
        )


def test_error_transformer_return() -> None:
    def error_handler(error: Exception) -> int: ...  # type: ignore[empty-body]

    @error_transformer_handler(error_handler)
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


def test_parallel_sqs_handler() -> None:
    first_message = "some first_message"
    sqs_event = schemas.EventType(
        {
            "Records": [
                {
                    "messageId": "someid1",
                    "receiptHandle": "",
                    "body": Message(message=first_message).model_dump_json(),
                    "attributes": {
                        "ApproximateReceiveCount": "1",
                        "SentTimestamp": "1758197089376",
                        "SenderId": "AROA4BY23KGPOJ2IHSVCD:a89b997ffa993552a059e02d14416754",
                        "ApproximateFirstReceiveTimestamp": "1758197089380",
                    },
                    "messageAttributes": {},
                    "md5OfBody": "",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "",
                    "awsRegion": "us-east-1",
                },
                {
                    "messageId": "someid2",
                    "receiptHandle": "",
                    "body": Message(message="some message").model_dump_json(),
                    "attributes": {
                        "ApproximateReceiveCount": "1",
                        "SentTimestamp": "1758197089376",
                        "SenderId": "AROA4BY23KGPOJ2IHSVCD:a89b997ffa993552a059e02d14416754",
                        "ApproximateFirstReceiveTimestamp": "1758197089380",
                    },
                    "messageAttributes": {},
                    "md5OfBody": "",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "",
                    "awsRegion": "us-east-1",
                },
                {
                    "messageId": "someid3",
                    "receiptHandle": "",
                    "body": "bad schema",
                    "attributes": {
                        "ApproximateReceiveCount": "1",
                        "SentTimestamp": "1758197089376",
                        "SenderId": "AROA4BY23KGPOJ2IHSVCD:a89b997ffa993552a059e02d14416754",
                        "ApproximateFirstReceiveTimestamp": "1758197089380",
                    },
                    "messageAttributes": {},
                    "md5OfBody": "",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "",
                    "awsRegion": "us-east-1",
                },
            ]
        }
    )

    @validated_handler
    @parallel_sqs_handler(max_workers=1)
    def handler(message_event: Annotated[Message, Json]) -> None:
        assert message_event.message == first_message

    assert handler(sqs_event, SampleContext()) == {
        "batchItemFailures": [
            {"itemIdentifier": sqs_event["Records"][1]["messageId"]},
            {"itemIdentifier": sqs_event["Records"][2]["messageId"]},
        ]
    }


def test_cached_context_manager() -> None:
    @contextmanager
    def f() -> Generator[object]:
        yield o

    o = object()
    cm = CachedContextManager(f())
    with cm as val:
        assert cm() == val == o
