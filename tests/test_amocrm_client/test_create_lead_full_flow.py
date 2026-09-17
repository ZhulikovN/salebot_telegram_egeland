import uuid

import pytest

from app.services.amocrm_client import AmoCRMClient
from app.settings import settings

# poetry run pytest tests/test_amocrm_client/test_create_lead_full_flow.py -v -s --log-cli-level=INFO

SALEBOT_PRO_TAG_ID = 929163


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_contact_new_lead_with_tags_in_one_request() -> None:
    client = AmoCRMClient()

    try:
        tg_id = f"test_{uuid.uuid4().hex[:8]}"

        contact_id = await client.create_contact(name=f"pytest {tg_id}", tg_id=tg_id)

        # Контакт только что создан — check_duplicate_lead пропускается по новой логике,
        # сразу create_lead с тегами
        lead_id = await client.create_lead(
            contact_id=contact_id,
            bot_name="pytest_bot",
            tag_ids=[SALEBOT_PRO_TAG_ID],
        )

        response = await client._make_request(
            "GET", f"/leads/{lead_id}", params={"with": "tags,contacts"}
        )
        embedded = response.get("_embedded", {})
        tag_ids_on_lead = [t["id"] for t in embedded.get("tags", [])]
        contact_ids_on_lead = [c["id"] for c in embedded.get("contacts", [])]

        print(f"\n✓ New lead {lead_id}: tags={tag_ids_on_lead}, contacts={contact_ids_on_lead}")

        assert SALEBOT_PRO_TAG_ID in tag_ids_on_lead
        assert contact_id in contact_ids_on_lead

        # Дубля для этого же контакта теперь быть не должно (сделка одна)
        duplicate = await client.check_duplicate_lead(
            contact_id=contact_id, pipeline_id=settings.AMOCRM_PIPELINE_ID
        )
        assert duplicate is not None
        assert duplicate["id"] == lead_id
    finally:
        await client.close()
