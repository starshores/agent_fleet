"""Stable public failures shared by storage and coordination services."""


class PlatformError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
