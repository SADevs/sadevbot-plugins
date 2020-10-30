import json
import os
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from typing import Any
from typing import Dict
from typing import List

import delegator
from decouple import config as get_config
from errbot import BotPlugin


class GitError(Exception):
    pass


class GithubError(Exception):
    pass


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


class SADevsWebsite(BotPlugin):
    class GitException(Exception):
        pass

    def configure(self, configuration: Dict) -> None:
        """
        Configures the plugin
        """
        self.log.debug("Starting Config")
        if configuration is None:
            configuration = dict()

        # name of the channel to post in
        get_config_item(
            "WEBSITE_GIT_URL",
            configuration,
            default="git@github.com:SADevs/sadevs.github.io.git",
        )
        get_config_item("WEBSITE_GIT_BASE_BRANCH", configuration, default="website")
        get_config_item("GITHUB_TOKEN", configuration)
        super().configure(configuration)

    def activate(self):
        super().activate()

    def deactivate(self):
        super().deactivate()

    @contextmanager
    def temp_website_clone(self, checkout_branch: str = None) -> str:
        """
        Contextmanager that offers a temporary clone of the websites gitrepo that can be used to make changes to the
        site
        """
        with TemporaryDirectory() as directory:
            web_repo_dir = os.path.join(directory, "sadevs-website")
            clone_result = self._run_git_cmd(
                directory, f"clone {self.config['WEBSITE_GIT_URL']} sadevs-website"
            )
            self.log.debug(clone_result)
            if checkout_branch is not None:
                checkout_result = self._run_git_cmd(
                    web_repo_dir, f"checkout -b {checkout_branch}"
                )
                self.log.debug(checkout_result)
            yield web_repo_dir

    def open_website_pr(
        self,
        website_repo_path: str,
        files_changed: List[str],
        commit_msg: str,
        pr_title: str,
        pr_body: str,
    ) -> str:
        """Opens a PR to the website for the changed files. Returns the PR url"""
        file_list_str = " ".join(files_changed)
        commit_result = self._run_git_cmd(
            website_repo_path, f"commit {file_list_str} -m '{commit_msg}'"
        )
        self.log.debug(commit_result)
        push = self._run_git_cmd(website_repo_path, "push origin HEAD")
        self.log.debug(push)
        pr_url = self._run_gh_cli_cmd(
            website_repo_path, f'pr create --title "{pr_title}" --body "{pr_body}"'
        )
        return pr_url

    def _run_cmd(
        self,
        cmd: str,
        cwd: str,
        timeout: int,
        exception_type: Exception,
        env: Dict = None,
    ) -> str:
        """Runs a command using delegator and returns stdout. Rasies an exception of exception_type if rc != 0"""
        if env is None:
            env = dict()
        env = {**env, **os.environ.copy()}
        command = delegator.run(cmd, block=True, cwd=cwd, timeout=timeout, env=env)

        self.log.debug("CMD %s run as PID %s", cmd, command.pid)
        if not command.ok:
            try:
                output = command.out()
            except TypeError:
                output = ""
            raise exception_type(json.dumps({"stdout": output, "stderr": command.err}))
        return command.out

    def _run_git_cmd(self, repo_path: str, cmd: str) -> str:
        git_result = self._run_cmd(f"git {cmd}", repo_path, 120, GitError)
        return git_result

    def _get_gh_user(self) -> str:
        """Auths to the GH api with the PAT. Used to get the current username + validate our GH token works"""
        api_response = self._run_gh_cli_cmd("/tmp", "api user")
        return json.loads(api_response)["login"]

    def _run_gh_cli_cmd(self, repo_path: str, cmd: str) -> str:
        gh_result = self._run_cmd(f"gh {cmd}", repo_path, 120, GithubError)
        return gh_result
