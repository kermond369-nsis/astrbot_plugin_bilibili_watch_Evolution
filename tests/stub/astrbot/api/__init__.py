"""Minimal `astrbot.api` stub used only by offline tests.

Deliberately does not import Python's logging module: release code and test
fixtures both keep the AstrBot logging entry point as the only logger source.
"""


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


logger = _Logger()
