import pytest

from qa_bot.config import ConfigError, load_settings


def test_subscriptionは本人1人なら起動できる(base_env):
    s = load_settings(base_env)
    assert s.auth_mode == "subscription"
    assert s.is_slack_user_allowed("UOWNER")
    assert not s.is_slack_user_allowed("UOTHER")
    assert s.daily_limit_per_user == 20 and s.retention_days == 365 and s.session_retention_days == 30


def test_subscriptionはSlack利用者なしでもCLI用に起動できる(base_env):
    base_env["ALLOWED_SLACK_USERS"] = ""
    assert not load_settings(base_env).is_slack_user_allowed("UOWNER")


@pytest.mark.parametrize("users", ["UOWNER,UOTHER", "*"])
def test_subscriptionで複数人に公開しようとすると起動しない(base_env, users):
    base_env["ALLOWED_SLACK_USERS"] = users
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_subscriptionでAPIキーが残っていると起動しない(base_env):
    base_env["ANTHROPIC_API_KEY"] = "sk-ant-xxx"
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_apiはキーがないと起動しない(base_env):
    base_env["AUTH_MODE"] = "api"
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_apiは全員に公開できる(base_env):
    base_env.update(AUTH_MODE="api", ANTHROPIC_API_KEY="sk-ant-xxx", ALLOWED_SLACK_USERS="*")
    assert load_settings(base_env).is_slack_user_allowed("UANYONE")


def test_認証モードの指定は必須(base_env):
    base_env["AUTH_MODE"] = ""
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_ミラーがなければ起動しない(base_env, tmp_path):
    base_env["MIRROR_PATH"] = str(tmp_path / "none.git")
    with pytest.raises(ConfigError):
        load_settings(base_env)


@pytest.mark.parametrize("key, value", [("DAILY_LIMIT_PER_USER", "abc"), ("TIMEZONE", "Mars/Base")])
def test_値の誤りは起動前に分かる(base_env, key, value):
    base_env[key] = value
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_ユーザーグループをIDかハンドル名で指定できる(base_env):
    base_env.update(
        AUTH_MODE="api", ANTHROPIC_API_KEY="sk-ant-xxx",
        ALLOWED_SLACK_USERS="UONE,S0123ABCD,@sales", ADMIN_SLACK_USERS="@qa-admins",
    )
    s = load_settings(base_env)
    assert s.allowed_slack_groups == ("S0123ABCD", "@sales")
    assert s.admin_slack_groups == ("@qa-admins",)
    assert s.is_slack_user_allowed("UONE")
    assert not s.is_slack_user_allowed("UMEMBER")
    assert s.is_slack_user_allowed("UMEMBER", frozenset({"UMEMBER"}))
    assert s.is_admin("UMEMBER", frozenset({"UMEMBER"}))


@pytest.mark.parametrize("users", ["UOWNER,@sales", "S0123ABCD"])
def test_subscriptionではユーザーグループを指定できない(base_env, users):
    base_env["ALLOWED_SLACK_USERS"] = users
    with pytest.raises(ConfigError):
        load_settings(base_env)
