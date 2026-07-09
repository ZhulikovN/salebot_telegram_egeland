import pytest

from app.services.amocrm_client import AmoCRMClient
#  poetry run pytest tests/test_amocrm_client/test_amocrm_client.py -v -s --log-cli-level=INFO

TEST_CONTACT_ID = 60758413
TEST_LEAD_ID = 40562799      # живая сделка → ожидаем dict с данными
TEST_ABSORBED_LEAD_ID = 40564811  # поглощён NOVA → ожидаем {}
TEST_TG_ID = "5305636742"
TEST_USERNAME = "test_username"
TEST_ABSORBED_CONTACT_ID = 60799113  # поглощён NOVA → ожидаем {}



@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_contact() -> None:
    client = AmoCRMClient()

    # # Живой контакт — должен вернуть dict с данными
    # result = await client.get_contact(TEST_CONTACT_ID)
    # if result is None:
    #     print(f"\n✗ Contact {TEST_CONTACT_ID} returned None (404 or error)")
    # elif result == {}:
    #     print(f"\n✗ Contact {TEST_CONTACT_ID} returned {{}} (204 — absorbed)")
    # else:
    #     print(f"\n✓ Contact {TEST_CONTACT_ID} is alive: name={result.get('name')}")

    # Поглощённый контакт — должен вернуть {}
    result_absorbed = await client.get_contact(TEST_ABSORBED_CONTACT_ID)
    if result_absorbed == {}:
        print(f"✓ Contact {TEST_ABSORBED_CONTACT_ID} is absorbed (204) — {{}} as expected")
    elif result_absorbed is None:
        print(f"? Contact {TEST_ABSORBED_CONTACT_ID} returned None (404 or error)")
    else:
        print(f"? Contact {TEST_ABSORBED_CONTACT_ID} is alive: name={result_absorbed.get('name')}")

    await client.close()


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_get_lead() -> None:
#     client = AmoCRMClient()
#
#     try:
#         # Живая сделка — должна вернуть dict с данными
#         result = await client.get_lead(TEST_LEAD_ID)
#         if result is None:
#             print(f"\n✗ Lead {TEST_LEAD_ID} returned None (404 or error)")
#         elif result == {}:
#             print(f"\n✗ Lead {TEST_LEAD_ID} returned {{}} (204 — absorbed)")
#         else:
#             print(f"\n✓ Lead {TEST_LEAD_ID} is alive: name={result.get('name')}, status={result.get('status_id')}")
#
#         # Поглощённая сделка — должна вернуть {}
#         result_absorbed = await client.get_lead(TEST_ABSORBED_LEAD_ID)
#         if result_absorbed == {}:
#             print(f"✓ Lead {TEST_ABSORBED_LEAD_ID} is absorbed (204) — {{}} as expected")
#         elif result_absorbed is None:
#             print(f"? Lead {TEST_ABSORBED_LEAD_ID} returned None (404 or error)")
#         else:
#             print(f"? Lead {TEST_ABSORBED_LEAD_ID} is alive: status={result_absorbed.get('status_id')}")
#     finally:
#         await client.close()


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_find_contact_by_tg_id() -> None:
#     client = AmoCRMClient()
#
#     contact = await client.find_contact_by_tg_id(TEST_TG_ID)
#
#     if contact:
#         # assert contact["id"] == TEST_CONTACT_ID
#         # assert "name" in contact
#         # assert "custom_fields_values" in contact
#         # print(f"\n✓ Found contact by TG ID: {contact['id']}")
#         print(f"✓ Contact name: {contact['name']}")
#         print(f"✓ Contact: {contact}")
#     else:
#         print(f"\n✓ Contact not found by TG ID: {TEST_TG_ID}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_find_contact_by_username() -> None:
#     client = AmoCRMClient()
#
#     contact = await client.find_contact_by_username(TEST_USERNAME)
#
#     if contact:
#         assert contact["id"] == TEST_CONTACT_ID
#         assert "name" in contact
#         print(f"\n✓ Found contact by username: {contact['id']}")
#         print(f"✓ Contact name: {contact['name']}")
#     else:
#         print(f"\n✓ Contact not found by username: {TEST_USERNAME}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_create_contact() -> None:
#     client = AmoCRMClient()
#
#     contact_id = await client.create_contact(
#         name="Тестовый контакт",
#         tg_id="999999999",
#         tg_username="test_user_pytest",
#     )
#
#     assert contact_id > 0
#     print(f"\n✓ Contact created: {contact_id}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_update_contact() -> None:
#     client = AmoCRMClient()
#
#     await client.update_contact(
#         contact_id=TEST_CONTACT_ID,
#         fields={
#             1362361: 111111,  # FIELD_TG_ID
#         },
#     )
#
#     print(f"\n✓ Contact {TEST_CONTACT_ID} updated")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_create_lead() -> None:
#     client = AmoCRMClient()
#
#     lead_id = await client.create_lead(
#         contact_id=47353447,
#         bot_name="TestBot",
#         course_direction="ЕГЭ",
#     )
#
#     assert lead_id > 0
#     print(f"\n✓ Lead created: {lead_id}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_update_lead() -> None:
#     client = AmoCRMClient()
#
#     await client.update_lead(
#         lead_id=34207141,
#         fields={
#             1362369: "UpdatedBotName",  # FIELD_BOT_NAME
#         },
#     )
#
#     print(f"\n✓ Lead {TEST_LEAD_ID} updated")


# def test_parse_custom_fields() -> None:
#     client = AmoCRMClient()
#
#     custom_fields = [
#         {"field_id": 123, "values": [{"value": "test_value"}]},
#         {"field_id": 456, "values": [{"enum_id": 789}]},
#         {"field_id": 999, "values": [{"enum_id": 111}, {"enum_id": 222}]},
#         {"field_id": 888, "values": []},
#         {"field_id": 777, "values": [{"value": "val1"}, {"value": "val2"}]},
#     ]
#
#     result = client._parse_custom_fields(custom_fields)
#
#     assert result[123] == "test_value"
#     assert result[456] == 789
#     assert result[999] == [111, 222]
#     assert 888 not in result
#     assert result[777] == ["val1", "val2"]
#
#     print(f"\n✓ Parsed fields: {result}")


# def test_parse_custom_fields_empty() -> None:
#     client = AmoCRMClient()
#
#     result = client._parse_custom_fields(None)
#     assert result == {}
#
#     result = client._parse_custom_fields([])
#     assert result == {}
#
#     print("\n✓ Empty custom fields parsed correctly")


# def test_parse_custom_fields_with_enum() -> None:
#     client = AmoCRMClient()
#
#     custom_fields = [
#         {
#             "field_id": 1362369,
#             "field_name": "Название бота",
#             "values": [{"value": "ElAuthBot"}],
#         },
#         {
#             "field_id": 1183221,
#             "field_name": "Тариф",
#             "values": [{"enum_id": 12345, "value": "ЕГЭ"}],
#         },
#     ]
#
#     result = client._parse_custom_fields(custom_fields)
#
#     assert result[1362369] == "ElAuthBot"
#     assert result[1183221] == 12345
#
#     print(f"\n✓ Custom fields with enum parsed: {result}")


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_create_chat_in_amojo() -> None:
#     """Тест создания чата в amojo."""
#     from uuid import uuid4
#
#     client = AmoCRMClient()
#
#     try:
#         conversation_id = f"tg:111111:TestBot_{uuid4()}"
#         user_id = "tg:111111"
#         user_name = "Тестовый Контакт pytest"
#
#         chat_id = await client.create_chat_in_amojo(
#             conversation_id=conversation_id,
#             user_id=user_id,
#             user_name=user_name,
#             profile_link="https://t.me/test",
#         )
#
#         assert chat_id is not None
#         assert len(chat_id) == 36  # UUID формат
#         print(f"\n✓ Chat created in amojo: {chat_id}")
#         print(f"✓ conversation_id: {conversation_id}")
#     finally:
#         await client.close()


# # @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_link_chat_to_contact() -> None:
#     """Тест привязки чата к контакту (используя реальный chat_id)."""
#     client = AmoCRMClient()
#
#     try:
#         # ВАЖНО: Это должен быть СУЩЕСТВУЮЩИЙ chat_id из amojo
#         # Получи его из предыдущего теста или из amoCRM UI
#         # Формат: UUID (36 символов)
#         EXISTING_CHAT_ID = "dbdf255c-3f50-40a2-9a29-f4563480a570"  # Замени на реальный
#
#         print(f"\n✓ Using existing chat_id: {EXISTING_CHAT_ID}")
#
#         # Привязываем существующий чат к контакту
#         await client.link_chat_to_contact(
#             contact_id=47353447,
#             chat_id=EXISTING_CHAT_ID,
#         )
#         print(f"✓ Chat {EXISTING_CHAT_ID} linked to contact {TEST_CONTACT_ID}")
#     finally:
#         await client.close()

# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_full_chat_flow() -> None:
#     """Полный поток: создание чата + привязка + отправка сообщения."""
#     from uuid import uuid4
#
#     from app.services.amojo_client import AmojoClient
#
#     amocrm_client = AmoCRMClient()
#     amojo_client = AmojoClient()
#
#     try:
#         # 1. Генерируем уникальные ID
#         unique_id = uuid4()
#         tg_id = "111111"
#         bot_name = f"TestBot_{unique_id}"
#         conversation_id = f"tg:{tg_id}:{bot_name}"
#         user_id = f"tg:{tg_id}"
#         user_name = "Тестовый Контакт pytest"
#
#         print(f"\n✓ conversation_id: {conversation_id}")
#
#         # 2. Создаем чат в amojo
#         chat_id = await amocrm_client.create_chat_in_amojo(
#             conversation_id=conversation_id,
#             user_id=user_id,
#             user_name=user_name,
#             profile_link="https://t.me/test",
#         )
#         print(f"✓ Chat created in amojo: {chat_id}")
#
#         # 3. Привязываем чат к контакту
#         await amocrm_client.link_chat_to_contact(
#             contact_id=47418161,
#             chat_id=chat_id,
#         )
#         print(f"✓ Chat linked to contact {TEST_CONTACT_ID}")
#
#         # 4. Отправляем сообщение в этот чат
#         msgid = f"pytest:{uuid4()}"
#         await amojo_client.send_incoming_message(
#             conversation_id=conversation_id,
#             msgid=msgid,
#             sender_id=user_id,
#             sender_name=user_name,
#             text="pytest: тестовое сообщение после привязки чата",
#             silent=True,  # не создавать Неразобранное
#             profile_link="https://t.me/test",
#         )
#         print(f"✓ Message sent to linked chat: {msgid}")
#
#         print("\n Full flow completed: chat created, linked, and message sent!")
#     finally:
#         await amocrm_client.close()


# @pytest.mark.asyncio
# @pytest.mark.integration
# async def test_check_duplicate_lead() -> None:
#     """Тест поиска открытой сделки у контакта."""
#     client = AmoCRMClient()
#
#     try:
#         # ID контакта для теста
#         test_contact_id = 60766225
#
#         # Ищем открытую сделку
#         # open_lead = await client.check_duplicate_lead(contact_id=test_contact_id, pipeline_id=8598230)
#         open_lead = await client.check_duplicate_lead(contact_id=test_contact_id)
#
#         if open_lead:
#             print(f"\n✓ FULL lead: {open_lead}")
#             print(f"\n✓ Open lead found: {open_lead['id']}")
#             print(f"  - Pipeline: {open_lead.get('pipeline_id')}")
#             print(f"  - Status: {open_lead.get('status_id')}")
#
#             # Проверяем что сделка действительно привязана к контакту
#             contacts = open_lead.get("_embedded", {}).get("contacts", [])
#             contact_ids = [c["id"] for c in contacts]
#
#             assert test_contact_id in contact_ids, (
#                 f"Lead {open_lead['id']} is not linked to contact {test_contact_id}! "
#                 f"Linked to: {contact_ids}"
#             )
#             print(f"  - ✓ Lead is correctly linked to contact {test_contact_id}")
#         else:
#             print(f"\n✓ No open leads found for contact {test_contact_id}")
#
#     finally:
#         await client.close()
#
#
