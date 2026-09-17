# poetry run pytest tests/test_amocrm_client/test_lead_with_embedded_tags.py -v -s --log-cli-level=INFO
import pytest

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

TEST_TAG_ID = 929163  # Salebot-pro


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_lead_with_embedded_tag() -> None:
    client = AmoCRMClient()

    try:
        lead_data = {
            "name": "ТЕСТ ТЕГОВ — можно удалить",
            "pipeline_id": settings.AMOCRM_PIPELINE_ID,
            "status_id": settings.AMOCRM_STATUS_ID,
            "_embedded": {
                "tags": [{"id": TEST_TAG_ID}],
            },
        }

        create_response = await client._make_request("POST", "/leads", data=[lead_data])
        lead_id = create_response["_embedded"]["leads"][0]["id"]

        check_response = await client._make_request(
            "GET", f"/leads/{lead_id}", params={"with": "tags"}
        )
        tags = check_response.get("_embedded", {}).get("tags", [])
        tag_ids_on_lead = [t["id"] for t in tags]

        print(f"\n✓ Lead {lead_id} created with tags: {tag_ids_on_lead}")

        assert TEST_TAG_ID in tag_ids_on_lead
    finally:
        await client.close()
