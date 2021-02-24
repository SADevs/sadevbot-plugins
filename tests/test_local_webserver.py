import json
import logging
import os
import socket
from time import sleep

import pytest
import requests

extra_plugin_dir = "."

log = logging.getLogger(__name__)

PYTHONOBJECT = ["foo", {"bar": ("baz", None, 1.0, 2)}]
JSONOBJECT = json.dumps(PYTHONOBJECT)
# Webserver port is picked based on the process ID so that when tests
# are run in parallel with pytest-xdist, each process runs the server
# on a different port
WEBSERVER_PORT = int(os.environ.get("WEBSERVER_HTTP_PORT", 3142)) + int(
    os.environ.get("PYTEST_XDIST_WORKER", "gw0").replace("gw", "")
)


@pytest.fixture(scope="session", autouse=True)
def setup_webserverport_env_var(worker_id):
    os.environ["WEBSERVER_HTTP_PORT"] = str(WEBSERVER_PORT)


def webserver_ready(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((host, port))
        s.shutdown(socket.SHUT_RDWR)
        s.close()
        return True
    except Exception:
        return False


def wait_for_server(port: int):
    failure_count = 10
    while not webserver_ready("localhost", port):
        waiting_time = 1.0 / failure_count
        log.info("Webserver not ready yet, sleeping for %f second.", waiting_time)
        sleep(waiting_time)
        failure_count -= 1
        if failure_count == 0:
            raise TimeoutError("Could not start the internal Webserver to test.")


@pytest.fixture
def webhook_testbot(request, testbot):
    wait_for_server(WEBSERVER_PORT)
    return testbot


def test_not_configured_url_returns_404(webhook_testbot):
    assert (
        requests.post(
            "http://localhost:{}/randomness_blah".format(WEBSERVER_PORT),
            "{'toto': 'titui'}",
        ).status_code
        == 404
    )


def test_webserver_plugin_ok(webhook_testbot):
    assert "/echo" in webhook_testbot.exec_command("!webstatus")


def test_trailing_no_slash_ok(webhook_testbot):
    assert requests.post(
        "http://localhost:{}/echo".format(WEBSERVER_PORT), JSONOBJECT
    ).text == repr(json.loads(JSONOBJECT))


def test_trailing_slash_also_ok(webhook_testbot):
    assert requests.post(
        "http://localhost:{}/echo/".format(WEBSERVER_PORT), JSONOBJECT
    ).text == repr(json.loads(JSONOBJECT))


def test_webstatus_command(webhook_testbot):
    webhook_testbot.push_message("!webstatus")
    message = webhook_testbot.pop_message()
    assert f"Web server is running on port {WEBSERVER_PORT}." in message
    assert "Configured Rules:\n" in message
    assert "/echo" in message
