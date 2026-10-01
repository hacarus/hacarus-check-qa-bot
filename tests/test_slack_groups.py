from qa_bot.config import load_settings
from qa_bot.agent import FakeAgentRunner
from qa_bot.service import QAService
from qa_bot.slack_groups import GroupMembers
from qa_bot.store import Store


class GroupClient:
    def __init__(self, members: dict[str, list[str]], fail: bool = False):
        self.members = members
        self.fail = fail

    async def usergroups_list(self, **kw):
        if self.fail:
            raise RuntimeError("network")
        return {"usergroups": [{"id": "SSALES", "handle": "sales"}, {"id": "SADMIN", "handle": "qa-admins"}]}

    async def usergroups_users_list(self, usergroup: str):
        if self.fail:
            raise RuntimeError("network")
        return {"users": self.members.get(usergroup, [])}


async def test_IDとハンドル名のどちらでもメンバーを読む():
    g = GroupMembers()
    await g.refresh(GroupClient({"SSALES": ["U1", "U2"], "SADMIN": ["U3"]}), ["@sales", "SADMIN", "@unknown"])
    assert g.users(["@sales"]) == {"U1", "U2"}
    assert g.users(["SADMIN", "@sales"]) == {"U1", "U2", "U3"}
    assert g.users(["@unknown"]) == frozenset()


async def test_読めなかったときは前回のメンバーを残す():
    g = GroupMembers()
    await g.refresh(GroupClient({"SSALES": ["U1"]}), ["@sales", "SSALES"])
    await g.refresh(GroupClient({}, fail=True), ["@sales", "SSALES"])
    assert g.users(["@sales"]) == {"U1"} and g.users(["SSALES"]) == {"U1"}


async def test_グループのメンバーは質問でき管理者グループのメンバーは管理者になる(base_env, prices):
    base_env.update(
        AUTH_MODE="api", ANTHROPIC_API_KEY="sk-ant-xxx",
        ALLOWED_SLACK_USERS="@sales", ADMIN_SLACK_USERS="@qa-admins",
    )
    settings = load_settings(base_env)
    service = QAService(settings, FakeAgentRunner(settings.model), Store(settings.db_path), prices)
    assert not service.is_allowed("U1")
    await service.refresh_groups(GroupClient({"SSALES": ["U1"], "SADMIN": ["U3"]}))
    assert service.is_allowed("U1") and not service.is_admin("U1")
    assert service.is_admin("U3") and not service.is_allowed("U3")
