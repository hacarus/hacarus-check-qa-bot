import pytest

from qa_bot.config import ConfigError, load_settings


def test_subscriptionは本人1人なら起動できる(base_env):
    s = load_settings(base_env)
    assert s.auth_mode == "subscription"
    assert s.is_slack_user_allowed("UOWNER")
    assert not s.is_slack_user_allowed("UOTHER")


def test_subscriptionはSlack利用者なしでもCLI用に起動できる(base_env):
    base_env["ALLOWED_SLACK_USERS"] = ""
    s = load_settings(base_env)
    assert not s.is_slack_user_allowed("UOWNER")


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
    s = load_settings(base_env)
    assert s.is_slack_user_allowed("UANYONE")


def test_認証モードの指定は必須(base_env):
    base_env["AUTH_MODE"] = ""
    with pytest.raises(ConfigError):
        load_settings(base_env)


def test_存在しないリポジトリは起動しない(base_env, tmp_path):
    base_env["REPO_PATH"] = str(tmp_path / "none")
    with pytest.raises(ConfigError):
        load_settings(base_env)
