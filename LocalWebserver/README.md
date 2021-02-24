# LocalWebserver
This plugin is a fork of Errbot's [webserver plugin](https://github.com/errbotio/errbot/blob/master/errbot/core_plugins/webserver.py). 

It was decided to fork and modify the upstream webserver plugin for SaDevbot because of how the upstream configures. For
a core plugin like the webserver, I want the config to be done outside of bot interactions itself - as env vars. I also
removed any SSL from the webserver - ssl termination will be handled elsewhere.

# Restrictions
On Sadevbot, webhooks will not be accessible outside of the bot's container by default. Further config will be required 
to setup ingress and internet accessibility. 

# Config
* WEBSERVER_HTTP_HOST: Str, What host to setup the webserver on. Default 127.0.0.1
* WEBSERVER_HTTP_PORT: Int, What port to setup the webserver on. Default 3142
