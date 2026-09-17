# poetry run pytest tests/test_amocrm_client/test_contact_with_leads.py -v -s --log-cli-level=INFO
import uuid

import pytest

from app.services.amocrm_client import AmoCRMClient


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_contact_with_embedded_leads() -> None:
    client = AmoCRMClient()

    try:
        tg_id = f"test_{uuid.uuid4().hex[:8]}"
        contact_id = await client.create_contact(name=f"pytest {tg_id}", tg_id=tg_id)
        lead_id = await client.create_lead(contact_id=contact_id, bot_name="pytest_bot")

        response = await client._make_request(
            "GET",
            f"/contacts/{contact_id}",
            params={"with": "leads"},
        )

        leads = response.get("_embedded", {}).get("leads", [])
        lead_ids = [lead["id"] for lead in leads]

        print(f"\n✓ Contact {contact_id} response contains embedded lead(s): {lead_ids}")

        assert "_embedded" in response
        assert lead_id in lead_ids
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_search_contact_by_query_with_embedded_leads() -> None:
    client = AmoCRMClient()

    try:
        tg_id = f"test_{uuid.uuid4().hex[:8]}"
        contact_id = await client.create_contact(name=f"pytest {tg_id}", tg_id=tg_id)
        lead_id = await client.create_lead(contact_id=contact_id, bot_name="pytest_bot")

        response = await client._make_request(
            "GET",
            "/contacts",
            params={"query": tg_id, "with": "leads"},
        )

        contacts = response.get("_embedded", {}).get("contacts", [])
        found = next((c for c in contacts if c["id"] == contact_id), None)
        assert found is not None, f"Contact {contact_id} not found by query={tg_id}"

        leads = found.get("_embedded", {}).get("leads", [])
        lead_ids = [lead["id"] for lead in leads]

        print(f"\n✓ Contact search by query={tg_id} returned embedded lead(s): {lead_ids}")

        assert "_embedded" in found
        assert lead_id in lead_ids
    finally:
        await client.close()
