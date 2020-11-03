import os
from datetime import datetime
from hashlib import sha512
from threading import RLock
from typing import Any
from typing import Dict
from typing import List

from decouple import config as get_config
from errbot import arg_botcmd
from errbot import botcmd
from errbot import BotPlugin
from errbot.templating import tenv
from wrapt import synchronized

DONOR_LOCK = RLock()
RECORDED_LOCK = RLock()
CONFIRMATION_LOCK = RLock()


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


class DonationManager(BotPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.website_plugin = None

    def configure(self, configuration: Dict) -> None:
        """
        Configures the plugin
        """
        self.log.debug("Starting Config")
        if configuration is None:
            configuration = dict()

        get_config_item("DONATION_MANAGER_CHANNEL", configuration)
        configuration["DM_CHANNEL_ID"] = self._bot.channelname_to_channelid(
            configuration["DONATION_MANAGER_CHANNEL"]
        )
        configuration["DM_CHANNEL_IDENTIFIER"] = self.build_identifier(
            configuration["DONATION_MANAGER_CHANNEL"]
        )
        get_config_item("DONATION_MANAGER_REPORT_CHANNEL", configuration)
        configuration["DM_REPORT_CHANNEL_ID"] = self._bot.channelname_to_channelid(
            configuration["DONATION_MANAGER_REPORT_CHANNEL"]
        )
        configuration["DM_REPORT_CHANNEL_IDENTIFIER"] = self.build_identifier(
            configuration["DONATION_MANAGER_REPORT_CHANNEL"]
        )
        get_config_item(
            "DM_RECORD_POLLER_INTERVAL", configuration, cast=int, default=3600
        )
        super().configure(configuration)

    def activate(self):
        super().activate()
        with synchronized(CONFIRMATION_LOCK):
            try:
                self["to_be_confirmed"]
            except KeyError:
                self["to_be_confirmed"] = dict()
        with synchronized(RECORDED_LOCK):
            try:
                self["to_be_recorded"]
            except KeyError:
                self["to_be_recorded"] = dict()
        with synchronized(DONOR_LOCK):
            try:
                self["donations"]
            except KeyError:
                self["donations"] = dict()
        self["donation_total"] = self._total_donations()
        self.website_plugin = self.get_plugin("SADevsWebsite")
        self.start_poller(
            self.config["DM_RECORD_POLLER_INTERVAL"], self._record_donations
        )

    def deactivate(self):
        super().deactivate()

    @arg_botcmd("amount", type=str)
    @arg_botcmd("--make-public", action="store_true", default=False)
    def donation(self, msg, amount: str, make_public: bool) -> str:
        """
        Record a donation for SA Devs Season of Giving
        """

        if "files" not in msg.extras["slack_event"]:
            return (
                "Error: No Receipt.\nPlease attach a PDF receipt of your donation when reporting it using Slack's "
                "file attachment to your `./donation` message"
            )

        if "$" not in amount:
            return (
                "Error: Please include your amount as a $##.##. i.e. $20.99. You can also use whole numbers like "
                "$20"
            )

        amount_float = float(amount.replace("$", ""))
        if amount_float <= 0:
            return "Error: Donation amount has to be a positive number."

        file_url = msg.extras["slack_event"]["files"][0]["url_private"]
        donation_id = sha512(
            f"{msg.frm}-{amount}-{file_url}".encode("utf-8")
        ).hexdigest()[-8:]

        try:
            self._add_donation_for_confirmation(
                donation_id, amount_float, file_url, msg.frm, make_public
            )
        except Exception as err:
            return f"Error: {err}"

        return_msg = f"Your donation of ${amount_float:.2f} has been reported and is being reviewed."
        if not make_public:
            return_msg = (
                f"{return_msg}\nSince you have elected for your donation to be private, we won't publicize "
                f"your name."
            )
        return_msg = f"{return_msg}\nThank you so very much for your generosity!"
        return return_msg

    @botcmd(admin_only=True)
    @arg_botcmd("user", type=str)
    @arg_botcmd("amount", type=str)
    @arg_botcmd("--make-public", action="store_true", default=False)
    def admin_donation(self, msg, amount: str, user: str, make_public: bool) -> str:
        """
        As an admin, record a donation for a user that's having issues
        """
        if "files" not in msg.extras["slack_event"]:
            file_url = ""
        else:
            file_url = msg.extras["slack_event"]["files"][0]["url_private"]

        if "$" not in amount:
            return (
                "Error: Please include your amount as a $##.##. i.e. $20.99. You can also use whole numbers like "
                "$20"
            )

        amount_float = float(amount.replace("$", ""))
        if amount_float <= 0:
            return "Error: Donation amount has to be a positive number."

        donation_id = sha512(f"{user}-{amount}-{file_url}".encode("utf-8")).hexdigest()[
            -8:
        ]
        user = self.build_identifier(user)
        try:
            self._add_donation_for_confirmation(
                donation_id, amount_float, file_url, user, make_public
            )
        except Exception as err:
            return f"Error: {err}"

        return_msg = f"The donation of ${amount_float:.2f} has been reported and is ready for review."
        if not make_public:
            return_msg = (
                f"{return_msg}\nSince you have elected for this donation to be private, we won't publicize "
                f"the username."
            )
        return return_msg

    @botcmd(admin_only=True)
    @arg_botcmd("donation_id", type=str)
    def donation_confirm(self, msg, donation_id: str) -> str:
        """
        As an admin, confirm a donation
        """
        with synchronized(CONFIRMATION_LOCK):
            to_be_confirmed = self["to_be_confirmed"]
            donation = to_be_confirmed.pop(donation_id, None)
            if donation is None:
                return f"Error: {donation_id} is not in our donation database."
            self["to_be_confirmed"] = to_be_confirmed

        with synchronized(RECORDED_LOCK):
            to_be_recorded = self["to_be_recorded"]
            to_be_recorded[donation_id] = donation
            self["to_be_recorded"] = to_be_recorded

        return f"Donation {donation_id} confirmed. Be on the look out for a PR updating the website"

    @botcmd(admin_only=True)
    @arg_botcmd("amount", type=str)
    @arg_botcmd("donation_id", type=str)
    def donation_change(self, msg, donation_id: str, amount: str) -> str:
        """
        As an admin, change a donation amount
        """
        if "$" not in amount:
            return (
                "Error: Please include your amount as a $##.##. i.e. $20.99. You can also use whole numbers like "
                "$20"
            )

        amount_float = float(amount.replace("$", ""))
        if amount_float <= 0:
            return "Error: Donation amount has to be a positive number."

        with synchronized(CONFIRMATION_LOCK):
            to_be_confirmed = self["to_be_confirmed"]
            donation = to_be_confirmed.pop(donation_id, None)
            if donation is None:
                return f"Error: {donation_id} is not in our donation database."
            self["to_be_confirmed"] = to_be_confirmed

        self._add_donation_for_confirmation(
            donation_id=donation_id,
            amount=amount_float,
            file_url=donation["file_url"],
            user=donation["user"],
            make_public=donation["user"] is not None,
        )
        return (
            f"Donation {donation_id} has been updated. You can now confirm it with "
            f"`./donation confirm {donation_id}`"
        )

    @botcmd(admin_only=True)
    @arg_botcmd("donation_id", type=str)
    def donation_delete(self, msg, donation_id: str) -> str:
        """
        As an admin, delete a donation either because its spam or needs to be re-submitted
        """
        with synchronized(CONFIRMATION_LOCK):
            try:
                to_be_confirmed = self["to_be_confirmed"]
                to_be_confirmed.pop(donation_id)
                self["to_be_confirmed"] = to_be_confirmed
                return f"Removed pending donation {donation_id}"
            except KeyError:
                pass

        with synchronized(RECORDED_LOCK):
            try:
                to_be_recorded = self["to_be_recorded"]
                to_be_recorded.pop(donation_id)
                self["to_be_recorded"] = to_be_recorded
                return f"Removed recorded donation {donation_id}"
            except KeyError:
                pass

        with synchronized(DONOR_LOCK):
            try:
                donations = self["donations"]
                donations.pop(donation_id)
                self["donations"] = donations
                return (
                    f"Removed pr'd donation {donation_id}. This won't remove the donation from the page until a pr"
                    f"is redone with new donations list. You can do this with ./rebuild donations list"
                )
            except KeyError:
                return f"Donation {donation_id} is not found."
        return f"Donation {donation_id} is not in our donations lists"

    @botcmd(admin_only=True)
    def list_donations(self, msg, _) -> str:
        """Lists all the donations we have"""

        yield "*Donations still needing confirmation*:"
        with synchronized(CONFIRMATION_LOCK):
            for id, donation in self["to_be_confirmed"].items():
                yield f"{id}: {donation['user']} - {donation['amount']} - {donation['file_url']}"

        yield "*Donations waiting to be recorded:*"
        with synchronized(RECORDED_LOCK):
            yield "\n".join(
                [
                    f"{id}: {donation['user']} - {donation['amount']}"
                    for id, donation in self["to_be_recorded"].items()
                ]
            )

        yield "*Confirmed Donations*:"
        with synchronized(DONOR_LOCK):
            yield "\n".join(
                [
                    f"{id}: {donation['user']} - {donation['amount']}"
                    for id, donation in self["donations"].items()
                ]
            )

    @botcmd(admin_only=True)
    def rebuild_donations_list(self, msg, *_, **__) -> str:
        """
        Rebuilds the websites donations list with the current data
        """
        self._record_donations(force=True)

    def _update_blog_post(
        self, clone_path: str, donations: Dict, donation_total: float
    ) -> List[str]:
        """
        Updates the blog post from our template using the donations dict and total
        """
        blog_post = (
            tenv()
            .get_template("blog-post.md")
            .render(total=donation_total, donations=donations)
        )
        article_path = os.path.join(
            clone_path, "content/articles/SADevs-season-of-giving-2020.md"
        )
        with open(article_path, "w") as file:
            file.write(blog_post)

        return [article_path]

    def _add_donation_for_confirmation(
        self,
        donation_id: str,
        amount: float,
        file_url: str,
        user: str,
        make_public: bool,
    ) -> None:
        """
        Adds a donation to be confirmed
        """
        if not make_public:
            user = None
        else:
            if type(user) != str:
                user = self._get_user_real_name(user)

        with synchronized(CONFIRMATION_LOCK):
            try:
                to_be_confirmed = self["to_be_confirmed"]
            except KeyError:
                to_be_confirmed = dict()
            if donation_id in to_be_confirmed:
                raise KeyError(
                    "Donation is not unique. Did you already add this donation? If this is in error, "
                    "reach out to the admins"
                )

            to_be_confirmed[donation_id] = {
                "amount": amount,
                "file_url": file_url,
                "user": user,
            }
            self["to_be_confirmed"] = to_be_confirmed

        self.send(
            self.config["DM_CHANNEL_IDENTIFIER"],
            text=f"New donation:\n"
            f"Amount: ${amount:.2f}\n"
            f"File URL: {file_url}\n"
            f"User: {user}\n\n"
            f"To approve this donation run `./donation confirm {donation_id}`\n"
            f"To change this donation run `./donation change {donation_id} [new amount]`",
        )

    def _get_user_real_name(self, user) -> str:
        return self._bot.api_call("users.info", {"user": user.userid})["user"][
            "profile"
        ]["real_name"]

    @synchronized(DONOR_LOCK)
    def _total_donations(self):
        """Totals donation amounts into self['donation_total']"""
        total = 0
        for _, donation in self["donations"].items():
            total += donation["amount"]
        return total

    @synchronized(RECORDED_LOCK)
    def _record_donations(self, force: bool = False) -> None:
        """
        Poller that records a donation and turns it into a PR
        """
        if len(self["to_be_recorded"].keys()) == 0 and not force:
            return
        timestamp = int(datetime.now().timestamp())

        with synchronized(RECORDED_LOCK):
            to_be_recorded = self["to_be_recorded"]
            self["to_be_recorded"] = dict()

        with synchronized(DONOR_LOCK):
            new_donations = {**self["donations"], **to_be_recorded}
            self["donations"] = new_donations

        self["donation_total"] = self._total_donations()
        branch_name = f"new-donations-{timestamp}"
        with self.website_plugin.temp_website_clone(
            checkout_branch=branch_name
        ) as website_clone:
            file_list = self._update_blog_post(
                website_clone, new_donations, self["donation_total"]
            )
            pr = self.website_plugin.open_website_pr(
                website_clone,
                file_list,
                f"updating with new donations {timestamp}",
                f"Donation Manager: New Donations {timestamp}",
                "New donations",
            )

        self.send(
            self.config["DM_CHANNEL_IDENTIFIER"], text=f"New donation PR:\n" f"{pr}"
        )

        self.log.debug(self.config["DM_REPORT_CHANNEL_ID"])
        self._bot.api_call(
            "conversations.setTopic",
            {
                "channel": self.config["DM_REPORT_CHANNEL_ID"],
                "topic": f"Total Donations in SA Dev's Season of Giving: ${self['donation_total']:.2f}",
            },
        )
