import copy
import json
import logging
import os
import time
from datetime import datetime
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

extra_plugin_dir = "."

log = logging.getLogger(__name__)

CHANNEL = "#test"
USER = "@tester"

TEST_TEMPLATE = {"dry_run": "DRY", "archive": "ARCHIVE"}


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


def test_send_archive_message_dry_run(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._send_archive_message({"id": "C012AB3CD"}, dry_run=True)
    message = testbot.pop_message()
    assert (
        plugin.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["dry_run"][0:10] in message
    )


def test_send_archive_message(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._send_archive_message({"id": "C012AB3CD"}, dry_run=False)
    message = testbot.pop_message()
    assert (
        plugin.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["archive"][0:10] in message
    )


def test_archive_channel_dry_run_success(testbot, mocker):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._bot.api_call = mocker.MagicMock(return_value={"ok": True})

    plugin._archive_channel({"id": "C012AB3CD"}, dry_run=True)
    message = testbot.pop_message()
    assert (
        plugin.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["dry_run"][0:10] in message
    )


def test_archive_channel_failure(testbot, mocker):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._bot.api_call = mocker.MagicMock(return_value={"ok": False, "error": "test"})

    plugin._archive_channel({"id": "C012AB3CD", "name": "Test"}, dry_run=False)
    message = testbot.pop_message()
    assert (
        plugin.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["archive"][0:10] in message
    )
    message = testbot.pop_message()
    assert "Tried to archive channel test and hit an error: test"[0:10] in message


def test_archive_channel_success(testbot, mocker):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")
    plugin._bot.api_call = mocker.MagicMock(return_value={"ok": True, "error": "test"})

    plugin._archive_channel({"id": "C012AB3CD", "name": "Test"}, dry_run=False)
    message = testbot.pop_message()
    assert (
        plugin.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["archive"][0:10] in message
    )


def test_should_archive(testbot, mocker):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")

    plugin["channel_archive_whitelist"] = ["whitelisted", "C012AB3CD"]
    plugin.config["CHANNEL_ARCHIVE_MEMBER_COUNT"] = 10

    # archived channels should be false
    assert plugin._should_archive({"is_archived": True}) is False

    # not a channel should be false
    assert plugin._should_archive({"is_archived": False, "is_channel": False}) is False

    # general channel should be false
    assert (
        plugin._should_archive(
            {"is_archived": False, "is_channel": True, "is_general": True}
        )
        is False
    )

    # whitelisted channel should be false
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "whitelisted",
            }
        )
        is False
    )

    # whitelisted id should be false
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3CD",
            }
        )
        is False
    )

    # brand new channel should be false
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3",
                "created": time.time(),
            }
        )
        is False
    )

    plugin._bot.api_call = mocker.MagicMock(
        return_value={"ok": True, "latest": time.time()}
    )

    # channel with latest right now should be false
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3",
                "created": 100,
                "num_members": 12,
            }
        )
        is False
    )

    plugin._bot.api_call = mocker.MagicMock(
        return_value={"ok": True, "messages": [{"ts": time.time()}]}
    )
    # channel with message right now should be false
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3",
                "created": 100,
                "num_members": 1,
            }
        )
        is False
    )

    plugin._bot.api_call = mocker.MagicMock(return_value={"ok": True, "messages": []})
    # channel with no messages should be true
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3",
                "created": 100,
                "num_members": 1,
            }
        )
        is True
    )

    plugin._bot.api_call = mocker.MagicMock(
        return_value={"ok": True, "messages": [{"ts": 478059599}]}
    )
    # channel with no messages since 1985-02-24 should be true
    assert (
        plugin._should_archive(
            {
                "is_archived": False,
                "is_channel": True,
                "is_general": False,
                "name": "test",
                "id": "C012AB3",
                "created": 100,
                "num_members": 1,
            }
        )
        is True
    )


def test_get_message_templates(testbot):
    plugin = testbot.bot.plugin_manager.get_plugin_obj_by_name("ChannelMonitor")

    with TemporaryDirectory() as directory:
        temp_path = os.path.join(directory, str(uuid4()))
        with open(temp_path, "w") as fh:
            fh.write(json.dumps(TEST_TEMPLATE))
        result = plugin._get_message_templates(temp_path)
        assert result["dry_run"] == "DRY"
        assert result["archive"] == "ARCHIVE"

        bad_template = copy.deepcopy(TEST_TEMPLATE)
        bad_template.pop("archive")
        with open(temp_path, "w") as fh:
            fh.write(json.dumps(bad_template))
        with pytest.raises(Exception):
            plugin._get_message_templates(temp_path)

        missing_template = copy.deepcopy(TEST_TEMPLATE)
        missing_template.pop("dry_run")
        with open(temp_path, "w") as fh:
            fh.write(json.dumps(missing_template))

        result = plugin._get_message_templates(temp_path)
        assert result["dry_run"] == result["archive"]
