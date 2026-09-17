import uuid

import pytest

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

# poetry run pytest tests/test_amocrm_client/test_lead_with_contact_and_tags.py -v -s --log-cli-level=INFO

TEST_TAG_ID = 929163  # Salebot-pro


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_lead_with_embedded_contact_and_tags() -> None:
    client = AmoCRMClient()

    try:
        tg_id = f"test_{uuid.uuid4().hex[:8]}"
        contact_id = await client.create_contact(name=f"pytest {tg_id}", tg_id=tg_id)

        lead_data = {
            "name": "ТЕСТ ТЕГОВ+КОНТАКТА — можно удалить",
            "pipeline_id": settings.AMOCRM_PIPELINE_ID,
            "status_id": settings.AMOCRM_STATUS_ID,
            "_embedded": {
                "contacts": [{"id": contact_id}],
                "tags": [{"id": TEST_TAG_ID}],
            },
        }

        create_response = await client._make_request("POST", "/leads", data=[lead_data])
        lead_id = create_response["_embedded"]["leads"][0]["id"]

        check_response = await client._make_request(
            "GET", f"/leads/{lead_id}", params={"with": "tags,contacts"}
        )
        embedded = check_response.get("_embedded", {})
        tag_ids_on_lead = [t["id"] for t in embedded.get("tags", [])]
        contact_ids_on_lead = [c["id"] for c in embedded.get("contacts", [])]

        print(f"\n✓ Lead {lead_id} tags={tag_ids_on_lead}, contacts={contact_ids_on_lead}")

        assert TEST_TAG_ID in tag_ids_on_lead
        assert contact_id in contact_ids_on_lead
    finally:
        await client.close()
