import base64
import datetime
import json
import os
import re
from enum import Enum
from http import HTTPStatus
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NewType,
    Protocol,
    TypedDict,
)
from urllib.parse import urlencode

import annotated_types
import pydantic
from pydantic_core import CoreSchema, core_schema

IS_LAMBDA = os.environ.get("AWS_EXECUTION_ENV", "").startswith("AWS_Lambda_")
EventType = NewType("EventType", dict[str, Any])
_ROUTE_ARN_PATTERN_STR = r"^arn:aws:execute-api:(?P<region>[a-zA-Z0-9-]+):(?P<account_id>\d+):(?P<api_id>[a-zA-Z0-9]+)/(?P<stage>[^/]+)/(?P<method>[A-Z]+)/(?P<resource_path>.*)$"
_ROUTE_ARN_PATTERN = re.compile(_ROUTE_ARN_PATTERN_STR)


class _OnErrorNone:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: pydantic.GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.with_default_schema(
            default=None, schema=handler(source_type), on_error="default"
        )


type OnErrorNone[T] = Annotated[T | None, _OnErrorNone]


class LambdaContextProtocol(Protocol):
    function_name: str
    memory_limit_in_mb: int
    invoked_function_arn: str
    aws_request_id: str


class GatewayEventPathParameters[ParamT](pydantic.BaseModel):
    path_parameters: Annotated[ParamT, pydantic.Field(alias="pathParameters")]


class GatewayEventQueryParameters[ParamT](pydantic.BaseModel):
    query_string_parameters: Annotated[
        ParamT | None, pydantic.Field(alias="queryStringParameters")
    ] = None


class GatewayEventBodyJson[ParamT](pydantic.BaseModel):
    body: Annotated[ParamT, pydantic.Json]


class HttpErrorResponse(pydantic.BaseModel):
    """RFC7807 Compatible schema."""

    type: str
    status: int
    title: str
    detail: str
    extensions: Any


class ApiGatewaySerializedResponse(TypedDict):
    statusCode: int
    headers: dict[str, str]
    body: str | None
    isBase64Encoded: bool


class ApiGatewayResponse(pydantic.BaseModel):
    status_code: HTTPStatus | None = None
    headers: dict[str, str | None] | None = None
    body: Any

    @pydantic.model_serializer(mode="wrap")
    def serializer(
        self, handler: pydantic.SerializerFunctionWrapHandler
    ) -> ApiGatewaySerializedResponse:  # pragma: no cover
        serialized = handler(self)
        headers: dict[str, str] = {
            k: v for k, v in (serialized["headers"] or {}).items() if v is not None
        }
        match self.body:
            case bytes():
                return {
                    "statusCode": serialized["status_code"] or HTTPStatus.OK,
                    "headers": {"Content-Type": "application/octet-stream"} | headers,
                    "body": base64.b64encode(self.body).decode(),
                    "isBase64Encoded": True,
                }
            case None:
                return {
                    "statusCode": serialized["status_code"] or HTTPStatus.NO_CONTENT,
                    "headers": headers,
                    "body": None,
                    "isBase64Encoded": False,
                }
            case _:
                return {
                    "statusCode": serialized["status_code"] or HTTPStatus.OK,
                    "headers": {"Content-Type": "application/json"} | headers,
                    "body": json.dumps(serialized["body"], separators=(",", ":")),
                    "isBase64Encoded": False,
                }

    if TYPE_CHECKING:

        def model_dump(self) -> ApiGatewaySerializedResponse: ...  # type: ignore[override]


class PagedResponse[ItemT: pydantic.BaseModel, ParamsT: pydantic.BaseModel](
    pydantic.BaseModel
):
    items: list[ItemT]
    next_key: ParamsT | None

    def to_link_header(self, url: str) -> str | None:
        rels = {
            "next": urlencode(self.next_key.model_dump()) if self.next_key else None,
        }
        return (
            ", ".join(
                f'<{url}?{rel_params}>; rel="{rel_name}"'
                for rel_name, rel_params in rels.items()
                if rel_params is not None
            )
            or None
        )


class SqsAttributesModel(pydantic.BaseModel):
    approximate_receive_count: Annotated[
        str,
        pydantic.Field(
            alias="ApproximateReceiveCount",
            description="The number of times a message has been received across all queues but not deleted.",
            examples=["1", "2"],
        ),
    ]
    message_deduplication_id: Annotated[
        str | None,
        pydantic.Field(
            alias="MessageDeduplicationId",
            description="Returns the value provided by the producer that calls the SendMessage action.",
            examples=["msg-dedup-12345", "unique-msg-abc123", None],
        ),
    ] = None
    message_group_id: Annotated[
        str | None,
        pydantic.Field(
            alias="MessageGroupId",
            description="Returns the value provided by the producer that calls the SendMessage action.",
            examples=["order-processing", "user-123-updates", None],
        ),
    ] = None
    aws_trace_header: Annotated[
        str | None,
        pydantic.Field(
            alias="AWSTraceHeader",
            description="The AWS X-Ray trace header for request tracing.",
            examples=["Root=1-5e1b4151-5ac6c58239c1e5b4", None],
        ),
    ] = None
    sent_timestamp: Annotated[
        datetime.datetime,
        pydantic.Field(
            alias="SentTimestamp",
            description="The time the message was sent to the queue (epoch time in milliseconds).",
            examples=["1545082649183", "1545082650636", "1713185156609"],
        ),
    ]
    sequence_number: Annotated[
        str | None,
        pydantic.Field(
            alias="SequenceNumber",
            description="Returns the value provided by Amazon SQS.",
            examples=["18849496460467696128", "18849496460467696129", None],
        ),
    ] = None
    dead_letter_queue_source_arn: Annotated[
        str | None,
        pydantic.Field(
            alias="DeadLetterQueueSourceArn",
            description="The ARN of the dead-letter queue from which the message was moved.",
            examples=[
                "arn:aws:sqs:eu-central-1:123456789012:sqs-redrive-SampleQueue-RNvLCpwGmLi7",
                None,
            ],
        ),
    ] = None
    sender_id: Annotated[
        str,
        pydantic.Field(
            alias="SenderId",
            description="The user ID for IAM users or the role ID for IAM roles that sent the message.",
            examples=["AIDAIENQZJOLO23YVJ4VO", "AMCXIENQZJOLO23YVJ4VO"],
        ),
    ]
    approximate_first_receive_timestamp: Annotated[
        datetime.datetime,
        pydantic.Field(
            alias="ApproximateFirstReceiveTimestamp",
            description="The time the message was first received from the queue (epoch time in milliseconds).",
            examples=["1545082649185", "1545082650649", "1713185156612"],
        ),
    ]


class SqsMsgAttributeModel(pydantic.BaseModel):
    string_value: Annotated[
        str | None,
        pydantic.Field(
            alias="stringValue",
            description="The string value of the message attribute.",
            examples=["100", "active", "user-12345", None],
        ),
    ] = None
    binary_value: Annotated[
        str | None,
        pydantic.Field(
            alias="binaryValue",
            description="The binary value of the message attribute, base64-encoded.",
            examples=["base64Str", "SGVsbG8gV29ybGQ=", None],
        ),
    ] = None
    string_list_values: Annotated[
        list[str],
        pydantic.Field(
            alias="stringListValues",
            description="A list of string values for the message attribute.",
            examples=[["item1", "item2"], ["tag1", "tag2", "tag3"], []],
        ),
    ] = []
    binary_list_values: Annotated[
        list[str],
        pydantic.Field(
            alias="binaryListValues",
            description="A list of binary values for the message attribute, each base64-encoded.",
            examples=[["dmFsdWUx", "dmFsdWUy"], ["aGVsbG8="], []],
        ),
    ] = []
    data_type: Annotated[
        str,
        pydantic.Field(
            alias="dataType",
            description="The data type of the message attribute (String, Number, Binary, or custom data type).",
            examples=["String", "Number", "Binary", "String.custom", "Number.float"],
        ),
    ]


class SqsRecordModel[BodyT](pydantic.BaseModel):
    message_id: Annotated[
        str,
        pydantic.Field(
            alias="messageId",
            description="A unique identifier for the message. A MessageId is considered unique across all AWS accounts.",
            examples=[
                "059f36b4-87a3-44ab-83d2-661975830a7d",
                "2e1424d4-f796-459a-8184-9c92662be6da",
                "db37cc61-1bb0-4e77-b6f3-7cf87f44a72a",
            ],
        ),
    ]
    receipt_handle: Annotated[
        str,
        pydantic.Field(
            alias="receiptHandle",
            description="An identifier associated with the act of receiving the message, used for message deletion.",
            examples=[
                "AQEBwJnKyrHigUMZj6rYigCgxlaS3SLy0a...",
                "AQEBzWwaftRI0KuVm4tP+/7q1rGgNqicHq...",
            ],
        ),
    ]
    body: Annotated[
        BodyT,
        pydantic.Field(
            description="The message's contents (not URL-encoded). Can be plain text or JSON.",
            examples=[
                "Test message.",
                '{"message": "foo1"}',
                "hello world",
            ],
        ),
    ]
    attributes: Annotated[
        SqsAttributesModel,
        pydantic.Field(
            description="A map of the attributes requested in ReceiveMessage to their respective values.",
        ),
    ]
    message_attributes: Annotated[
        dict[str, SqsMsgAttributeModel],
        pydantic.Field(
            alias="messageAttributes",
            description="User-defined message attributes as key-value pairs.",
        ),
    ]
    md5_of_body: Annotated[
        str,
        pydantic.Field(
            alias="md5OfBody",
            description="An MD5 digest of the non-URL-encoded message body string.",
            examples=[
                "e4e68fb7bd0e697a0ae8f1bb342846b3",
                "6a204bd89f3c8348afd5c77c717a097a",
            ],
        ),
    ]
    md5_of_message_attributes: Annotated[
        str | None,
        pydantic.Field(
            alias="md5OfMessageAttributes",
            description="An MD5 digest of the non-URL-encoded message attribute string.",
            examples=[
                "00484c68...59e48fb7",
                "b25f48e8...f4e4f0bb",
                None,
            ],
        ),
    ] = None
    event_source: Annotated[
        Literal["aws:sqs"],
        pydantic.Field(
            alias="eventSource",
            description="The AWS service that invoked the function.",
            examples=["aws:sqs"],
        ),
    ]
    event_source_arn: Annotated[
        str,
        pydantic.Field(
            alias="eventSourceARN",
            description="The Amazon Resource Name (ARN) of the SQS queue.",
            examples=[
                "arn:aws:sqs:us-east-2:123456789012:my-queue",
                "arn:aws:sqs:eu-central-1:123456789012:sqs-redrive-SampleDLQ-Emgp9MFSLBZm",
            ],
        ),
    ]
    aws_region: Annotated[
        str,
        pydantic.Field(
            alias="awsRegion",
            description="The AWS region where the SQS queue is located.",
            examples=["us-east-1", "us-east-2", "eu-central-1"],
        ),
    ]


class SqsEvent[BodyT](pydantic.BaseModel):
    records: Annotated[
        list[SqsRecordModel[BodyT]],
        pydantic.Field(
            alias="Records",
            description="A list of SQS message records included in the event.",
            examples=[
                [
                    {
                        "messageId": "059f36b4-87a3-44ab-83d2-661975830a7d",
                        "body": "Test message.",
                    }
                ]
            ],
        ),
    ]


class LambdaCheckpointItem(pydantic.BaseModel):
    item_identifier: Annotated[
        str, pydantic.Field(serialization_alias="itemIdentifier")
    ]


class LambdaCheckpointResponse(pydantic.BaseModel):
    batch_item_failures: Annotated[
        list[LambdaCheckpointItem],
        pydantic.Field(serialization_alias="batchItemFailures"),
    ]


class EventBridgeModel[DetailT](pydantic.BaseModel):
    version: Annotated[
        str,
        pydantic.Field(
            description="By default, this is set to 0 (zero) in all events.",
            examples=["0"],
        ),
    ]
    id: Annotated[
        str,
        pydantic.Field(
            description="A Version 4 UUID generated for every event.",
            examples=["6a7e8feb-b491-4cf7-a9f1-bf3703467718"],
        ),
    ]
    source: Annotated[
        str,
        pydantic.Field(
            description="Identifies the service that sourced the event. \
        All events sourced from within AWS begin with 'aws.'",
            examples=["aws.ec2", "aws.s3", "aws.events", "aws.scheduler"],
        ),
    ]
    account: Annotated[
        str,
        pydantic.Field(
            description="The 12-digit AWS account ID of the owner of the service emitting the event.",
            examples=["111122223333", "123456789012"],
        ),
    ]
    time: Annotated[
        datetime.datetime,
        pydantic.Field(
            description="The event timestamp, which can be specified by the service originating the event.",
            examples=["2017-12-22T18:43:48Z", "2023-01-15T10:30:00Z"],
        ),
    ]
    region: Annotated[
        str,
        pydantic.Field(
            description="Identifies the AWS region where the event originated.",
            examples=["us-east-1", "us-west-2", "eu-west-1"],
        ),
    ]
    resources: Annotated[
        list[str],
        pydantic.Field(
            description="A JSON array that contains ARNs that identify resources involved in the event. "
            "Inclusion of these ARNs is at the discretion of the service.",
            examples=[
                ["arn:aws:ec2:us-west-1:123456789012:instance/i-1234567890abcdef0"],
                ["arn:aws:s3:::my-bucket/my-key"],
                ["arn:aws:events:us-east-1:123456789012:rule/MyRule"],
            ],
        ),
    ]
    detail_type: Annotated[
        str,
        pydantic.Field(
            alias="detail-type",
            description="Identifies, in combination with the source Field, the fields and values that appear in the detail field.",
            examples=[
                "EC2 Instance State-change Notification",
                "Object Created",
                "Scheduled Event",
            ],
        ),
    ]
    detail: Annotated[
        DetailT,
        pydantic.Field(
            description="A JSON object, whose content is at the discretion of the service originating the event.",
        ),
    ]
    replay_name: Annotated[
        str | None,
        pydantic.Field(
            alias="replay-name",
            description="Identifies whether the event is being replayed and what is the name of the replay.",
            examples=["replay_archive", "my-replay-2023"],
        ),
    ] = None


class RouteARN(pydantic.BaseModel):
    region: str
    account_id: int
    api_id: str
    stage: str
    method: str
    resource_path: str


def _route_arn_validate(v: str | Any) -> dict[str, str] | Any:
    if not isinstance(v, str):
        return v
    matched = _ROUTE_ARN_PATTERN.match(v)
    if not matched:
        raise ValueError("Invalid Route ARN")
    return matched.groupdict()


def _route_arn_serialize(value: RouteARN) -> str:
    return f"arn:aws:execute-api:{value.region}:{value.account_id}:{value.api_id}/{value.stage}/{value.method}/{value.resource_path}"


type RouteARNStr = Annotated[
    RouteARN,
    pydantic.BeforeValidator(
        _route_arn_validate,
        json_schema_input_type=core_schema.str_schema(pattern=_ROUTE_ARN_PATTERN_STR),
    ),
    pydantic.PlainSerializer(
        _route_arn_serialize,
        return_type=core_schema.str_schema(pattern=_ROUTE_ARN_PATTERN_STR),
    ),
]


class ActionEnum(Enum):
    API_INVOKE = "execute-api:Invoke"


class EffectEnum(Enum):
    ALLOW = "Allow"
    DENY = "Deny"


class AuthorizerPolicyStatement(pydantic.BaseModel):
    action: Annotated[ActionEnum, pydantic.Field(serialization_alias="Action")]
    effect: Annotated[EffectEnum, pydantic.Field(serialization_alias="Effect")]
    resource: Annotated[str, pydantic.Field(serialization_alias="Resource")]


class AuthorizerPolicyDocument(pydantic.BaseModel):
    version: Annotated[
        Literal["2012-10-17"], pydantic.Field(serialization_alias="Version")
    ]
    statement: Annotated[
        list[AuthorizerPolicyStatement],
        annotated_types.Len(1),
        pydantic.Field(serialization_alias="Statement"),
    ]


class AuthorizerResponse(pydantic.BaseModel):
    principal_id: Annotated[str, pydantic.Field(serialization_alias="principalId")]
    policy_document: Annotated[
        AuthorizerPolicyDocument, pydantic.Field(serialization_alias="policyDocument")
    ]
    context: dict[str, str]
