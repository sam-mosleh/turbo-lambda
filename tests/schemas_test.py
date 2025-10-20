import pydantic
import pytest

from turbo_lambda.schemas import RouteARN, RouteARNStr


def test_route_arn() -> None:
    class SampleData(pydantic.BaseModel):
        arn: RouteARNStr

    with pytest.raises(pydantic.ValidationError):
        SampleData(arn=1)

    with pytest.raises(pydantic.ValidationError):
        SampleData(arn="bad input")

    route_arn_str = (
        "arn:aws:execute-api:us-east-1:111111111111:apiid/$default/GET/myroute/abc"
    )
    data = SampleData(arn=route_arn_str)
    assert data.arn == RouteARN(
        region="us-east-1",
        account_id="111111111111",
        api_id="apiid",
        stage="$default",
        method="GET",
        resource_path="myroute/abc",
    )
    assert data.model_dump() == {"arn": route_arn_str}
