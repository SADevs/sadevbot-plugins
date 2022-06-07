import json
import time
from collections import OrderedDict
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from threading import RLock
from time import mktime
from typing import Any
from typing import Dict
from typing import List

from decouple import config as get_config
from errbot import arg_botcmd
from errbot import botcmd
from errbot import BotPlugin
from pendulum import parse
from pytz import UTC
from wrapt import synchronized

CAL_LOCK = RLock()
CAR_LOCK = RLock()


def get_config_item(
    key: str, config: Dict, overwrite: bool = False, **decouple_kwargs
) -> Any:
    """
    Checks config to see if key was passed in, if not gets it from the environment/config file

    If key is already in config and overwrite is not true, nothing is done. Otherwise, config var is added to config
    at key
    """
    if key not in config and not overwrite:
        config[key] = get_config(key, **decouple_kwargs)


class ChannelMonitor(BotPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def configure(self, configuration: Dict) -> None:
        """
        Configures the plugin
        """
        self.log.debug("Starting Config")
        if configuration is None:
            configuration = OrderedDict()

        # name of the channel to post in
        get_config_item("CHANMON_CHANNEL", configuration, default="")
        configuration["CHANMON_CHANNEL_ID"] = (
            self.build_identifier(configuration["CHANMON_CHANNEL"])
            if configuration["CHANMON_CHANNEL"] != ""
            else None
        )
        get_config_item("CHANMON_LOG_DAYS", configuration, default=90, cast=int)
        get_config_item(
            "CHANMON_LOG_JANITOR_INTERVAL", configuration, default=600, cast=int
        )

        get_config_item(
            "CHANNEL_ARCHIVE_WHITELIST",
            configuration,
            default="",
            cast=lambda v: [s for s in v.split(",")],
        )
        get_config_item(
            "CHANNEL_ARCHIVE_MESSAGE_TEMPLATE_PATH",
            configuration,
            default="/config/channel_archive_template.json",
        )
        configuration[
            "CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"
        ] = self._get_message_templates(
            configuration["CHANNEL_ARCHIVE_MESSAGE_TEMPLATE_PATH"]
        )
        get_config_item(
            "CHANNEL_ARCHIVE_AT_LEAST_AGE", configuration, default="45", cast=int
        )
        configuration["CHANNEL_ARCHIVE_AT_LEAST_AGE_SECONDS"] = (
            configuration["CHANNEL_ARCHIVE_AT_LEAST_AGE"] * 24 * 60 * 60
        )

        get_config_item(
            "CHANNEL_ARCHIVE_LAST_MESSAGE", configuration, default="30", cast=int
        )
        configuration["CHANNEL_ARCHIVE_LAST_MESSAGE_SECONDS"] = (
            configuration["CHANNEL_ARCHIVE_LAST_MESSAGE"] * 24 * 60 * 60
        )

        get_config_item(
            "CHANNEL_ARCHIVE_JANITOR_INTERVAL", configuration, default=3600, cast=float
        )
        super().configure(configuration)

    def activate(self):
        super().activate()
        # setup our on disk log
        with synchronized(CAL_LOCK):
            try:
                self["channel_action_log"]
            except KeyError:
                self["channel_action_log"] = {
                    datetime.now().strftime("%Y-%m-%d"): list()
                }

        try:
            self["channel_archive_whitelist"]
        except KeyError:
            self["channel_archive_whitelist"] = self.config["CHANNEL_ARCHIVE_WHITELIST"]

        self.start_poller(
            self.config["CHANMON_LOG_JANITOR_INTERVAL"],
            self._log_janitor,
            args=(self.config["CHANMON_LOG_DAYS"]),
        )
        # Dry run poller
        self.start_poller(
            self.config["CHANNEL_ARCHIVE_JANITOR_INTERVAL"],
            self._channel_janitor,
            args=(True),
        )
        # archive poller
        self.start_poller(
            self.config["CHANNEL_ARCHIVE_JANITOR_INTERVAL"] + 3600,
            self._channel_janitor,
        )

    def deactivate(self):
        self.stop_poller(self._log_janitor, args=(self.config["CHANMON_LOG_DAYS"]))
        super().deactivate()

    @botcmd(admin_only=True)
    def print_channel_log(self, msg, _) -> None:
        logs_text = self._get_logs_text(self["channel_action_log"])
        self.log.debug("Got logs text of %i length", len(logs_text))
        if len(logs_text) == 0:
            yield "No logs"
        for log in logs_text:
            yield log

    @botcmd(admin_only=True)
    @arg_botcmd("day_count", type=int)
    def run_log_cleaner(self, msg, day_count: int) -> str:
        self.warn_admins(f"{msg.frm} is clearing Channel Monitor logs for {day_count}")
        self._log_janitor(day_count)
        return "Log cleanup complete"

    # Callbacks
    def callback_channel_created(self, msg: Dict) -> None:
        """Received the callback from the SlackExtendedBackend for channel_created"""
        action = "create"
        self._log_channel_change(
            channel_name=f"#{msg['channel']['name']}",
            user_name=f"@{self._get_user_name(msg['channel']['creator'])}",
            action=action,
            timestamp=msg["channel"]["created"],
        )

    def callback_channel_archive(self, msg: Dict) -> None:
        """Received the callback from the SlackExtendedBackend for channel_archive"""
        action = "archive"
        self._log_channel_change(
            channel_name=f"#{self._get_channel_name(msg['channel'])}",
            user_name=f"@{self._get_user_name(msg['user'])}",
            action=action,
            timestamp=mktime(datetime.now().timetuple()),
        )

    def callback_channel_deleted(self, msg: Dict) -> None:
        """Received the callback from the SlackExtendedBackend for channel_deleted"""
        action = "delete"
        self._log_channel_change(
            channel_name=f"#{self._get_channel_name(msg['channel'])}",
            user_name=None,
            action=action,
            timestamp=mktime(datetime.now().timetuple()),
        )

    def callback_channel_unarchive(self, msg: Dict) -> None:
        """Received the callback from the SlackExtendedBackend for channel_unarchive"""
        action = "unarchive"
        self._log_channel_change(
            channel_name=f"#{self._get_channel_name(msg['channel'])}",
            user_name=f"@{self._get_user_name(msg['user'])}",
            action=action,
            timestamp=mktime(datetime.now().timetuple()),
        )

    # Util methods
    def _log_channel_change(
        self, channel_name: str, user_name: str, action: str, timestamp: str
    ) -> None:
        """Logs a channel change event"""
        log = self._build_log(channel_name, user_name, action, timestamp)
        if self.config["CHANMON_CHANNEL_ID"] is not None:
            self._send_log_to_slack(log)

        with synchronized(CAL_LOCK):
            chan_log = self["channel_action_log"]
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                chan_log[today].append(log)
            except KeyError:
                chan_log[today] = list()
                chan_log[today].append(log)
            self["channel_action_log"] = chan_log

    @staticmethod
    def _build_log(channel: str, user: str, action: str, timestamp: str) -> Dict:
        """Builds a log dict"""
        return {
            "channel": channel,
            "user": user,
            "action": action,
            "timestamp": timestamp,
            "string_repr": f"{timestamp}: {user} {action}d {channel}.",
        }

    @staticmethod
    def _get_logs_text(logs: Dict) -> List[str]:
        """Turns a dict of lists into a printable slack log table"""
        days = list()
        for day, logs in logs.items():
            logs_str_reprs = [log["string_repr"] for log in logs]
            logs_str = "\n".join(logs_str_reprs)
            days.append(f"*{day}*\n{logs_str}")

        return days

    def _get_channel_name(self, channel: str) -> str:
        """Returns a channel name from a channel id. Loose wrapper around channelid_to_channelname with a LRU cache"""
        return self._bot.channelid_to_channelname(channel)

    def _get_user_name(self, user: str) -> str:
        """Returns a username from a userid. Loose wrapper around userid_to_username with a LRU cache"""
        return self._bot.userid_to_username(user)

    def _send_log_to_slack(self, log: Dict) -> None:
        """Sends a log to a slack channel"""
        self.send(self.config["CHANMON_CHANNEL_ID"], log["string_repr"])

    def _send_archive_message(self, channel: Dict, dry_run: bool) -> None:
        """Sends a templated message to channel, based on dry_run

        Arguments:
            channel {Dict} -- Channel object
            dry_run {bool} -- whether or not this is a dry run
        """
        if dry_run:
            message = self.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["dry_run"]
        else:
            message = self.config["CHANNEL_ARCHIVE_MESSAGE_TEMPLATES"]["archive"]

        self.send(self.build_identifier(channel["id"]), message)

    def _archive_channel(self, channel: Dict, dry_run: bool) -> None:
        """Sends a message to each channel to be archived and archives it, based on dry_run

        Arguments:
            channel {Dict} -- Channel object
            dry_run {bool} -- Whether this is a dry_run or not
        """
        self._send_archive_message(channel, dry_run)
        if not dry_run:
            response = self._bot.api_call(
                "conversations.archive", data={"channel": channel["id"]}
            )
            if not response["ok"]:
                self.warn_admins(
                    f"Tried to archive channel {channel['name']} and hit an error: {response['error']}"
                )

    def _should_archive(self, channel: Dict) -> bool:
        """Checks if a channel should be archived based on our config

        Arguments:
            channel {Dict} -- channel object

        Returns:
            bool -- if the channel should be archived
        """
        now = int(time.time())

        # check data we have first, before hitting the slack API again

        # if somehow we get an archived channel, this prevents the error
        if channel["is_archived"]:
            self.log.debug("channel is archived")
            return False

        # only care about slack channels, nothing else
        if not channel["is_channel"]:
            self.log.debug("channel isn't a channel")
            return False

        # check if this is the general channel and cannot be archived
        if channel["is_general"]:
            self.log.debug("channel is general")
            return False

        # check if name whitelisted
        if channel["name"] in self["channel_archive_whitelist"]:
            self.log.debug("channel name is in whitelist")
            return False

        # check if id whitelisted
        if channel["id"] in self["channel_archive_whitelist"]:
            self.log.debug("channel id is whitelisted")
            return False

        # check if the channel is old enough to be archived
        if (
            now - channel["created"]
            < self.config["CHANNEL_ARCHIVE_AT_LEAST_AGE_SECONDS"]
        ):
            self.log.debug("channel isn't old enough to archive")
            return False

        # check min members
        if (
            self.config["CHANNEL_ARCHIVE_MEMBER_COUNT"] != 0
            and channel["num_members"] > self.config["CHANNEL_ARCHIVE_MEMBER_COUNT"]
        ):
            self.log.debug("channel has too many members to archive")
            return False

        # get the ts of the last message in the channel
        messages = self._bot.api_call(
            "conversations.history", data={"inclusive": 0, "oldest": 0, "count": 50}
        )
        if "latest" in messages:
            ts = messages["latest"]
            self.log.debug(f"Got {ts} from latest")
        else:
            # if we don't have a latest from the api, try to get the last message in the messages
            # If there are no messages, return an absurdly small timestamp (arbitrarily 100)
            ts = (
                messages["messages"][-1]["ts"] if len(messages["messages"]) > 0 else 100
            )
            self.log.debug(f"No latest, got TS from message {ts}")

        # check if its been too long since a message in the channel
        if now - ts > self.config["CHANNEL_ARCHIVE_LAST_MESSAGE_SECONDS"]:
            self.log.debug("channel's last message isn't recent, archiving")
            return True

        self.log.debug("shouldarchive is falling through")
        return False

    def _get_all_channels(self) -> List[Dict]:
        """
        Gets a list of all slack channels from the slack api

        Returns:
            List[Dict] -- List of slack channel objects
        """
        channels = self._bot.api_call(
            "conversations.list", data={"exclude_archived": 1}
        )
        return channels["channels"]

    @staticmethod
    def _get_message_templates(file_path: str) -> Dict:
        """Reads templates from a file or returns defaults

        Arguments:
            file_path {str} -- path of the template file to read

        Returns:
            Dict -- message templates
        """
        if not Path(file_path).is_file():
            return {
                "dry_run": "Warning: This channel will be archived on the next archive run due to inactivity. "
                + "To prevent this, post a mesage in this channel or whitelist it with `./whitelist #[channel_name]`",
                "archive": "This channel is being archived due to inactivity. If you feel this is a mistake you can "
                + "<https://get.slack.help/hc/en-us/articles/201563847-Archive-a-channel#unarchive-a-channel|unarchive"
                + " this channel>.",
            }
        with open(file_path, mode="r") as fh:
            data = json.load(fh)

        if "archive" not in data:
            raise Exception("Missing Archive template in template file")

        if "dry_run" not in data:
            data["dry_run"] = data["archive"]

        return data

    # Poller methods
    @synchronized(CAL_LOCK)
    def _log_janitor(self, days_to_keep: int) -> None:
        """Prunes our on-disk logs"""
        first_key = next(iter(self["channel_action_log"]))
        if UTC.localize(datetime.utcnow()) - parse(first_key) > timedelta(
            days=days_to_keep
        ):
            with synchronized(CAL_LOCK):
                cal_log = self["channel_action_log"]
                cal_log.pop(first_key, None)
                self["channel_action_log"] = cal_log

        cal_log = self["channel_action_log"]
        today = datetime.now().strftime("%Y-%m-%d")
        for key in cal_log.keys():
            if len(cal_log[key]) == 0 and key != today:
                cal_log.pop(key)
        self["channel_action_log"] = cal_log

    @synchronized(CAR_LOCK)
    def _channel_janitor(self, dry_run: bool = False) -> None:
        """Poller that cleans up channels that are old"""
        for channel in self._get_all_channels():
            if self._should_archive(channel):
                self._archive_channel(channel, dry_run)
