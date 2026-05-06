"""Тесты для Salebot API клиента."""
import pytest

from app.services.salebot_client import SalebotClient
#  poetry run pytest tests/test_salebot_client/test_salebot_client.py -v -s --log-cli-level=INFO

TEST_CLIENT_ID = 836058546
TEST_PLATFORM_ID = 6253651200
TEST_BOT_NAME = "ElAuthBot"


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_send_message() -> None:
#     """Тест отправки сообщения клиенту через Salebot API."""
#     client = SalebotClient()
#
#     response = await client.send_message(
#         client_id=TEST_CLIENT_ID,
#         message="Тестовое сообщение от интеграции3",
#     )
#
#     assert response is not None
#     print(f"\n✓ Message sent to Salebot: {response}")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_load_client() -> None:
    """Тест загрузки клиента из Salebot по platform_id."""
    client = SalebotClient()

    response = await client.load_client(
        platform_id=TEST_PLATFORM_ID,
        group_id=TEST_BOT_NAME,
    )

    print(f"\n=== Load Client Response ===")
    print(f"Status: {response.get('status')}")
    print(f"Items: {response.get('items')}")
    print(f"response: {response.get('items')}")
    items = response.get("items", [])

    print(f"response: {items[0].get("id")}")

    assert response is not None
    assert response.get("status") == "success"
    assert "items" in response
    assert len(response["items"]) > 0

    item = response["items"][0]
    assert item.get("platform_id") == TEST_PLATFORM_ID
    assert item.get("group_id") == TEST_BOT_NAME
    assert item.get("id") == TEST_CLIENT_ID

    print(f"✓ Client loaded: platform_id={item['platform_id']}, salebot_client_id={item['id']}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_get_history() -> None:
#     """Тест получения истории сообщений клиента из Salebot."""
#     client = SalebotClient()
#
#     response = await client.get_history(client_id=TEST_CLIENT_ID)
#
#     print(f"\n=== Get History Response ===")
#     print(f"Status: {response.get('status')}")
#     print(f"Keys: {list(response.keys())}")
#
#     assert response is not None
#
#     if "result" in response:
#         messages = response["result"]
#         print(f"Total messages: {len(messages)}")
#
#         if messages:
#             print(f"\nFirst message:")
#             first = messages[0]
#             print(f"  - text: {first.get('text', '')[:50]}...")
#             print(f"  - client_replica: {first.get('client_replica')}")
#             print(f"  - created_at: {first.get('created_at')}")
#
#             print(f"\nLast message:")
#             last = messages[-1]
#             print(f"  - text: {last.get('text', '')[:50]}...")
#             print(f"  - client_replica: {last.get('client_replica')}")
#             print(f"  - created_at: {last.get('created_at')}")
#
#         print(f"✓ History retrieved: {len(messages)} messages")
#     else:
#         print("No 'result' key in response")
#         print(f"Response: {response}")
