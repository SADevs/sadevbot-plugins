import logging

from errbot.backends.slack import SlackBackend

log = logging.getLogger(__name__)


def _make_handler_function(callback_name):
    def _function(message):
        self._dispatch_to_plugins(callback_name, message)  # noqa: F821

    return _function


class SlackExtendedBackend(SlackBackend):
    def __init__(self, config):
        super().__init__(config)
        # be careful not to overwrite any events that the slack backend handles by default
        # This will cause things to break unexpectedly!
        # These events come straight from the slack api's event types
        self.extra_events = [
            "channel_created",
            "channel_deleted",
            "channel_archive",
            "channel_rename",
            "channel_unarchive",
            "pin_added",
            "pin_removed",
            "star_added",
            "star_removed",
        ]
        # use _make_handler_function to dynamically create a class method for each of our event types
        # This method will call self._dispatch_to_plugins with callback_{event_type} and the message as args.
        # _dispatch_to_plugins takes care of calling all the potential callbacks in all plugins for us and passes
        # along the message as an argument
        for event in self.extra_events:
            # add a handler in for any event type that doesn't have a handler already created
            if getattr(self, f"{event}_handler", None) is None:
                setattr(
                    self,
                    f"{event}_handler",
                    _make_handler_function(f"callback_{event}"),
                )

    def _dispatch_slack_message(self, message):
        """
        Calls the super() of this method, then adds extra events that we want to process in our Extended backend
        """
        super()._dispatch_slack_message(message)

        event_type = message.get("type", None)
        extra_event_handler = getattr(self, f"{event_type}_handler", None)

        if extra_event_handler is not None:
            log.debug(
                "Processing slack event for event_type %s on msg: %s",
                event_type,
                message,
            )
            try:
                extra_event_handler(message)
            except Exception as err:
                log.error(
                    "Event_type %s for msg %s raised an exception: %s",
                    event_type,
                    message,
                    err,
                )
        else:
            log.debug(
                "No event_handler for %s, ignoring this message: %s. "
                "If you want to handle this event add a handler for it in the Extended backend",
                event_type,
                message,
            )
        return

    def _member_joined_channel_event_handler(self, event):
        """Calls the super() of this method and then adds our own call to "callback_member_joined_room"

        The callback_member_joined_room signature should look like this:
        def callback_member_joined_room(self, user: SlackPerson, channelid: str)
        """
        # This one method is special because its an event that's already handled by the main slack backend
        # We're just wanting to extend its behavior. Rather than add this as an extra event, this medhod just
        # adds extra functionality to the existing event
        super()._member_joined_channel_event_handler(event)
        self._dispatch_to_plugins("callback_member_joined_room", event)
