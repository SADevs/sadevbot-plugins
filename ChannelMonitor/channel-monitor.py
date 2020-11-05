from collections import OrderedDict
from datetime import datetime
from datetime import timedelta
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

        self.start_poller(
            self.config["CHANMON_LOG_JANITOR_INTERVAL"],
            self._log_janitor,
            args=(self.config["CHANMON_LOG_DAYS"]),
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
