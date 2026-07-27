"""Provider plugin for mycompany/hello."""

from mycompany.plugins.providers.hello.provider import HelloRuntime
from mycompany.plugins.providers.hello.provider import provider_plugin

__all__ = ["HelloRuntime", "provider_plugin"]
