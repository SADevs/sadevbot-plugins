from threading import Thread
from typing import Any
from typing import Dict

from decouple import config as get_config
from errbot import botcmd
from errbot import BotPlugin
from errbot import webhook
from errbot.core_plugins import flask_app
from webtest import TestApp
from werkzeug.serving import ThreadedWSGIServer

TEST_REPORT = """*** Test Report
URL : %s
Detected your post as : %s
Status code : %i
"""


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


class Webserver(BotPlugin):
    def __init__(self, *args, **kwargs):
        self.server = None
        self.server_thread = None
        self.test_app = TestApp(flask_app)
        super().__init__(*args, **kwargs)

    def configure(self, configuration: Dict) -> None:
        """
        Configures the plugin
        """
        self.log.debug("Starting Config")
        if configuration is None:
            configuration = dict()

        # name of the channel to post in
        get_config_item("WEBSERVER_HTTP_PORT", configuration, default="3142")

        super().configure(configuration)

    def activate(self):
        if self.server_thread and self.server_thread.is_alive():
            raise Exception(
                "Invalid state, you should not have a webserver already running."
            )
        self.server_thread = Thread(target=self.run_server, name="Webserver Thread")
        self.server_thread.start()
        self.log.debug("Webserver started.")

        super().activate()

    def deactivate(self):
        if self.server is not None:
            self.log.info("Shutting down the internal webserver.")
            self.server.shutdown()
            self.log.info("Waiting for the webserver thread to quit.")
            self.server_thread.join()
            self.log.info("Webserver shut down correctly.")
        super().deactivate()

    def run_server(self):
        try:
            host = "127.0.0.1"
            port = int(self.config["WEBSERVER_HTTP_PORT"])
            self.log.info("Starting the webserver on %s:%i", host, port)
            self.server = ThreadedWSGIServer(host, port, flask_app)
            self.server.serve_forever()
            self.log.debug("Webserver stopped")
        except KeyboardInterrupt:
            self.log.info("Keyboard interrupt, request a global shutdown.")
            self.server.shutdown()
        except Exception:
            self.log.exception("The webserver exploded.")

    @botcmd
    def webstatus(self, msg, args):
        """
        Gives a quick status of what is mapped in the internal webserver
        """
        web_server_info = f"Web server is running on port {self.config['WEBSERVER_HTTP_PORT']}.\nConfigured Rules:\n"
        for rule in flask_app.url_map._rules:
            web_server_info += f"* {rule.rule} -> {rule.endpoint}\n"
        return web_server_info

    @webhook
    def echo(self, incoming_request):
        """
        A simple test webhook
        """
        self.log.debug("Your incoming request is :" + str(incoming_request))
        return str(incoming_request)
