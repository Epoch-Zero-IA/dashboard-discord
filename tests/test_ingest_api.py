"""The acceptance endpoint of the socle: `GET /api/ingest/status`."""

import pytest
from litestar.status_codes import HTTP_200_OK, HTTP_401_UNAUTHORIZED
from litestar.testing import AsyncTestClient

from backend.app import app
from backend.security import API_KEY_HEADER

STATUS_PATH = "/api/ingest/status"


async def test_the_status_needs_the_api_key(client: AsyncTestClient) -> None:
    """The answer names every channel of the server, which is more than a visitor needs.

    No database is touched: the guard runs before the handler, so this test holds with
    or without Postgres.
    """
    response = await client.get(STATUS_PATH)
    assert response.status_code == HTTP_401_UNAUTHORIZED


def test_the_contract_describes_the_fields() -> None:
    """Guards the `dict[str, str]` trap documented in CLAUDE.md.

    A dict-annotated response comes out of openapi.json as `{ [key: string]: string }`,
    which describes nothing and leaves the generated client with no types. Asserting on
    the schema catches that at test time rather than at code-review time.
    """
    schema = app.openapi_schema.to_schema()
    operation = schema["paths"][STATUS_PATH]["get"]

    assert operation["security"] == [{"APIKey": []}]

    body = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("IngestStatus")

    status = schema["components"]["schemas"]["IngestStatus"]
    assert set(status["required"]) == {"channels", "total_messages", "heartbeat"}
    assert status["properties"]["channels"]["type"] == "array"


def test_a_channel_entry_is_fully_described() -> None:
    """Every field the reader needs, typed rather than loosely nested."""
    schema = app.openapi_schema.to_schema()
    channel = schema["components"]["schemas"]["ChannelStatus"]

    assert set(channel["properties"]) == {
        "channel_id",
        "channel_name",
        "message_count",
        "oldest_message_id",
        "newest_message_id",
        "is_complete",
    }


@pytest.mark.db
async def test_the_status_answers_on_an_empty_database(
    client: AsyncTestClient, migrated_database: str, api_key: str
) -> None:
    """The wiring end to end: dependency, session, query, serialisation.

    An empty database is the interesting case here — it is what a fresh deployment
    looks like, and the endpoint has to answer rather than fail on a missing heartbeat.
    """
    response = await client.get(STATUS_PATH, headers={API_KEY_HEADER: api_key})

    assert response.status_code == HTTP_200_OK
    body = response.json()
    assert isinstance(body["channels"], list)
    assert body["total_messages"] >= 0
    assert "heartbeat" in body
