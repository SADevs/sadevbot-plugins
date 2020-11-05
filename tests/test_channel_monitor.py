import logging
from datetime import datetime

extra_plugin_dir = "."

log = logging.getLogger(__name__)

CHANNEL = "#test"
USER = "@tester"


def test_print_channel_log(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._log_channel_change("#test", "@tester", "delete", 12345)
    plugin._log_channel_change("#test2", "@tester", "archive", 78901)
    testbot.push_message("!print channel log")
    message = testbot.pop_message()
    assert "#test" in message
    assert "@tester" in message
    assert "#test2" in message
    assert "delete" in message
    assert "archive" in message
    assert "12345" in message


def test_run_log_cleaner(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._log_channel_change("#test", "@tester", "delete", 12345)
    plugin._log_channel_change("#test2", "@tester", "archive", 78901)
    today = datetime.now().strftime("%Y-%m-%d")
    assert len(plugin["channel_action_log"][today]) == 2
    testbot.push_message("!run log cleaner 0")
    message = testbot.pop_message()
    assert "is clearing Channel Monitor logs for 0" in message
    message = testbot.pop_message()
    assert "Log cleanup complete" in message
    assert today not in plugin["channel_action_log"]


def test_build_log(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    log = plugin._build_log(CHANNEL, USER, "create", 12345)

    assert log["channel"] == CHANNEL
    assert log["user"] == USER
    assert log["action"] == "create"
    assert log["timestamp"] == 12345
    assert log["string_repr"] == f"12345: {USER} created {CHANNEL}."


def test_log_channel_change(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._log_channel_change("#test", "@tester", "delete", 12345)
    plugin._log_channel_change("#test2", "@tester", "archive", 78901)
    today = datetime.now().strftime("%Y-%m-%d")
    assert len(plugin["channel_action_log"][today]) == 2


def test_log_janitor(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._log_channel_change("#test", "@tester", "delete", 12345)
    plugin._log_channel_change("#test2", "@tester", "archive", 78901)
    today = datetime.now().strftime("%Y-%m-%d")
    assert len(plugin["channel_action_log"][today]) == 2
    plugin._log_janitor(0)
    assert today not in plugin["channel_action_log"]


def test_get_logs_text(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._log_channel_change(CHANNEL, USER, "delete", 12345)
    plugin._log_channel_change("#test2", USER, "archive", 78901)
    today = datetime.now().strftime("%Y-%m-%d")
    logs_text = plugin._get_logs_text(plugin["channel_action_log"])
    assert len(logs_text) == 1
    assert today in logs_text[0]
    assert CHANNEL in logs_text[0]
    assert USER in logs_text[0]
    assert "\n" in logs_text[0]
    assert "12345" in logs_text[0]
    assert "78901" in logs_text[0]
    assert "#test2" in logs_text[0]
